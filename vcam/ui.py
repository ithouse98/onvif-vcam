"""Web control panel: static page + JSON API on its own port (default 8000).

GET  /                               panel (vcam/web/index.html)
GET  /api/state                      everything the panel shows
GET  /api/logs?after=N               new log lines
GET  /api/videos                     files in the videos folder (+ probe info)
PUT  /api/videos?name=x.mp4          upload (raw body)      DELETE /api/videos?name=x.mp4
POST /api/cameras                    add  {..., "count": n}  PUT/DELETE /api/cameras/<id>
POST /api/cameras/<id>/motion?state=1
GET  /api/cameras/<id>/snapshot.jpg?profile=<token>
PUT  /api/settings                   {"advertise_ip": "", "username": "", "password": ""}
"""

import base64
import hmac
import json
import logging
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from . import __version__
from .model import probe
from .source import snapshot
from .util import list_ipv4

log = logging.getLogger("vcam.ui")
WEB = os.path.join(os.path.dirname(__file__), "web")
VIDEO_EXT = (".mp4", ".mkv", ".mov", ".avi", ".ts", ".m4v", ".h264", ".h265", ".hevc", ".mjpeg")
_probe_cache = {}


def video_info(path):
    st = os.stat(path)
    key = (path, st.st_mtime, st.st_size)
    if key not in _probe_cache:
        try:
            i = probe(path)
            _probe_cache[key] = f"{i.codec} {i.width}x{i.height} @{i.fps:g} · {i.duration:.0f}s"
        except Exception as exc:
            _probe_cache[key] = f"unreadable: {exc}"[:120]
    return _probe_cache[key]


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "ONVIF-VCam-Panel/" + __version__

    def log_message(self, fmt, *args):
        log.debug("%s " + fmt, self.client_address[0], *args)

    @property
    def app(self):
        return self.server.app

    # ------------------------------------------------------------------ plumbing
    def _send(self, code, body=b"", ctype="application/json; charset=utf-8", headers=()):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if not any(k == "Cache-Control" for k, _ in headers):
            self.send_header("Cache-Control", "no-store")
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _err(self, code, msg):
        self._send(code, {"error": msg})

    def _authorized(self):
        # same default credentials as the cameras (model.py) when none are configured
        users = [u for u in self.app.cfg.get("users") or
                 [{"username": "admin", "password": "admin"}]
                 if u.get("level", "Administrator") == "Administrator"]
        if not self.app.cfg.get("auth", True):
            return True
        hdr = self.headers.get("Authorization", "")
        if hdr.startswith("Basic "):
            try:
                user, _, pwd = base64.b64decode(hdr[6:]).decode().partition(":")
            except Exception:
                user = pwd = None
            for u in users:
                if user == u["username"] and hmac.compare_digest(pwd or "", u["password"]):
                    return True
        self._send(401, {"error": "login required"},
                   headers=[("WWW-Authenticate", 'Basic realm="ONVIF-VCam panel"')])
        return False

    def _json(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            raise ValueError("invalid JSON body")

    def _route(self):
        u = urlparse(self.path)
        return [p for p in u.path.split("/") if p], {k: v[-1] for k, v in parse_qs(u.query).items()}

    # ------------------------------------------------------------------ verbs
    def do_GET(self):
        if not self._authorized():
            return
        parts, q = self._route()
        try:
            if not parts or parts == ["index.html"]:
                with open(os.path.join(WEB, "index.html"), "rb") as fh:
                    return self._send(200, fh.read(), "text/html; charset=utf-8")
            if len(parts) == 2 and parts[0] in ("fonts", "assets"):
                f = os.path.join(WEB, parts[0], os.path.basename(parts[1]))
                if os.path.isfile(f):
                    ctype = {".woff2": "font/woff2", ".png": "image/png"}.get(
                        os.path.splitext(f)[1], "text/plain; charset=utf-8")
                    with open(f, "rb") as fh:
                        return self._send(200, fh.read(), ctype,
                                          [("Cache-Control", "max-age=86400")])
            if parts == ["api", "state"]:
                return self._send(200, self.state())
            if parts == ["api", "logs"]:
                return self._send(200, self.app.logbuf.since(int(q.get("after", 0))))
            if parts == ["api", "videos"]:
                return self._send(200, self.videos())
            if len(parts) == 4 and parts[:2] == ["api", "cameras"] and parts[3] == "snapshot.jpg":
                cam = self.app.camera_by_id.get(parts[2])
                if cam is None:
                    return self._err(404, "camera is not running")
                prof = cam.profiles.get(q.get("profile", "")) or next(iter(cam.profiles.values()))
                vec = cam.vecs.get(prof.vec) if prof else None
                return self._send(200, snapshot(cam, vec, self.app.sources.epoch), "image/jpeg")
        except Exception as exc:
            log.warning("panel GET %s failed: %s", self.path, exc)
            return self._err(500, str(exc))
        self._err(404, "not found")

    def do_PUT(self):
        if not self._authorized():
            return
        parts, q = self._route()
        try:
            if parts == ["api", "videos"]:
                return self._send(200, self.upload(q.get("name", "")))
            if parts == ["api", "settings"]:
                body = self._json()
                if "advertise_ip" in body:
                    self.app.set_network((body["advertise_ip"] or "").strip())
                if body.get("username"):
                    self.app.set_credentials(body["username"].strip(), body.get("password") or "")
                return self._send(200, {"ok": True})
            if len(parts) == 3 and parts[:2] == ["api", "cameras"]:
                self.app.update_camera(parts[2], self.clean(self._json()))
                return self._send(200, {"ok": True})
        except KeyError:
            return self._err(404, "no such camera")
        except Exception as exc:
            log.warning("panel PUT %s failed: %s", self.path, exc)
            return self._err(400, str(exc))
        self._err(404, "not found")

    def do_POST(self):
        if not self._authorized():
            return
        parts, q = self._route()
        try:
            if parts == ["api", "cameras"]:
                body = self._json()
                count = max(1, min(256, int(body.pop("count", 1) or 1)))
                base = body.get("name") or "VirtualCam"
                ids = []
                for i in range(count):
                    c = self.clean(body)
                    c["name"] = base if count == 1 else f"{base}-{i + 1}"
                    ids.append(self.app.add_camera(c))
                return self._send(200, {"ok": True, "ids": ids})
            if parts == ["api", "shutdown"]:
                log.info("panel: shutdown requested by %s", self.client_address[0])
                self._send(200, {"ok": True})
                self.app.quit.set()
                return
            if len(parts) == 4 and parts[:2] == ["api", "cameras"] and parts[3] == "clone":
                n = max(1, min(256, int(self._json().get("count", 1) or 1)))
                return self._send(200, {"ok": True, "ids": self.app.clone_camera(parts[2], n)})
            if len(parts) == 4 and parts[:2] == ["api", "cameras"] and parts[3] == "motion":
                cam = self.app.camera_by_id.get(parts[2])
                if cam is None:
                    return self._err(404, "camera is not running")
                self.app.events.set_motion(cam, q.get("state", "1") in ("1", "true", "on"),
                                           manual=True)
                return self._send(200, {"ok": True})
        except Exception as exc:
            log.warning("panel POST %s failed: %s", self.path, exc)
            return self._err(400, str(exc))
        self._err(404, "not found")

    def do_DELETE(self):
        if not self._authorized():
            return
        parts, q = self._route()
        try:
            if parts == ["api", "videos"]:
                os.remove(self.video_path(q.get("name", "")))
                return self._send(200, {"ok": True})
            if len(parts) == 3 and parts[:2] == ["api", "cameras"]:
                self.app.remove_camera(parts[2])
                return self._send(200, {"ok": True})
        except KeyError:
            return self._err(404, "no such camera")
        except Exception as exc:
            return self._err(400, str(exc))
        self._err(404, "not found")

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def clean(body):
        """Whitelist of camera fields the panel may set (trust boundary)."""
        out = {}
        for k in ("name", "source", "enabled", "manufacturer", "model", "firmware", "location",
                  "hostname"):
            if k in body:
                out[k] = body[k]
        if "source" in out and not str(out["source"]).strip():
            raise ValueError("source (video file or URL) is required")
        if "profiles" in body:
            profs = []
            for p in body["profiles"][:8]:
                d = {"name": str(p.get("name") or f"Profile{len(profs) + 1}")[:32],
                     "mode": "copy" if p.get("mode") == "copy" else "transcode"}
                if d["mode"] == "transcode":
                    enc = str(p.get("encoding", "H264")).upper()
                    if enc not in ("H264", "H265", "JPEG"):
                        raise ValueError(f"unsupported codec {enc}")
                    d["encoding"] = enc
                    for k, lo, hi in (("width", 16, 7680), ("height", 16, 4320), ("fps", 1, 120),
                                      ("bitrate_kbps", 32, 32768), ("gop", 1, 300)):
                        if p.get(k) not in (None, ""):
                            v = float(p[k])
                            if not lo <= v <= hi:
                                raise ValueError(f"{k} must be between {lo} and {hi}")
                            d[k] = v if k == "fps" else int(v)
                    if p.get("profile"):
                        d["profile"] = str(p["profile"])
                profs.append(d)
            if not profs:
                raise ValueError("at least one stream profile is required")
            out["profiles"] = profs
        if "motion" in body:
            m = body["motion"] or {}
            out["motion"] = {"enabled": bool(m.get("enabled", True)),
                             "on_seconds": max(1, float(m.get("on_seconds") or 10)),
                             "off_seconds": max(1, float(m.get("off_seconds") or 50))}
        return out

    def videos_dir(self):
        d = os.path.abspath(self.app.cfg.get("videos_dir") or "videos")
        os.makedirs(d, exist_ok=True)
        return d

    def video_path(self, name):
        name = os.path.basename(name or "")
        if not name or not name.lower().endswith(VIDEO_EXT):
            raise ValueError("file name must end with " + ", ".join(VIDEO_EXT))
        return os.path.join(self.videos_dir(), name)

    def videos(self):
        d = self.videos_dir()
        out = []
        for f in sorted(os.listdir(d)):
            p = os.path.join(d, f)
            if f.lower().endswith(VIDEO_EXT) and os.path.isfile(p):
                out.append({"name": f, "path": p, "size_mb": round(os.path.getsize(p) / 1e6, 1),
                            "info": video_info(p)})
        return {"dir": d, "files": out}

    def upload(self, name):
        path = self.video_path(name)
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            raise ValueError("empty upload")
        tmp = path + ".part"
        with open(tmp, "wb") as fh:
            left = n
            while left:
                chunk = self.rfile.read(min(1 << 20, left))
                if not chunk:
                    raise ValueError("upload interrupted")
                fh.write(chunk)
                left -= len(chunk)
        os.replace(tmp, path)
        log.info("panel: uploaded %s (%.1f MB)", os.path.basename(path), n / 1e6)
        return {"ok": True, "path": path, "info": video_info(path)}

    def state(self):
        app = self.app
        ip = self.connection.getsockname()[0]
        if ip.startswith("127.") or ip == "0.0.0.0":
            ip = app.primary_ip()
        st = app.status_json(app.advertise_host or ip)
        running = {c["id"]: c for c in st["cameras"]}
        cams = []
        for ccfg in app.cfg["cameras"]:
            cid = ccfg["id"]
            r = running.get(cid)
            cams.append({"config": ccfg, "running": r is not None, "error": app.errors.get(cid),
                         "runtime": r,
                         "sessions": [s for s in st["rtsp_sessions"] if s["camera"] == cid]})
        users = app.cfg.get("users") or [{"username": "admin", "password": "admin"}]
        return {
            "version": __version__, "uptime_s": st["uptime_s"],
            "settings": {"advertise_ip": app.advertise_host or "", "ips": list_ipv4(),
                         "primary_ip": app.primary_ip(), "rtsp_port": app.rtsp_port,
                         "discovery": app.cfg.get("discovery", True),
                         "username": users[0]["username"], "auth": app.cfg.get("auth", True),
                         "config_path": app.config_path},
            "cameras": cams, "encoders": st["encoders"], "sessions": st["rtsp_sessions"],
            "soap_calls": sum(st["soap_calls"].values()), "time": time.time(),
        }


class PanelServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = os.name != "nt"  # on Windows it would let two servers share a port

    def __init__(self, addr, app):
        self.app = app
        super().__init__(addr, Handler)

    def handle_error(self, request, client_address):
        log.debug("panel connection from %s ended", client_address[0])


def serve_panel(app, port, bind="0.0.0.0"):
    srv = PanelServer((bind, port), app)
    threading.Thread(target=srv.serve_forever, name="panel", daemon=True).start()
    log.info("control panel on http://%s:%d/", app.primary_ip(), port)
    return srv
