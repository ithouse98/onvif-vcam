"""Screen agents: other PCs on the network that stream their monitors to this VCam.

The agent (vcam.agent, normally a Windows service on the remote PC) connects *out* to this hub,
so the remote PC needs no inbound firewall rule. One TCP port (default 8600) carries:
  * control connections, JSON lines:
      hub -> agent  {"type": "challenge", "nonce": hex}
      agent -> hub  {"type": "hello", "id", "name", "host", "os", "version", "screens": [...],
                     "auth": hmac_sha256(key, nonce)}
      hub -> agent  {"type": "welcome"} | {"type": "denied", "reason"}
      agent -> hub  {"type": "ping"} (every 10 s), {"type": "stream_error", "stream", "msg"}
      hub -> agent  {"type": "start", "stream", "screen", "fps", "bitrate"} / {"type": "stop", ...}
  * data connections: first line {"type": "data", "stream": token, "auth": hmac(key, token)},
    then raw MPEG-TS (H.264) of the captured screen.
UDP on the same port number answers {"type": "discover"} broadcasts so agents find the server.

A camera with source agent://<agent-id>/<screen>[?fps=F] gets its video through a local relay:
open_stream() returns tcp://127.0.0.1:<port> for the GStreamer source process (vcam.gst).
"""

import hashlib
import hmac
import json
import logging
import os
import secrets
import socket
import threading
import time
import urllib.parse

from . import __version__

log = logging.getLogger("vcam.agents")

HUB = None          # the running AgentHub (used by model.probe and gst.GstSource)
PREFIX = "agent://"
PING_TIMEOUT = 35


def sign(key, msg):
    return hmac.new(key.encode(), msg.encode(), hashlib.sha256).hexdigest() if key else ""


def parse(src):
    """agent://<id>/<screen>?fps=F -> (id, screen, fps)"""
    u = urllib.parse.urlsplit(src)
    screen = urllib.parse.unquote(u.path.strip("/")) or ""
    q = dict(urllib.parse.parse_qsl(u.query))
    return u.netloc, screen, int(float(q.get("fps", 25)))


def layout_size(screens, arg):
    """Picture size of screen arg ('' primary, 'N', 'all', 'N+M') on a list of monitors."""
    if not screens:
        return 1920, 1080
    if arg == "all":
        x0, y0 = min(m["x"] for m in screens), min(m["y"] for m in screens)
        w = max(m["x"] - x0 + m["width"] for m in screens)
        h = max(m["y"] - y0 + m["height"] for m in screens)
    elif "+" in arg:
        ms = [screens[int(t)] for t in arg.split("+") if t.isdigit() and int(t) < len(screens)]
        w, h = sum(m["width"] for m in ms), max(m["height"] for m in ms)
    elif arg.isdigit() and int(arg) < len(screens):
        w, h = screens[int(arg)]["width"], screens[int(arg)]["height"]
    else:
        prim = [m for m in screens if m.get("primary")] or screens
        w, h = prim[0]["width"], prim[0]["height"]
    return w + w % 2, h + h % 2


class Agent:
    def __init__(self, hub, sock, addr, info):
        self.hub = hub
        self.sock = sock
        self.addr = addr
        self.info = info
        self.id = info["id"]
        self.last_seen = time.time()
        self.lock = threading.Lock()
        self.alive = True

    def send(self, msg):
        try:
            with self.lock:
                self.sock.sendall((json.dumps(msg) + "\n").encode())
            return True
        except OSError:
            self.close()
            return False

    def close(self):
        if self.alive:
            self.alive = False
            try:
                self.sock.close()
            except OSError:
                pass


class Stream:
    """Relay of one agent stream to one local GStreamer source process."""

    def __init__(self, hub, agent, screen, fps, bitrate):
        self.hub = hub
        self.agent = agent
        self.token = secrets.token_hex(12)
        self.screen, self.fps, self.bitrate = screen, fps, bitrate
        self.local = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.local.bind(("127.0.0.1", 0))
        self.local.listen(1)
        self.port = self.local.getsockname()[1]
        self.data = None
        self.data_evt = threading.Event()
        self.closed = False
        self.error = None
        threading.Thread(target=self._relay, daemon=True, name=f"agent-relay-{self.port}").start()

    def _relay(self):
        consumer = None
        try:
            self.local.settimeout(30)
            consumer, _ = self.local.accept()
            if not self.data_evt.wait(25) or self.data is None:
                raise RuntimeError(self.error or "agent did not start streaming")
            src = self.data
            src.settimeout(30)  # the agent sends continuously; silence = dead link
            while not self.closed:
                chunk = src.recv(65536)
                if not chunk:
                    break
                consumer.sendall(chunk)
        except (OSError, RuntimeError) as exc:
            if not self.closed:
                log.warning("agent %s: stream %s ended: %s", self.agent.info.get("name"), self.screen
                            or "primary", exc)
        finally:
            self.close()
            for s in (consumer, self.data, self.local):
                if s is not None:
                    try:
                        s.close()
                    except OSError:
                        pass

    def close(self):
        if not self.closed:
            self.closed = True
            self.hub.streams.pop(self.token, None)
            if self.agent.alive:
                self.agent.send({"type": "stop", "stream": self.token})
            self.data_evt.set()


class AgentHub:
    def __init__(self, app, cfg):
        global HUB
        self.app = app
        self.cfg = cfg
        self.port = int(cfg.get("agent_port", 8600))
        self.agents = {}            # id -> Agent (online)
        self.streams = {}           # token -> Stream
        self.lock = threading.Lock()
        self.stop_evt = threading.Event()
        self.sock = None
        HUB = self

    @property
    def key(self):
        return self.cfg.get("agent_key") or ""

    # ---------------------------------------------------------------- lifecycle
    def start(self):
        bind = self.cfg.get("bind", "0.0.0.0")
        for port in range(self.port, self.port + 20):
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.SOL_SOCKET, getattr(socket, "SO_EXCLUSIVEADDRUSE", socket.SO_REUSEADDR), 1)
            try:
                s.bind((bind, port))
                break
            except OSError as exc:
                s.close()
                log.warning("agent port %d unavailable: %s", port, exc)
        else:
            log.error("agent hub disabled: no free port from %d", self.port)
            return
        s.listen(64)
        self.sock = s
        self.port = port
        threading.Thread(target=self._accept, daemon=True, name="agent-hub").start()
        threading.Thread(target=self._discovery, daemon=True, name="agent-discovery").start()
        threading.Thread(target=self._reaper, daemon=True, name="agent-reaper").start()
        log.info("agent hub listening on tcp/%d (discovery udp/%d)%s", port,
                 int(self.cfg.get("agent_port", 8600)), "" if self.key else ", no agent key set")

    def shutdown(self):
        self.stop_evt.set()
        for st in list(self.streams.values()):
            st.close()
        for a in list(self.agents.values()):
            a.close()
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass

    def _discovery(self):
        u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            u.bind(("", int(self.cfg.get("agent_port", 8600))))
        except OSError as exc:
            log.warning("agent discovery disabled: %s", exc)
            return
        name = socket.gethostname()
        while not self.stop_evt.is_set():
            try:
                data, addr = u.recvfrom(2048)
                if json.loads(data.decode("utf-8", "replace")).get("type") == "discover":
                    u.sendto(json.dumps({"type": "vcam", "port": self.port, "name": name,
                                         "version": __version__}).encode(), addr)
            except (OSError, ValueError, AttributeError):
                continue

    def _accept(self):
        while not self.stop_evt.is_set():
            try:
                conn, addr = self.sock.accept()
            except OSError:
                if self.stop_evt.is_set():
                    return
                continue
            threading.Thread(target=self._handle, args=(conn, addr), daemon=True).start()

    def _reaper(self):
        while not self.stop_evt.wait(5):
            now = time.time()
            for a in list(self.agents.values()):
                if now - a.last_seen > PING_TIMEOUT:
                    log.warning("agent %s (%s): no heartbeat, disconnecting", a.info.get("name"), a.addr[0])
                    a.close()
                    self._gone(a)

    # ---------------------------------------------------------------- connections
    def _handle(self, conn, addr):
        conn.settimeout(15)
        f = conn.makefile("rb")
        nonce = secrets.token_hex(16)
        try:
            conn.sendall((json.dumps({"type": "challenge", "nonce": nonce,
                                      "server": f"ONVIF VCam {__version__}"}) + "\n").encode())
            first = json.loads(f.readline(65536) or b"{}")
        except (OSError, ValueError):
            conn.close()
            return
        t = first.get("type")
        if t == "data":
            st = self.streams.get(first.get("stream", ""))
            if st is None or not hmac.compare_digest(first.get("auth", ""), sign(self.key, st.token)):
                conn.close()
                return
            st.data = _Prefixed(conn, f)
            st.data_evt.set()
            return
        if t != "hello" or not first.get("id"):
            conn.close()
            return
        if self.key and not hmac.compare_digest(first.get("auth", ""), sign(self.key, nonce)):
            log.warning("agent %s (%s) rejected: wrong agent key", first.get("name"), addr[0])
            try:
                conn.sendall(b'{"type": "denied", "reason": "wrong agent key"}\n')
            except OSError:
                pass
            conn.close()
            return
        info = {k: first.get(k) for k in ("id", "name", "host", "os", "version", "screens")}
        info["screens"] = info.get("screens") or []
        agent = Agent(self, conn, addr, info)
        with self.lock:
            old = self.agents.get(agent.id)
            self.agents[agent.id] = agent
        if old:
            old.close()
        agent.send({"type": "welcome"})
        log.info("agent %s (%s) connected, %d screen(s)", info.get("name"), addr[0], len(info["screens"]))
        self._remember(agent)
        threading.Thread(target=self.app.revive_agent_cameras, args=(agent.id,), daemon=True).start()
        conn.settimeout(None)
        try:
            for line in f:
                agent.last_seen = time.time()
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if msg.get("type") == "stream_error":
                    st = self.streams.get(msg.get("stream", ""))
                    if st:
                        st.error = msg.get("msg") or "capture failed on the agent"
                        log.warning("agent %s: capture failed: %s", info.get("name"), st.error)
                        st.close()
                elif msg.get("type") == "ping" and msg.get("screens"):
                    agent.info["screens"] = msg["screens"]
        except OSError:
            pass
        agent.close()
        self._gone(agent)

    def _gone(self, agent):
        with self.lock:
            if self.agents.get(agent.id) is agent:
                del self.agents[agent.id]
                log.info("agent %s (%s) disconnected", agent.info.get("name"), agent.addr[0])
            else:
                return
        for st in list(self.streams.values()):
            if st.agent is agent:
                st.close()
        self._remember(agent)

    def _remember(self, agent):
        known = self.cfg.setdefault("agents", {})
        k = known.setdefault(agent.id, {})
        changed = k.get("name") != agent.info.get("name") or k.get("ip") != agent.addr[0]
        k.update(name=agent.info.get("name"), host=agent.info.get("host"), ip=agent.addr[0],
                 os=agent.info.get("os"), last_seen=int(time.time()))
        if changed:
            try:
                self.app.save_config()
            except Exception:  # noqa - best effort
                pass

    # ---------------------------------------------------------------- API
    def screens(self, agent_id):
        a = self.agents.get(agent_id)
        if a is None:
            name = (self.cfg.get("agents") or {}).get(agent_id, {}).get("name") or agent_id
            raise RuntimeError(f"agent '{name}' is offline")
        return a.info["screens"]

    def open_stream(self, src, bitrate=None):
        agent_id, screen, fps = parse(src)
        a = self.agents.get(agent_id)
        if a is None:
            self.screens(agent_id)  # raises the offline error
        st = Stream(self, a, screen, fps, int(bitrate or self.cfg.get("agent_bitrate_kbps", 6000)))
        self.streams[st.token] = st
        if not a.send({"type": "start", "stream": st.token, "screen": screen, "fps": fps,
                       "bitrate": st.bitrate}):
            st.close()
            raise RuntimeError("agent connection lost")
        return f"tcp://127.0.0.1:{st.port}"

    def forget(self, agent_id):
        (self.cfg.get("agents") or {}).pop(agent_id, None)
        a = self.agents.get(agent_id)
        if a:
            a.close()
            self._gone(a)
        self.app.save_config()

    def list(self):
        known = dict(self.cfg.get("agents") or {})
        out = []
        for aid in set(known) | set(self.agents):
            a = self.agents.get(aid)
            k = known.get(aid, {})
            out.append({"id": aid, "online": a is not None,
                        "name": (a.info.get("name") if a else None) or k.get("name") or aid,
                        "ip": a.addr[0] if a else k.get("ip"), "os": (a.info.get("os") if a else k.get("os")),
                        "version": a.info.get("version") if a else None,
                        "screens": a.info["screens"] if a else [],
                        "last_seen": int(a.last_seen) if a else k.get("last_seen"),
                        "streams": sum(1 for s in self.streams.values() if s.agent is a) if a else 0})
        out.sort(key=lambda x: (not x["online"], str(x["name"]).lower()))
        return out


class _Prefixed:
    """Socket whose first bytes were already buffered by a makefile() reader."""

    def __init__(self, sock, f):
        self.sock, self.f = sock, f

    def settimeout(self, t):
        self.sock.settimeout(t)

    def recv(self, n):
        data = self.f.read1(n) if hasattr(self.f, "read1") else self.f.read(n)
        return data

    def close(self):
        try:
            self.f.close()
        finally:
            self.sock.close()


def screen_info(src):
    """(width, height, fps) of an agent:// source; raises when the agent is offline."""
    if HUB is None:
        raise RuntimeError("agent hub is not running")
    agent_id, screen, fps = parse(src)
    w, h = layout_size(HUB.screens(agent_id), screen.lower())
    return w, h, float(fps)


def hostname():
    return os.environ.get("COMPUTERNAME") or socket.gethostname()
