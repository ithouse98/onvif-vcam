"""VCam screen agent: streams this PC's monitors to an ONVIF VCam server on the network.

    python -m vcam.agent [--server HOST[:PORT]] [--key KEY] [--name NAME] [--config agent.json]

Without --server the agent finds the VCam server by UDP broadcast (port 8600). It connects out
to the server's agent hub, so no inbound firewall rule is needed on this PC, and it appears in
the VCam control panel (Agents) with its monitors. When a camera on the server uses one of them
(agent://<id>/<screen>), the server asks for a stream and the agent starts a GStreamer capture:
screen -> H.264 -> MPEG-TS -> server. On Windows, AGENT-INSTALL.bat runs it as a service
(vcam.winservice), which starts this agent inside the logged-in user's session (screen access).
"""

import argparse
import json
import logging
import os
import platform
import socket
import subprocess
import sys
import threading
import time
import uuid
from collections import deque

from . import __version__, gst
from .agents import hostname, sign
from .util import NO_WINDOW

log = logging.getLogger("vcam.agent")
DISCOVERY_PORT = 8600


def load_config(path):
    cfg = {}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                cfg = json.load(fh)
        except ValueError:
            log.warning("%s is not valid JSON; using defaults", path)
    if not cfg.get("id"):
        cfg["id"] = str(uuid.uuid4())
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(cfg, fh, indent=2)
        except OSError as exc:
            log.warning("cannot save %s: %s", path, exc)
    return cfg


def discover(port=DISCOVERY_PORT, timeout=3.0):
    """Broadcast for a VCam server -> (host, tcp_port) or None."""
    u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    u.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    u.settimeout(timeout)
    try:
        u.sendto(b'{"type": "discover"}', ("255.255.255.255", port))
        while True:
            data, addr = u.recvfrom(2048)
            msg = json.loads(data.decode("utf-8", "replace"))
            if msg.get("type") == "vcam":
                return addr[0], int(msg.get("port") or port)
    except (OSError, ValueError):
        return None
    finally:
        u.close()


class AgentSession:
    def __init__(self, cfg, host, port):
        self.cfg = cfg
        self.host, self.port = host, port
        self.key = cfg.get("key") or ""
        self.sock = None
        self.lock = threading.Lock()
        self.procs = {}       # stream token -> Popen
        self.alive = True

    def send(self, msg):
        try:
            with self.lock:
                self.sock.sendall((json.dumps(msg) + "\n").encode())
        except OSError:
            self.alive = False

    def screens(self):
        try:
            scr = gst.devices().get("screens") or []
            if not scr:  # X11 / GDI-only: one picture, measure it
                r = gst.probe("screen://")
                scr = [{"index": 0, "name": "Screen", "width": r["width"], "height": r["height"],
                        "x": 0, "y": 0, "primary": True}]
            return scr
        except Exception as exc:  # noqa - still register, the server shows 'no screens'
            log.warning("cannot list monitors: %s", exc)
            return []

    def run(self):
        self.sock = socket.create_connection((self.host, self.port), timeout=10)
        f = self.sock.makefile("rb")
        challenge = json.loads(f.readline(65536) or b"{}")
        screens = self.screens()
        self.send({"type": "hello", "id": self.cfg["id"], "name": self.cfg.get("name") or hostname(),
                   "host": hostname(), "os": f"{platform.system()} {platform.release()}",
                   "version": __version__, "screens": screens,
                   "auth": sign(self.key, challenge.get("nonce", ""))})
        reply = json.loads(f.readline(65536) or b"{}")
        if reply.get("type") != "welcome":
            raise PermissionError(reply.get("reason") or "server refused the agent")
        log.info("registered with %s:%d as '%s' (%d screen(s))", self.host, self.port,
                 self.cfg.get("name") or hostname(), len(screens))
        self.sock.settimeout(None)
        threading.Thread(target=self._pinger, daemon=True).start()
        try:
            for line in f:
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if msg.get("type") == "start":
                    self.start(msg)
                elif msg.get("type") == "stop":
                    self.stop(msg.get("stream"))
        finally:
            self.alive = False
            for tok in list(self.procs):
                self.stop(tok)
            try:
                self.sock.close()
            except OSError:
                pass
        log.info("disconnected from %s:%d", self.host, self.port)

    def _pinger(self):
        n = 0
        while self.alive:
            time.sleep(10)
            n += 1
            msg = {"type": "ping"}
            if n % 6 == 0:  # monitors may have been plugged / rearranged
                msg["screens"] = self.screens()
            self.send(msg)

    def start(self, msg):
        tok = msg.get("stream", "")
        screen = msg.get("screen") or ""
        fps = int(msg.get("fps") or 25)
        cmd = gst.helper_cmd("push", f"screen://{screen}?fps={fps}", "--to", f"{self.host}:{self.port}",
                             "--token", tok, "--auth", sign(self.key, tok),
                             "--bitrate", str(int(msg.get("bitrate") or 6000)),
                             "--rc", str(msg.get("rc") or "vbr"))
        log.info("streaming screen %s at %d fps", screen or "primary", fps)
        p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.PIPE, env=gst._env(), creationflags=NO_WINDOW)
        self.procs[tok] = p
        threading.Thread(target=self._watch, args=(tok, p), daemon=True).start()

    def _watch(self, tok, p):
        tail = deque(maxlen=8)
        for line in iter(p.stderr.readline, b""):
            s = line.decode("utf-8", "replace").strip()
            if s:
                tail.append(s)
                log.debug("capture: %s", s)
        rc = p.wait()
        if self.procs.pop(tok, None) is not None and self.alive:
            why = " | ".join(tail) or f"capture exited with code {rc}"
            log.warning("capture stopped: %s", why)
            self.send({"type": "stream_error", "stream": tok, "msg": why[-500:]})

    def stop(self, tok):
        p = self.procs.pop(tok, None)
        if p and p.poll() is None:
            log.info("stream stopped by the server")
            try:
                p.kill()
            except OSError:
                pass


def run_forever(cfg, stop_evt=None):
    backoff = 2
    while not (stop_evt and stop_evt.is_set()):
        server = (cfg.get("server") or "").strip()
        if server:
            host, _, port = server.partition(":")
            target = (host, int(port or DISCOVERY_PORT))
        else:
            target = discover()
            if target is None:
                log.info("no VCam server found by broadcast; retrying (set the server address "
                         "with --server or in agent.json)")
        if target:
            try:
                AgentSession(cfg, *target).run()
                backoff = 2
            except PermissionError as exc:
                log.error("server refused the agent: %s", exc)
                backoff = 30
            except (OSError, ValueError) as exc:
                log.warning("cannot reach %s:%d: %s", target[0], target[1], exc)
        if stop_evt:
            stop_evt.wait(backoff)
        else:
            time.sleep(backoff)
        backoff = min(backoff * 2, 30)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="vcam.agent", description=__doc__.split("\n\n")[0])
    ap.add_argument("--server", help="VCam server HOST[:PORT] (default: find by broadcast)")
    ap.add_argument("--key", help="agent key set in the VCam panel (if any)")
    ap.add_argument("--name", help="name shown in the panel (default: computer name)")
    ap.add_argument("--config", default="agent.json", help="settings / identity file")
    ap.add_argument("--log-file", help="also log to this file")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    handlers = [logging.StreamHandler()] if sys.stderr is not None else []
    if args.log_file or sys.stderr is None:
        from logging.handlers import RotatingFileHandler
        handlers.append(RotatingFileHandler(args.log_file or "vcam-agent.log", maxBytes=2_000_000,
                                            backupCount=2, encoding="utf-8"))
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)-7s %(name)-11s %(message)s", datefmt="%H:%M:%S")
    cfg = load_config(args.config)
    for k in ("server", "key", "name"):
        if getattr(args, k):
            cfg[k] = getattr(args, k)
    why = gst.available()
    if why:
        log.error("GStreamer is not available (%s); the agent cannot capture the screen", why)
        return 1
    log.info("ONVIF VCam agent %s, id %s", __version__, cfg["id"])
    try:
        run_forever(cfg)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
