"""Command line entry point.

Quick start:
    python -m vcam video.mp4
    python -m vcam a.mp4 b.mkv --cameras 4 --user admin --password 12345
    python -m vcam --config cameras.json
"""

import argparse
import json
import logging
import os
import re
import shutil
import signal
import sys

from . import __version__

log = logging.getLogger("vcam")


def parse_stream(spec, name):
    """'H264:1920x1080@25:4096'  'H265:1280x720@15'  'JPEG:640x360@10'  'copy'."""
    if spec.lower() == "copy":
        return {"name": name, "mode": "copy"}
    m = re.match(r"^(?P<enc>[A-Za-z0-9]+)?(?::(?P<w>\d+)x(?P<h>\d+))?(?:@(?P<fps>[\d.]+))?"
                 r"(?::(?P<br>\d+))?(?::gop=(?P<gop>\d+))?$", spec)
    if not m:
        raise argparse.ArgumentTypeError(f"bad stream spec {spec!r}")
    d = {"name": name}
    if m["enc"]:
        d["encoding"] = m["enc"].upper()
    if m["w"]:
        d["width"], d["height"] = int(m["w"]), int(m["h"])
    if m["fps"]:
        d["fps"] = float(m["fps"])
    if m["br"]:
        d["bitrate_kbps"] = int(m["br"])
    if m["gop"]:
        d["gop"] = int(m["gop"])
    return d


def build_config(args):
    cfg = {}
    if args.config and os.path.exists(args.config):
        with open(args.config, encoding="utf-8-sig") as fh:
            cfg = json.load(fh)
        if args.sources:  # the file already lists the cameras; don't add the folder again
            args.sources = []
    if args.config:
        cfg["config_path"] = os.path.abspath(args.config)
    # a folder argument = one camera per video file inside it
    exts = (".mp4", ".mkv", ".mov", ".avi", ".ts", ".m4v", ".h264", ".h265", ".hevc", ".mjpeg")
    args.sources = [os.path.join(s, f) if os.path.isdir(s) else s
                    for s in args.sources
                    for f in (sorted(x for x in os.listdir(s) if x.lower().endswith(exts))
                              if os.path.isdir(s) else [None])]
    if args.sources:
        profiles = [parse_stream(args.main, "MainStream")]
        if args.sub.lower() != "none":
            profiles.append(parse_stream(args.sub, "SubStream"))
        if args.mjpeg:
            profiles.append(parse_stream(args.mjpeg, "MJPEG"))
            profiles[-1]["encoding"] = "JPEG"
        cams = []
        n = 0
        for _ in range(max(1, args.cameras)):
            for src in args.sources:
                n += 1
                base = os.path.splitext(os.path.basename(src))[0][:24] if "://" not in src else "stream"
                cams.append({"id": f"cam{n}", "name": f"{args.name_prefix}{n}-{base}",
                             "source": src, "profiles": json.loads(json.dumps(profiles))})
        cfg["cameras"] = cams
    if not cfg.get("cameras") and not args.config:
        raise SystemExit("no cameras: give video files / a folder, or use -c config.json and add "
                         "cameras in the control panel")
    overrides = {
        "rtsp_port": args.rtsp_port, "http_port_base": args.http_port, "advertise_ip": args.advertise_ip,
        "bind": args.bind, "state_dir": args.state_dir, "x264_preset": args.preset,
        "multicast_interface": args.multicast_iface, "ui_port": args.ui_port,
    }
    for k, v in overrides.items():
        if v is not None:
            cfg[k] = v
    if args.no_auth:
        cfg["auth"] = False
    if args.no_rtsp_auth:
        cfg["rtsp_auth"] = False
    if args.no_discovery:
        cfg["discovery"] = False
    if args.user:
        cfg["users"] = [{"username": args.user, "password": args.password or "", "level": "Administrator"}]
    if args.no_motion:
        cfg.setdefault("motion", {})["enabled"] = False
    if args.prestart:
        cfg["prestart_encoders"] = True
    cfg.setdefault("rtsp_port", 8554)
    cfg.setdefault("http_port_base", 8080)
    cfg.setdefault("state_dir", "./vcam-state")
    cfg.setdefault("ui_port", 8000)
    return cfg


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="vcam", description="ONVIF virtual camera: serve video files as ONVIF Profile S/T "
                                 "devices (WS-Discovery, Device/Media/Media2/Events, RTSP).")
    ap.add_argument("sources", nargs="*", help="video files (or rtsp/http URLs) - one camera each")
    ap.add_argument("-c", "--config", help="JSON configuration file")
    ap.add_argument("-n", "--cameras", type=int, default=1,
                    help="clone the given sources N times (load testing)")
    ap.add_argument("--main", default="H264", help="main stream spec, e.g. H264:1920x1080@25:4096, "
                                                    "H265:2560x1440@20:3000:gop=40 or 'copy'")
    ap.add_argument("--sub", default="H264:640x360@15:512", help="sub stream spec or 'none'")
    ap.add_argument("--mjpeg", nargs="?", const="JPEG:640x360@10", help="add an MJPEG profile")
    ap.add_argument("--http-port", type=int, help="first ONVIF HTTP port (default 8080)")
    ap.add_argument("--rtsp-port", type=int, help="RTSP port (default 8554)")
    ap.add_argument("--bind", help="bind address (default all)")
    ap.add_argument("--advertise-ip", help="IP put in XAddrs / stream URIs (NAT, docker)")
    ap.add_argument("--multicast-iface", help="local IP used for RTP multicast output")
    ap.add_argument("--user", help="admin username (default admin)")
    ap.add_argument("--password", help="admin password (default admin)")
    ap.add_argument("--no-auth", action="store_true", help="disable ONVIF + RTSP authentication")
    ap.add_argument("--no-rtsp-auth", action="store_true", help="RTSP without authentication")
    ap.add_argument("--no-discovery", action="store_true", help="disable WS-Discovery")
    ap.add_argument("--no-motion", action="store_true", help="disable simulated motion events")
    ap.add_argument("--prestart", action="store_true", help="start encoders immediately")
    ap.add_argument("--preset", help="x264/x265 preset (default veryfast)")
    ap.add_argument("--state-dir", help="where configuration changes are persisted")
    ap.add_argument("--ui-port", type=int, help="web control panel port (default 8000, 0 = off)")
    ap.add_argument("--open", action="store_true", help="open the control panel in the browser")
    ap.add_argument("--log-file", help="also log to this file (rotating, 5 MB x 3)")
    ap.add_argument("--stop", action="store_true", help="stop the running instance (uses -c config)")
    ap.add_argument("--name-prefix", default="VirtualCam-")
    ap.add_argument("--print-config", action="store_true", help="print effective config and exit")
    ap.add_argument("-v", "--verbose", action="count", default=0)
    ap.add_argument("--version", action="version", version=f"onvif-vcam {__version__}")
    args = ap.parse_args(argv)

    handlers = [logging.StreamHandler()] if sys.stderr is not None else []
    log_file = args.log_file or (None if sys.stderr is not None else "vcam.log")
    if log_file:  # windowless (pythonw) runs always log to a file
        from logging.handlers import RotatingFileHandler
        handlers.append(RotatingFileHandler(log_file, maxBytes=5_000_000, backupCount=3,
                                            encoding="utf-8"))
    logging.basicConfig(level=logging.DEBUG if args.verbose > 1 else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)-14s %(message)s",
                        datefmt="%H:%M:%S", handlers=handlers)
    if args.verbose < 2:
        logging.getLogger("vcam.soap").setLevel(logging.INFO if args.verbose else logging.WARNING)

    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            raise SystemExit(f"{tool} not found in PATH - install FFmpeg first")

    cfg = build_config(args)
    if args.print_config:
        print(json.dumps(cfg, indent=2))
        return 0

    panel = f"http://127.0.0.1:{cfg.get('ui_port')}/"
    if args.stop:
        return 0 if panel_call(cfg, "/api/shutdown") else 1
    if cfg.get("ui_port") and panel_call(cfg, None):
        # already running (e.g. START.bat pressed twice): just show the panel
        log.info("vcam is already running; opening %s", panel)
        if args.open:
            import webbrowser
            webbrowser.open(panel)
        return 0

    from .app import App
    app = App(cfg)
    # LGPL/minimal ffmpeg builds lack libx264/libx265 and fail with "Unrecognized option 'preset'"
    import subprocess
    from .util import NO_WINDOW
    encoders = subprocess.run([shutil.which("ffmpeg"), "-hide_banner", "-encoders"],
                              capture_output=True, text=True, creationflags=NO_WINDOW).stdout
    need = {"H264": "libx264", "H265": "libx265"}
    missing = sorted({need[v.encoding] for c in app.cameras for v in c.vecs.values()
                      if v.mode != "copy" and v.encoding in need and need[v.encoding] not in encoders})
    if missing:
        raise SystemExit(f"ffmpeg at {shutil.which('ffmpeg')} has no {', '.join(missing)} encoder.\n"
                         "Install a GPL build (Windows: 'winget install Gyan.FFmpeg' or the 'gpl' build "
                         "from github.com/BtbN/FFmpeg-Builds) and put it first in PATH, "
                         "or use '--main copy' to stream the file without re-encoding.")
    app.start()
    if cfg.get("ui_port"):
        from .ui import serve_panel
        try:
            app.ui = serve_panel(app, int(cfg["ui_port"]), app.bind)
        except OSError as exc:
            log.error("control panel disabled: port %s: %s", cfg["ui_port"], exc)

    ip = app.primary_ip()
    users = cfg.get("users") or [{"username": "admin", "password": "admin"}]
    auth = "off" if not cfg.get("auth", True) else ", ".join(
        f"{u['username']}/{u['password']}" for u in users)
    print("\n" + "=" * 78)
    print(f" ONVIF Virtual Camera {__version__}  -  {len(app.cameras)} device(s)  -  auth: {auth}")
    print(" by Behrouz Kazemi, VMS Systems Specialist  |  IPS-Co.  |  Telegram @B_8241")
    print("=" * 78)
    for c in app.endpoints(ip):
        print(f" {c['name']}  [{c['source_info']}]")
        print(f"   ONVIF   {c['device_service']}")
        for p in c["profiles"]:
            enc = p["encoder"]
            desc = f"{enc['encoding']} {enc['resolution']}@{enc['fps']:g}" if enc else "-"
            print(f"   RTSP    {p['rtsp']:<52} {desc}")
    for cid, err in app.errors.items():
        print(f" {cid}: NOT RUNNING - {err}")
    if getattr(app, "ui", None):
        print(f"\n Control panel: http://{ip}:{cfg['ui_port']}/   (Ctrl+C or STOP.bat to stop)")
    if args.open and getattr(app, "ui", None):
        import webbrowser
        webbrowser.open(f"http://127.0.0.1:{cfg['ui_port']}/")
    others = [x for x in app.discovery_ips() if x != ip]
    if others and not app.advertise_host:
        print(f" Note: this PC has several adapters ({', '.join([ip] + others)}). If a VMS sees the\n"
              "       camera on the wrong one, pick the right IP in the panel (Network).")
    print("=" * 78 + "\n", flush=True)

    def _sig(*_):
        app.quit.set()
    signal.signal(signal.SIGINT, _sig)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _sig)
    while not app.quit.wait(1):  # set by Ctrl+C, the panel's shutdown button or STOP.bat
        pass
    app.shutdown()
    return 0


def panel_call(cfg, path):
    """path=None: is a vcam panel answering? Otherwise POST path with the admin credentials."""
    import base64
    import urllib.error
    import urllib.request
    port = cfg.get("ui_port")
    if not port:
        return False
    users = cfg.get("users") or [{"username": "admin", "password": "admin"}]
    tok = base64.b64encode(f"{users[0]['username']}:{users[0]['password']}".encode()).decode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path or '/api/state'}",
                                 data=b"" if path else None, method="POST" if path else "GET",
                                 headers={"Authorization": "Basic " + tok})
    try:
        urllib.request.urlopen(req, timeout=3).read()
        return True
    except urllib.error.HTTPError as e:
        return e.code == 401 and path is None  # someone answers on the port, credentials aside
    except OSError:
        return False


def run():
    """Entry point. Windowless runs (pythonw) report fatal errors in a message box."""
    windowless = sys.stderr is None
    try:
        return main()
    except SystemExit as exc:
        if windowless and exc.code not in (0, None):
            _msgbox(str(exc.code))
        raise
    except Exception:
        log.exception("fatal error")
        if windowless:
            import traceback
            _msgbox(traceback.format_exc()[-1500:])
        raise


def _msgbox(text):
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, text, "ONVIF VCam", 0x10)
    except Exception:
        pass


if __name__ == "__main__":
    sys.exit(run())
