"""SOAP 1.2 over HTTP framework + per-camera HTTP server.

Core 5.1.1: device service entry point is fixed to /onvif/device_service.
Core 5.8.2: SOAP 1.2 faults with ter: subcodes; Sender faults -> HTTP 400, Receiver -> 500.
"""

import os
import json
import sys
import logging
import socketserver
import threading
import time
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from . import auth as A
from .util import localname, nsof, child, esc

log = logging.getLogger("vcam.soap")

SOAP12 = "http://www.w3.org/2003/05/soap-envelope"
SOAP11 = "http://schemas.xmlsoap.org/soap/envelope/"

NS = {
    "tt": "http://www.onvif.org/ver10/schema",
    "tds": "http://www.onvif.org/ver10/device/wsdl",
    "trt": "http://www.onvif.org/ver10/media/wsdl",
    "tr2": "http://www.onvif.org/ver20/media/wsdl",
    "tev": "http://www.onvif.org/ver10/events/wsdl",
    "ter": "http://www.onvif.org/ver10/error",
    "wsnt": "http://docs.oasis-open.org/wsn/b-2",
    "wsrf-bf": "http://docs.oasis-open.org/wsrf/bf-2",
    "wstop": "http://docs.oasis-open.org/wsn/t-1",
    "wsa5": "http://www.w3.org/2005/08/addressing",
    "tns1": "http://www.onvif.org/ver10/topics",
    "xs": "http://www.w3.org/2001/XMLSchema",
    "xsi": "http://www.w3.org/2001/XMLSchema-instance",
    "tdn": "http://www.onvif.org/ver10/network/wsdl",
}

SERVICE_BY_NS = {
    NS["tds"]: "device", NS["trt"]: "media", NS["tr2"]: "media2", NS["tev"]: "events",
    NS["wsnt"]: "events",
}

_NSDECL = " ".join(f'xmlns:{k}="{v}"' for k, v in NS.items())


def envelope(body: str, soap_ns=SOAP12, action=None, relates_to=None) -> bytes:
    hdr = ""
    if action or relates_to:
        hdr = "<s:Header>"
        if action:
            hdr += f'<wsa5:Action s:mustUnderstand="1">{esc(action)}</wsa5:Action>'
        if relates_to:
            hdr += f"<wsa5:RelatesTo>{esc(relates_to)}</wsa5:RelatesTo>"
        hdr += "</s:Header>"
    return (f'<?xml version="1.0" encoding="UTF-8"?>\n<s:Envelope xmlns:s="{soap_ns}" {_NSDECL}>'
            f"{hdr}<s:Body>{body}</s:Body></s:Envelope>").encode("utf-8")


class SoapFault(Exception):
    def __init__(self, code="Receiver", subcode="ter:Action", subsub=None, reason="Action failed",
                 detail=None):
        super().__init__(reason)
        self.code, self.subcode, self.subsub, self.reason, self.detail = \
            code, subcode, subsub, reason, detail

    @property
    def http_status(self):
        return 400 if self.code == "Sender" else 500

    def body(self, soap_ns=SOAP12):
        if soap_ns == SOAP11:
            return (f"<s:Fault><faultcode>s:{'Client' if self.code == 'Sender' else 'Server'}"
                    f"</faultcode><faultstring>{esc(self.reason)}</faultstring></s:Fault>")
        sub = ""
        if self.subcode:
            inner = f"<s:Subcode><s:Value>{self.subsub}</s:Value></s:Subcode>" if self.subsub else ""
            sub = f"<s:Subcode><s:Value>{self.subcode}</s:Value>{inner}</s:Subcode>"
        det = f"<s:Detail><s:Text>{esc(self.detail)}</s:Text></s:Detail>" if self.detail else ""
        return (f"<s:Fault><s:Code><s:Value>s:{self.code}</s:Value>{sub}</s:Code>"
                f'<s:Reason><s:Text xml:lang="en">{esc(self.reason)}</s:Text></s:Reason>{det}'
                "</s:Fault>")


def invalid_arg(subsub, reason):
    return SoapFault("Sender", "ter:InvalidArgVal", subsub, reason)


def not_supported(subsub=None, reason="Optional Action Not Implemented"):
    return SoapFault("Receiver", "ter:ActionNotSupported", subsub, reason)


class Ctx:
    """Per-request context handed to service handlers."""

    def __init__(self, app, camera, handler, host_ip, soap_ns, header, path):
        self.app = app
        self.camera = camera
        self.handler = handler
        self.host_ip = host_ip
        self.soap_ns = soap_ns
        self.header = header
        self.path = path
        self.user = None
        self.level = "Anonymous"
        self.action = None  # response wsa:Action
        self.relates_to = None

    def xaddr(self, service):
        return f"http://{self.host_ip}:{self.camera.http_port}/onvif/{service}"

    @property
    def rtsp_port(self):
        return self.app.rtsp_port


class OnvifHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = os.name != "nt"  # on Windows it would let two servers share a port
    request_queue_size = 128

    def __init__(self, addr, app, camera):
        self.app = app
        self.camera = camera
        super().__init__(addr, OnvifHandler)

    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionError, TimeoutError)):  # client went away mid-reply
            log.info("%s %s closed the connection early (%s)", self.camera.id, client_address[0],
                     type(exc).__name__)
        else:
            log.exception("%s HTTP error from %s", self.camera.id, client_address[0])


class OnvifHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "ONVIF-VCam/1.0"
    timeout = 120

    def log_message(self, fmt, *args):  # route to logging
        log.debug("%s %s - " + fmt, self.server.camera.id, self.client_address[0], *args)

    # ------------------------------------------------------------------ helpers
    @property
    def app(self):
        return self.server.app

    @property
    def camera(self):
        return self.server.camera

    @property
    def host_ip(self):
        adv = self.app.advertise_host
        return adv or self.connection.getsockname()[0]

    def _send(self, status, body=b"", ctype="application/soap+xml; charset=utf-8", headers=None):
        self.send_response(status)
        if body is not None:
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or []):
            self.send_header(k, v)
        self.end_headers()
        if body and self.command != "HEAD":
            self.wfile.write(body)

    def _is_tunnel(self):
        # RTSP-over-HTTP (Apple tunnelling) is identified by its x-sessioncookie header
        return bool(self.headers.get("x-sessioncookie"))

    def _http_auth(self, required_class="READ_MEDIA"):
        """Digest/Basic for plain HTTP resources (snapshot, api)."""
        authn = self.camera.authenticator
        if not authn.enabled:
            return True
        try:
            res = authn.check_authorization(self.headers.get("Authorization"), self.command)
        except A.AuthError as exc:
            res = None
            stale = exc.kind == "stale"
        else:
            stale = False
        if res and authn.allowed(res[1], required_class):
            return True
        hdrs = [("WWW-Authenticate", c) for c in authn.challenges(stale)]
        if authn.allow_basic:
            hdrs.append(("WWW-Authenticate", f'Basic realm="{authn.realm}"'))
        self._send(401, b"Unauthorized", "text/plain", hdrs)
        return False

    # ------------------------------------------------------------------ verbs
    def do_GET(self):
        if self._is_tunnel():
            return self.app.rtsp.http_tunnel_get(self)
        url = urlparse(self.path)
        p = url.path.rstrip("/")
        if p.startswith("/onvif/snapshot/"):
            return self._snapshot(p)
        if p in ("", "/index.html"):
            body = self.app.status_html(self.host_ip).encode()
            return self._send(200, body, "text/html; charset=utf-8")
        if p == "/api/status":
            return self._send(200, json.dumps(self.app.status_json(self.host_ip), indent=2).encode(),
                              "application/json")
        if p.startswith("/onvif/"):
            # WSDL fetch / browser probe: SOAP endpoints accept POST only
            return self._send(405, b"Use SOAP POST", "text/plain", [("Allow", "POST")])
        self._send(404, b"Not found", "text/plain")

    do_HEAD = do_GET

    def do_POST(self):
        if self._is_tunnel():
            return self.app.rtsp.http_tunnel_post(self)
        url = urlparse(self.path)
        if url.path.startswith("/api/"):
            return self._api(url)
        length = int(self.headers.get("Content-Length") or 0)
        if self.headers.get("Transfer-Encoding", "").lower() == "chunked":
            raw = self._read_chunked()
        else:
            raw = self.rfile.read(length) if length else b""
        self._soap(raw, url.path)

    def _read_chunked(self):
        data = b""
        while True:
            line = self.rfile.readline().strip()
            size = int(line.split(b";")[0], 16) if line else 0
            if size == 0:
                self.rfile.readline()
                return data
            data += self.rfile.read(size)
            self.rfile.readline()

    # ------------------------------------------------------------------ SOAP
    def _soap(self, raw, path):
        t0 = time.time()
        try:
            root = ET.fromstring(raw)
        except ET.ParseError:
            f = SoapFault("Sender", "ter:WellFormed", None, "Well-formed Error")
            return self._send(400, envelope(f.body()))
        soap_ns = nsof(root.tag) if nsof(root.tag) in (SOAP11, SOAP12) else SOAP12
        header = child(root, "Header")
        body = child(root, "Body")
        req = next(iter(body), None) if body is not None else None
        if req is None:
            f = SoapFault("Sender", "ter:Tag", None, "No Tag")
            return self._send(400, envelope(f.body(soap_ns), soap_ns))
        op = localname(req.tag)
        service = SERVICE_BY_NS.get(nsof(req.tag))
        if service is None and "/events/" in path:
            service = "events"
        ctx = Ctx(self.app, self.camera, self, self.host_ip, soap_ns, header, path)
        ctype = "application/soap+xml; charset=utf-8" if soap_ns == SOAP12 else \
            "text/xml; charset=utf-8"
        try:
            self._authenticate(ctx, service or "device", op)
            handler = self.app.services.get(service)
            fn = getattr(handler, op, None) if handler else None
            if fn is None or op.startswith("_"):
                raise not_supported(None, f"{op} not implemented")
            out = fn(ctx, req)
            status, payload = 200, envelope(out, soap_ns, ctx.action, ctx.relates_to)
        except A.AuthError as exc:
            if exc.kind in ("challenge", "stale"):
                hdrs = [("WWW-Authenticate", c) for c in
                        self.camera.authenticator.challenges(exc.kind == "stale")]
                log.info("%s %s %s -> 401 (%s)", self.camera.id, self.client_address[0], op, exc)
                return self._send(401, envelope(SoapFault(
                    "Sender", "ter:NotAuthorized", None, "Sender not Authorized").body(soap_ns),
                    soap_ns), ctype, hdrs)
            f = SoapFault("Sender", "ter:NotAuthorized", None, "Sender not Authorized", str(exc))
            log.warning("%s %s %s -> NotAuthorized (%s)", self.camera.id, self.client_address[0], op,
                     exc)
            return self._send(400, envelope(f.body(soap_ns), soap_ns), ctype)
        except SoapFault as f:
            log.warning("%s %s %s -> fault %s/%s %s", self.camera.id, self.client_address[0], op,
                     f.subcode, f.subsub, f.reason)
            status, payload = f.http_status, envelope(f.body(soap_ns), soap_ns)
        except Exception as exc:  # never kill the connection on handler bugs
            log.exception("%s %s handler crashed", self.camera.id, op)
            f = SoapFault("Receiver", "ter:Action", None, "Action Failed", str(exc))
            status, payload = 500, envelope(f.body(soap_ns), soap_ns)
        log.debug("%s %s %s/%s %s %.1fms", self.camera.id, self.client_address[0], service, op,
                  status, (time.time() - t0) * 1000)
        self.app.counters[op] = self.app.counters.get(op, 0) + 1
        self._send(status, payload, ctype)

    def _authenticate(self, ctx, service, op):
        authn = self.camera.authenticator
        if not authn.enabled:
            ctx.user, ctx.level = "anonymous", "Administrator"
            return
        klass = A.access_class(service, op)
        http_user = None
        hdr = self.headers.get("Authorization")
        if hdr:
            try:
                http_user = authn.check_authorization(hdr, "POST")
            except A.AuthError as exc:
                if klass == A.PRE_AUTH:
                    http_user = None
                else:
                    raise exc
        try:
            ws_user = authn.check_wsse(ctx.header)
        except A.AuthError:
            if klass == A.PRE_AUTH:
                ws_user = None
            else:
                raise
        who = ws_user or http_user
        if who:
            ctx.user, ctx.level = who
        if klass == A.PRE_AUTH:
            return
        if not who:
            raise A.AuthError("challenge", "no credentials")
        if not authn.allowed(ctx.level, klass):
            raise SoapFault("Sender", "ter:NotAuthorized", None, "Sender not Authorized",
                            f"user level {ctx.level} cannot perform {klass}")

    # ------------------------------------------------------------------ snapshot
    def _snapshot(self, p):
        if self.app.snapshot_auth and not self._http_auth("READ_MEDIA"):
            return
        parts = p.split("/")  # /onvif/snapshot/<profile>.jpg
        token = parts[-1].rsplit(".", 1)[0] if parts else ""
        cam = self.camera
        with cam.lock:
            prof = cam.profiles.get(token)
            vec = cam.vecs.get(prof.vec) if prof and prof.vec else None
        try:
            from .source import snapshot
            jpg = snapshot(cam, vec, self.app.sources.epoch)
        except Exception as exc:
            log.warning("snapshot failed: %s", exc)
            return self._send(503, b"snapshot unavailable", "text/plain")
        self._send(200, jpg, "image/jpeg", [("Cache-Control", "no-cache")])

    # ------------------------------------------------------------------ test API
    def _api(self, url):
        """POST /api/motion?state=1  -> fire a motion event (for VMS rule testing)."""
        if not self._http_auth("ACTUATE"):
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        q = {k: v[-1] for k, v in parse_qs(url.query).items()}
        if url.path.rstrip("/") == "/api/motion":
            state = q.get("state", "1").lower() in ("1", "true", "on")
            self.app.events.set_motion(self.camera, state, manual=True)
            return self._send(200, json.dumps({"camera": self.camera.id, "motion": state}).encode(),
                              "application/json")
        self._send(404, b"unknown api", "text/plain")


def serve(app, camera, bind="0.0.0.0"):
    """Bind the camera's HTTP port; if it is busy or Windows-reserved (WinError 10013/10048)
    move to the next free one and pin it in the camera config so the address stays stable."""
    used = {c.http_port for c in app.cameras if c is not camera}
    used |= {app.rtsp_port, int(app.cfg.get("ui_port") or 0)}
    wanted, port = camera.http_port, camera.http_port
    while True:
        try:
            srv = OnvifHTTPServer((bind, port), app, camera)
            break
        except OSError as exc:
            log.warning("%s: HTTP port %d unavailable: %s", camera.id, port, exc)
            port += 1
            while port in used:
                port += 1
            if port > wanted + 200:
                raise
    if port != wanted:
        log.warning("%s: HTTP port %d unavailable, using %d instead", camera.id, wanted, port)
        camera.http_port = camera.protocols["HTTP"] = camera.cfg["http_port"] = port
    t = threading.Thread(target=srv.serve_forever, name=f"http-{camera.id}", daemon=True)
    t.start()
    return srv


__all__ = ["NS", "envelope", "SoapFault", "invalid_arg", "not_supported", "serve", "Ctx",
           "socketserver"]
