"""WS-Discovery target service - ONVIF Core section 7 / WS-Discovery 2005/04.

* Hello on start (and after scope changes / reboot), Bye on shutdown (7.2, 7.3.5).
* Probe -> ProbeMatches with Types 'dn:NetworkVideoTransmitter tds:Device' (Profile S 9.2,
  Core 7.3.2.1), scopes and XAddrs (7.3.2.3). rfc3986 scope matching (7.3.3) and strcmp0.
* Resolve -> ResolveMatches (7.3.4).
* Endpoint reference is a stable urn:uuid (7.3.1).
"""

import logging
import random
import socket
import struct
import threading
import time
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit

from .util import esc, localname, child, text, new_uuid, list_ipv4, route_ip

log = logging.getLogger("vcam.discovery")

MCAST_GRP = "239.255.255.250"
MCAST_PORT = 3702
NS_D = "http://schemas.xmlsoap.org/ws/2005/04/discovery"
NS_WSA = "http://schemas.xmlsoap.org/ws/2004/08/addressing"
TO_DISCOVERY = "urn:schemas-xmlsoap-org:ws:2005:04:discovery"
ANON = "http://schemas.xmlsoap.org/ws/2004/08/addressing/role/anonymous"
RULE_RFC3986 = "http://schemas.xmlsoap.org/ws/2005/04/discovery/rfc3986"
RULE_STRCMP = "http://schemas.xmlsoap.org/ws/2005/04/discovery/strcmp0"
APP_MAX_DELAY = 0.5
DEVICE_TYPES = {"NetworkVideoTransmitter", "Device"}


def scope_match(probe_scope, device_scope, rule):
    if rule == RULE_STRCMP:
        return probe_scope == device_scope
    p, d = urlsplit(probe_scope), urlsplit(device_scope)
    if p.scheme.lower() != d.scheme.lower() or p.netloc.lower() != d.netloc.lower():
        return False
    ps = [s for s in p.path.split("/") if s]
    ds = [s for s in d.path.split("/") if s]
    return len(ps) <= len(ds) and ds[:len(ps)] == ps


class Discovery:
    def __init__(self, app):
        self.app = app
        self.instance_id = int(time.time())
        self.msg_no = 0
        self.lock = threading.Lock()
        self.sock = None
        self.stop_evt = threading.Event()
        self.metadata_version = 1

    # ------------------------------------------------------------------ socket
    def start(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_REUSEPORT"):
            try:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except OSError:
                pass
        s.bind(("", MCAST_PORT))
        joined = 0
        for ip in self.app.discovery_ips():
            try:
                mreq = socket.inet_aton(MCAST_GRP) + socket.inet_aton(ip)
                s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
                joined += 1
            except OSError as exc:
                log.debug("join %s on %s failed: %s", MCAST_GRP, ip, exc)
        if not joined:
            mreq = struct.pack("4sl", socket.inet_aton(MCAST_GRP), socket.INADDR_ANY)
            s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 4)
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
        self.sock = s
        threading.Thread(target=self._loop, name="wsdiscovery", daemon=True).start()
        log.info("WS-Discovery listening on udp/%d (%s)", MCAST_PORT,
                 ", ".join(self.app.discovery_ips()) or "any")
        for cam in self.app.cameras:
            self.hello(cam)

    def stop(self):
        for cam in self.app.cameras:
            self.bye(cam)
        self.stop_evt.set()
        try:
            self.sock.close()
        except OSError:
            pass

    # ------------------------------------------------------------------ messages
    def _next(self):
        with self.lock:
            self.msg_no += 1
            return self.msg_no

    def _envelope(self, action, body, to=TO_DISCOVERY, relates=None):
        rel = f"<wsa:RelatesTo>{esc(relates)}</wsa:RelatesTo>" if relates else ""
        return (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://www.w3.org/2003/05/soap-envelope" '
            f'xmlns:wsa="{NS_WSA}" xmlns:d="{NS_D}" '
            'xmlns:dn="http://www.onvif.org/ver10/network/wsdl" '
            'xmlns:tds="http://www.onvif.org/ver10/device/wsdl">'
            "<SOAP-ENV:Header>"
            f"<wsa:MessageID>urn:uuid:{new_uuid()}</wsa:MessageID>{rel}"
            f'<wsa:To SOAP-ENV:mustUnderstand="true">{to}</wsa:To>'
            f'<wsa:Action SOAP-ENV:mustUnderstand="true">{NS_D}/{action}</wsa:Action>'
            f'<d:AppSequence SOAP-ENV:mustUnderstand="true" MessageNumber="{self._next()}" '
            f'InstanceId="{self.instance_id}"/>'
            f"</SOAP-ENV:Header><SOAP-ENV:Body>{body}</SOAP-ENV:Body></SOAP-ENV:Envelope>"
        ).encode("utf-8")

    def _match_body(self, cam, ip):
        # Core 7.3.2.3: one URI per address. Same list in every message (only the preferred
        # address first), so a VMS never sees the device "move" between host adapters.
        ips = [ip] if self.app.advertise_host else \
            [ip] + [x for x in self.app.discovery_ips() if x != ip]
        xaddr = " ".join(f"http://{x}:{cam.http_port}/onvif/device_service" for x in ips)
        return (f"<wsa:EndpointReference><wsa:Address>urn:uuid:{cam.uuid}</wsa:Address>"
                "</wsa:EndpointReference>"
                "<d:Types>dn:NetworkVideoTransmitter tds:Device</d:Types>"
                f"<d:Scopes>{esc(' '.join(cam.all_scopes))}</d:Scopes>"
                f"<d:XAddrs>{xaddr}</d:XAddrs>"
                f"<d:MetadataVersion>{self.metadata_version}</d:MetadataVersion>")

    def _send_multicast(self, data_for_ip):
        if not self.sock:
            return
        for ip in self.app.discovery_ips():
            try:
                self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF,
                                     socket.inet_aton(ip))
                self.sock.sendto(data_for_ip(ip), (MCAST_GRP, MCAST_PORT))
            except OSError as exc:
                log.debug("multicast send via %s failed: %s", ip, exc)

    def hello(self, cam):
        if not cam.discoverable:
            return
        self.metadata_version += 1
        log.info("WS-Discovery Hello %s (urn:uuid:%s)", cam.id, cam.uuid)
        pkt = self._envelope("Hello", f"<d:Hello>{self._match_body(cam, self.app.primary_ip())}"
                                      "</d:Hello>")
        self._send_multicast(lambda ip: pkt)

    def bye(self, cam):
        body = (f"<d:Bye><wsa:EndpointReference><wsa:Address>urn:uuid:{cam.uuid}</wsa:Address>"
                "</wsa:EndpointReference></d:Bye>")
        self._send_multicast(lambda ip: self._envelope("Bye", body))

    # ------------------------------------------------------------------ receive
    def _loop(self):
        while not self.stop_evt.is_set():
            try:
                data, addr = self.sock.recvfrom(65535)
            except OSError:
                if self.stop_evt.is_set():
                    return
                continue
            try:
                self._handle(data, addr)
            except ET.ParseError:
                pass  # 7.3.6: silently discard malformed multicast packets
            except Exception:
                log.exception("discovery error from %s", addr)

    def _handle(self, data, addr):
        if b"Probe" not in data and b"Resolve" not in data:
            return
        root = ET.fromstring(data)
        header = child(root, "Header")
        body = child(root, "Body")
        if body is None or not len(body):
            return
        req = body[0]
        name = localname(req.tag)
        msg_id = text(header, "MessageID") if header is not None else None
        if name == "Probe":
            self._probe(req, msg_id, addr)
        elif name == "Resolve":
            self._resolve(req, msg_id, addr)

    def _reply_ip(self, addr):
        return self.app.advertise_host or route_ip(addr[0])

    def _probe(self, req, msg_id, addr):
        types_txt = text(req, "Types", default="") or ""
        req_types = {t.split(":")[-1] for t in types_txt.split()}
        scopes_el = child(req, "Scopes")
        scopes = (scopes_el.text or "").split() if scopes_el is not None else []
        rule = (scopes_el.get("MatchBy") if scopes_el is not None else None) or RULE_RFC3986
        if rule not in (RULE_RFC3986, RULE_STRCMP):
            return
        if req_types and not req_types <= DEVICE_TYPES:
            return
        ip = self._reply_ip(addr)
        for cam in self.app.cameras:
            if not cam.discoverable:
                continue
            if scopes and not all(any(scope_match(s, d, rule) for d in cam.all_scopes)
                                  for s in scopes):
                continue
            body = f"<d:ProbeMatches><d:ProbeMatch>{self._match_body(cam, ip)}</d:ProbeMatch>" \
                   "</d:ProbeMatches>"
            pkt = self._envelope("ProbeMatches", body, to=ANON, relates=msg_id)
            delay = random.uniform(0, APP_MAX_DELAY)
            threading.Timer(delay, self._unicast, args=(pkt, addr)).start()
            log.debug("ProbeMatch %s -> %s:%d", cam.id, *addr)

    def _resolve(self, req, msg_id, addr):
        target = text(req, "EndpointReference", "Address", default="")
        ip = self._reply_ip(addr)
        for cam in self.app.cameras:
            if cam.discoverable and target == f"urn:uuid:{cam.uuid}":
                body = f"<d:ResolveMatches><d:ResolveMatch>{self._match_body(cam, ip)}" \
                       "</d:ResolveMatch></d:ResolveMatches>"
                self._unicast(self._envelope("ResolveMatches", body, to=ANON, relates=msg_id),
                              addr)

    def _unicast(self, pkt, addr):
        try:
            self.sock.sendto(pkt, addr)
        except OSError as exc:
            log.debug("unicast reply to %s failed: %s", addr, exc)


__all__ = ["Discovery", "scope_match", "list_ipv4"]
