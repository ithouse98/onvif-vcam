"""Application wiring: cameras, services, encoders, RTSP, discovery, HTTP servers, status page."""

import collections
import html
import json
import logging
import os
import threading
import time

from .auth import Authenticator
from .discovery import Discovery
from .model import Camera, User
from .rtsp import RtspServer
from .soap import serve
from .source import SourceManager
from .svc_device import DeviceService
from .svc_events import EventService
from .svc_media import MediaService, Media2Service
from .util import list_ipv4, route_ip

log = logging.getLogger("vcam")


class LogBuffer(logging.Handler):
    """Last log lines for the control panel."""

    def __init__(self, size=1000):
        super().__init__(logging.INFO)
        self.lines = collections.deque(maxlen=size)
        self.seq = 0
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)-14s %(message)s",
                                            "%H:%M:%S"))

    def emit(self, record):
        self.seq += 1
        self.lines.append((self.seq, record.levelname, self.format(record)))

    def since(self, seq):
        return [{"seq": n, "level": lv, "text": t} for n, lv, t in list(self.lines) if n > seq]


class App:
    def __init__(self, cfg):
        self.cfg = cfg
        self.stop_evt = threading.Event()
        self.quit = threading.Event()  # request to exit the process (panel shutdown button)
        self.started_at = time.time()
        self.counters = {}
        self.advertise_host = cfg.get("advertise_ip") or None
        self.multicast_iface = cfg.get("multicast_interface") or None
        self.max_profiles = int(cfg.get("max_profiles", 8))
        self.media1_h265_as = cfg.get("media1_h265_as", "H264")
        self.snapshot_auth = bool(cfg.get("snapshot_auth", False))
        self.rtsp_port = int(cfg.get("rtsp_port", 8554))
        self.bind = cfg.get("bind", "0.0.0.0")
        self.state_dir = cfg.get("state_dir")
        if self.state_dir:
            os.makedirs(self.state_dir, exist_ok=True)
        self.config_path = cfg.get("config_path")
        self.lock = threading.RLock()       # serialises camera add/update/remove
        self.logbuf = LogBuffer()
        logging.getLogger().addHandler(self.logbuf)
        self.cameras = []
        self.camera_by_id = {}
        self.errors = {}                     # camera id -> why it is not running
        self.http = {}
        self._index = 0
        cfg.setdefault("cameras", [])
        built = []
        for ccfg in cfg["cameras"]:
            self._ensure_identity(ccfg)
            if ccfg.get("enabled", True):
                try:
                    built.append(self._build(ccfg))
                except Exception as exc:  # one bad file must not take the other cameras down
                    self.errors[ccfg["id"]] = str(exc)
                    log.error("%s: cannot start: %s", ccfg["id"], exc)
        for cam in built:
            self.cameras.append(cam)
            self.camera_by_id[cam.id] = cam

        self.sources = SourceManager(cfg)
        self.services = {}
        self.services["device"] = DeviceService(self)
        self.services["media"] = MediaService(self)
        self.services["media2"] = Media2Service(self)
        self.events = EventService(self)
        self.services["events"] = self.events
        self.rtsp = RtspServer(self, cfg)
        self.discovery = Discovery(self)


    # ------------------------------------------------------------------ lifecycle
    def discovery_ips(self):
        ifs = self.cfg.get("discovery_interfaces")
        if ifs:
            return list(ifs)
        if self.bind not in ("", "0.0.0.0"):
            return [self.bind]
        return list_ipv4() or [route_ip()]

    def primary_ip(self):
        """Address put first in Hello/XAddrs and printed URLs: advertise_ip, else the
        first discovery interface (stable, unlike the default route on offline machines)."""
        if self.advertise_host:
            return self.advertise_host
        ips = self.discovery_ips()
        r = route_ip()
        return r if r in ips else ips[0]

    def start(self):
        self.rtsp.start()
        for cam in list(self.cameras):
            try:
                self.http[cam.id] = serve(self, cam, self.bind)
            except OSError as exc:
                self.errors[cam.id] = f"HTTP port {cam.http_port}: {exc}"
                log.error("%s: %s", cam.id, self.errors[cam.id])
                self.cameras.remove(cam)
                del self.camera_by_id[cam.id]
                continue
            self.events.start_camera(cam)
        if self.config_path and not os.path.exists(self.config_path):
            self.save_config()  # first run: later starts reuse (and the panel edits) this file
        if self.cfg.get("discovery", True):
            try:
                self.discovery.start()
            except OSError as exc:
                log.error("WS-Discovery disabled: cannot bind udp/3702 (%s)", exc)
        for cam in self.cameras:
            for prof in cam.profiles.values():
                vec = cam.vecs.get(prof.vec) if prof.vec else None
                if vec and vec.multicast.auto_start:
                    try:
                        self.rtsp.multicast_start(cam, prof.token, persistent=True)
                    except Exception as exc:
                        log.warning("multicast autostart %s/%s failed: %s", cam.id, prof.token, exc)
        if self.cfg.get("prestart_encoders", False):
            for cam in self.cameras:
                for vec in cam.vecs.values():
                    self.sources.get(cam, vec)

    def shutdown(self):
        log.info("shutting down")
        self.stop_evt.set()
        try:
            if self.cfg.get("discovery", True):
                self.discovery.stop()
        except Exception:
            pass
        for cam in self.cameras:
            cam.stop_evt.set()
        self.events.shutdown()
        self.rtsp.shutdown()
        self.sources.shutdown()
        for srv in list(self.http.values()) + ([self.ui] if getattr(self, "ui", None) else []):
            try:
                srv.shutdown()
            except Exception:
                pass

    # ------------------------------------------------------------------ runtime management
    def _ensure_identity(self, ccfg):
        """Pin id / HTTP port / UUID in the config so edits never make a 'new' device."""
        ids = {c.get("id") for c in self.cfg["cameras"] if c is not ccfg}
        if not ccfg.get("id"):
            n = 1
            while f"cam{n}" in ids:
                n += 1
            ccfg["id"] = f"cam{n}"
        if not ccfg.get("http_port"):
            used = {int(c["http_port"]) for c in self.cfg["cameras"]
                    if c is not ccfg and c.get("http_port")}
            used |= {self.rtsp_port, int(self.cfg.get("ui_port") or 0)}
            p = int(self.cfg.get("http_port_base", 8080))
            while p in used:
                p += 1
            ccfg["http_port"] = p
        if not ccfg.get("uuid"):
            from .util import stable_uuid
            ccfg["uuid"] = stable_uuid(f"{ccfg['id']}:{ccfg.get('source')}:{ccfg['http_port']}")

    def _build(self, ccfg):
        self._index += 1
        cam = Camera(self._index, ccfg, self.cfg)
        cam.authenticator = Authenticator(cam, self.cfg)
        if self.state_dir:
            cam.load_state(os.path.join(self.state_dir, f"{cam.id}.json"))
        return cam

    def _bring_up(self, ccfg):
        cam = self._build(ccfg)
        self.http[cam.id] = serve(self, cam, self.bind)  # raises if the port is taken
        self.cameras.append(cam)
        self.camera_by_id[cam.id] = cam
        self.errors.pop(cam.id, None)
        self.events.start_camera(cam)
        self.discovery.hello(cam)
        log.info("%s: online at port %d (%s)", cam.id, cam.http_port, cam.source)
        return cam

    def _take_down(self, cam_id, reason):
        cam = self.camera_by_id.pop(cam_id, None)
        if cam is None:
            return
        self.cameras.remove(cam)
        cam.stop_evt.set()
        cam.state_path = None  # in-flight SOAP calls on the old object must not rewrite the file
        self.discovery.bye(cam)
        self.rtsp.drop_camera(cam)
        self.events.drop_camera(cam)
        srv = self.http.pop(cam_id, None)
        if srv:
            srv.shutdown()
            srv.server_close()
        log.info("%s: offline (%s)", cam_id, reason)

    def ccfg(self, cam_id):
        for c in self.cfg["cameras"]:
            if c["id"] == cam_id:
                return c
        raise KeyError(cam_id)

    def add_camera(self, ccfg):
        with self.lock:
            ccfg = dict(ccfg)
            ccfg.pop("id", None), ccfg.pop("uuid", None), ccfg.pop("http_port", None)
            self.cfg["cameras"].append(ccfg)
            try:
                self._ensure_identity(ccfg)
                if ccfg.get("enabled", True):
                    self._bring_up(ccfg)
            except Exception:
                self.cfg["cameras"].remove(ccfg)
                raise
            self.save_config()
            return ccfg["id"]

    def clone_camera(self, cam_id, count):
        """N copies of a camera, each its own ONVIF device (new id, UUID, port) for VMS stress
        tests. Clones share the original's encoders (same source + settings), so CPU stays flat."""
        with self.lock:
            src = self.ccfg(cam_id)
            names = {c.get("name") for c in self.cfg["cameras"]}
            ids, n = [], 1
            for _ in range(count):
                while f"{src.get('name', cam_id)}-C{n}" in names:
                    n += 1
                c = json.loads(json.dumps(src))
                c["name"] = f"{src.get('name', cam_id)}-C{n}"
                names.add(c["name"])
                ids.append(self.add_camera(c))
            return ids

    def update_camera(self, cam_id, changes):
        """Apply an edit from the panel. Identity (id, uuid, port) is kept; the camera restarts
        and its VMS-made ONVIF changes are reset so the new profiles take effect."""
        with self.lock:
            old = self.ccfg(cam_id)
            new = dict(old, **{k: v for k, v in changes.items()
                               if k not in ("id", "uuid", "http_port")})
            restart = cam_id in self.camera_by_id or new.get("enabled", True)
            if restart:
                self._take_down(cam_id, "edited")
                if self.state_dir and changes.keys() - {"enabled", "name"}:
                    try:
                        os.remove(os.path.join(self.state_dir, f"{cam_id}.json"))
                    except OSError:
                        pass
                if new.get("enabled", True):
                    try:
                        self._bring_up(new)
                    except Exception as exc:
                        self.errors[cam_id] = str(exc)
                        try:  # keep the device available with its previous settings
                            self._bring_up(old)
                            self.errors[cam_id] = f"change rejected: {exc}"
                        except Exception:
                            pass
                        raise
            self.cfg["cameras"][self.cfg["cameras"].index(old)] = new
            if not new.get("enabled", True):
                self.errors.pop(cam_id, None)
            self.save_config()

    def remove_camera(self, cam_id):
        with self.lock:
            ccfg = self.ccfg(cam_id)
            self._take_down(cam_id, "removed")
            self.cfg["cameras"].remove(ccfg)
            self.errors.pop(cam_id, None)
            if self.state_dir:
                try:
                    os.remove(os.path.join(self.state_dir, f"{cam_id}.json"))
                except OSError:
                    pass
            self.save_config()

    def set_network(self, advertise_ip):
        """Pick the address announced to VMSs; '' = all adapters. Restarts WS-Discovery."""
        with self.lock:
            self.advertise_host = advertise_ip or None
            self.cfg["advertise_ip"] = advertise_ip or None
            self.cfg["discovery_interfaces"] = [advertise_ip] if advertise_ip else None
            if self.cfg.get("discovery", True):
                self.discovery.stop()
                self.discovery = Discovery(self)
                try:
                    self.discovery.start()
                except OSError as exc:
                    log.error("WS-Discovery restart failed: %s", exc)
            self.save_config()

    def set_credentials(self, username, password):
        with self.lock:
            self.cfg["users"] = [{"username": username, "password": password,
                                  "level": "Administrator"}]
            for cam in self.cameras:
                if not self.ccfg(cam.id).get("users"):
                    with cam.lock:
                        cam.users = {username: User(username, password, "Administrator")}
                        cam.save()
            self.save_config()

    def save_config(self):
        if not self.config_path:
            return
        keep = {k: v for k, v in self.cfg.items() if k != "config_path"}
        tmp = self.config_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(keep, fh, indent=2, ensure_ascii=False)
        os.replace(tmp, self.config_path)

    # ------------------------------------------------------------------ status
    def endpoints(self, ip):
        out = []
        for c in self.cameras:
            profiles = []
            with c.lock:
                for p in c.profiles.values():
                    v = c.vecs.get(p.vec) if p.vec else None
                    profiles.append({
                        "token": p.token, "name": p.name,
                        "rtsp": f"rtsp://{ip}:{self.rtsp_port}/{c.id}/{p.token}",
                        "rtsp_multicast": f"rtsp://{ip}:{self.rtsp_port}/{c.id}/{p.token}/multicast",
                        "snapshot": f"http://{ip}:{c.http_port}/onvif/snapshot/{p.token}.jpg",
                        "encoder": None if v is None else {
                            "token": v.token, "encoding": v.encoding,
                            "resolution": f"{v.width}x{v.height}", "fps": v.fps,
                            "bitrate_kbps": v.bitrate_kbps, "gop": v.gop, "profile": v.profile,
                            "mode": v.mode, "multicast": f"{v.multicast.address}:{v.multicast.port}"},
                    })
            out.append({
                "id": c.id, "name": c.name, "uuid": c.uuid, "source": c.source,
                "source_info": f"{c.info.codec} {c.info.width}x{c.info.height}@{c.info.fps:g} "
                               f"{c.info.duration:.1f}s",
                "device_service": f"http://{ip}:{c.http_port}/onvif/device_service",
                "users": [u.username for u in c.users.values()],
                "discoverable": c.discoverable,
                "event_subscriptions": self.events.count(c),
                "motion": self.events.state.get(c.id, {}).get("motion", False),
                "profiles": profiles,
            })
        return out

    def status_json(self, ip):
        return {
            "uptime_s": int(time.time() - self.started_at),
            "rtsp_port": self.rtsp_port,
            "cameras": self.endpoints(ip),
            "encoders": self.sources.stats(),
            "rtsp_sessions": self.rtsp.stats(),
            "soap_calls": dict(sorted(self.counters.items(), key=lambda kv: -kv[1])),
        }

    def status_html(self, ip):
        st = self.status_json(ip)
        e = html.escape
        cams = []
        for c in st["cameras"]:
            rows = "".join(
                f"<tr><td><b>{e(p['name'])}</b><br><span class=mut>{e(p['token'])}</span></td>"
                f"<td>{e(p['encoder']['encoding']) if p['encoder'] else '-'} "
                f"{e(p['encoder']['resolution']) if p['encoder'] else ''}"
                f"{' @ %g fps' % p['encoder']['fps'] if p['encoder'] else ''}<br>"
                f"<span class=mut>{p['encoder']['bitrate_kbps'] if p['encoder'] else ''} kbps"
                f" · GOP {p['encoder']['gop'] if p['encoder'] else ''}"
                f" · {e(p['encoder']['mode']) if p['encoder'] else ''}</span></td>"
                f"<td><code>{e(p['rtsp'])}</code><br><code class=mut>{e(p['rtsp_multicast'])}</code>"
                f"</td><td><a href=\"{e(p['snapshot'])}\" target=_blank>snapshot</a></td></tr>"
                for p in c["profiles"])
            cams.append(
                f"<section><h2>{e(c['name'])} <span class=pill>{e(c['id'])}</span>"
                f"{' <span class=pill on>motion</span>' if c['motion'] else ''}</h2>"
                f"<p class=mut>{e(c['source'])} — {e(c['source_info'])}</p>"
                f"<p>ONVIF: <code>{e(c['device_service'])}</code> · users: "
                f"{e(', '.join(c['users']))} · event subscriptions: {c['event_subscriptions']}</p>"
                f"<table><tr><th>Profile</th><th>Encoder</th><th>Stream URI</th><th></th></tr>"
                f"{rows}</table></section>")
        sess = "".join(
            f"<tr><td>{e(s['id'])}</td><td>{e(s['camera'])}/{e(s['profile'])}</td>"
            f"<td>{e(s['transport'] or '')}</td><td>{e(s['client'])}</td><td>{e(s['state'])}</td>"
            f"<td>{s['packets']}</td><td>{s['age_s']}s</td></tr>" for s in st["rtsp_sessions"]
        ) or "<tr><td colspan=7 class=mut>no active sessions</td></tr>"
        enc = "".join(
            f"<tr><td>{e(x['label'])}</td><td>{e(x['encoding'])} "
            f"{e(x['resolution'])} @ {x['fps']:g}</td><td>{x['frames']}</td><td>{x['mbytes']} MB</td>"
            f"<td>{x['consumers']}</td></tr>" for x in st["encoders"]
        ) or "<tr><td colspan=5 class=mut>no running encoders (started on demand)</td></tr>"
        return f"""<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<meta http-equiv=refresh content=5><title>ONVIF Virtual Camera</title><style>
:root{{--bg:#f6f7f9;--fg:#1d2330;--mut:#6b7385;--card:#fff;--line:#e3e6eb;--acc:#0b6bcb}}
@media (prefers-color-scheme:dark){{:root{{--bg:#12151b;--fg:#e7eaf0;--mut:#8b93a5;--card:#1a1f27;
--line:#2a313c;--acc:#5aa9ff}}}}
body{{margin:0;font:14px/1.45 system-ui,sans-serif;background:var(--bg);color:var(--fg)}}
main{{max-width:1100px;margin:0 auto;padding:24px 16px}}h1{{font-size:20px;margin:0 0 4px}}
h2{{font-size:16px;margin:0 0 6px}}section{{background:var(--card);border:1px solid var(--line);
border-radius:10px;padding:16px;margin:14px 0;overflow-x:auto}}table{{border-collapse:collapse;width:100%}}
th,td{{text-align:left;padding:6px 8px;border-top:1px solid var(--line);vertical-align:top}}
th{{color:var(--mut);font-weight:600;font-size:12px}}code{{font-size:12.5px}}.mut{{color:var(--mut)}}
a{{color:var(--acc)}}.pill{{font-size:11px;border:1px solid var(--line);border-radius:99px;padding:1px 8px;
color:var(--mut);font-weight:500}}.pill[on]{{color:#fff;background:#d9480f;border-color:#d9480f}}
</style></head><body><main><h1>ONVIF Virtual Camera</h1>
<p class=mut>Uptime {st['uptime_s']}s · RTSP port {st['rtsp_port']} · {len(st['cameras'])} device(s)
· <a href="/api/status">JSON</a></p>{''.join(cams)}
<section><h2>RTSP sessions</h2><table><tr><th>Session</th><th>Stream</th><th>Transport</th>
<th>Client</th><th>State</th><th>Packets</th><th>Age</th></tr>{sess}</table></section>
<section><h2>Encoders</h2><table><tr><th>Encoder</th><th>Format</th><th>Frames</th><th>Sent</th>
<th>Consumers</th></tr>{enc}</table></section></main></body></html>"""
