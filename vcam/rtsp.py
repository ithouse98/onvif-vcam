"""RTSP 1.0 server - ONVIF Streaming Specification section 5.

Transports (5.1.1):  RTP/UDP unicast (mandatory), RTP/RTSP/TCP interleaved, RTP/RTSP/HTTP/TCP
                     (Apple tunnelling, mandatory), RTP/UDP multicast.
Methods (5.2.2.1):   OPTIONS DESCRIBE SETUP PLAY PAUSE TEARDOWN GET_PARAMETER SET_PARAMETER.
Keep-alive (5.2.2.2): any request but TEARDOWN, and RTCP receiver reports, refresh the session.
Sync (5.2.2.3):      PLAY response carries Range (clock=...) and RTP-Info (seq, rtptime).
Require (5.3.1):     unknown feature tags (backchannel, onvif-replay) -> 551 Option not supported.
Auth (Core 5.9.1):   RTSP digest at RTSP level; never on the HTTP level of a tunnel.
"""

import base64
import datetime as dt
import logging
import os
import secrets
import select
import socket
import struct
import threading
import time
from collections import deque
from email.utils import formatdate
from urllib.parse import urlsplit

from . import rtp
from .auth import AuthError

log = logging.getLogger("vcam.rtsp")

PUBLIC = "OPTIONS, DESCRIBE, SETUP, PLAY, PAUSE, TEARDOWN, GET_PARAMETER, SET_PARAMETER"
SUPPORTED_REQUIRE = set()  # we do not implement backchannel / replay / SRTP
STATUS = {200: "OK", 400: "Bad Request", 401: "Unauthorized", 404: "Stream Not Found",
          405: "Method Not Allowed", 451: "Parameter Not Understood", 454: "Session Not Found",
          455: "Method Not Valid in This State", 459: "Aggregate Operation Not Allowed",
          461: "Unsupported Transport", 500: "Internal Server Error", 501: "Not Implemented",
          503: "Service Unavailable", 551: "Option not supported"}


# =========================================================================== transport
class SockWriter:
    """Serialises writes (RTSP replies + interleaved RTP) on one TCP socket."""

    def __init__(self, sock):
        self.sock = sock
        self.lock = threading.Lock()
        self.alive = True
        prepare_stream_socket(sock)

    def send(self, data):
        if not self.alive:
            raise ConnectionError("closed")
        with self.lock:
            try:
                self.sock.sendall(data)
            except OSError:
                self.alive = False
                raise

    def close(self):
        self.alive = False
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


SEND_TIMEOUT = 15.0


def prepare_stream_socket(sock):
    """Long-lived media socket: no idle read timeout, but bounded blocking on send.

    A socket timeout applies to both directions in Python, so readers treat socket.timeout as
    'nothing yet' and loop, while a send that cannot progress for SEND_TIMEOUT seconds (client
    stopped reading) fails and ends the session instead of blocking forever.
    """
    try:
        sock.settimeout(SEND_TIMEOUT)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        if hasattr(socket, "TCP_KEEPIDLE"):
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 30)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 10)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)
    except OSError:
        pass


def recv_loop(sock, size=65536):
    """recv that ignores read timeouts; returns b'' only on EOF/error."""
    while True:
        try:
            return sock.recv(size)
        except socket.timeout:
            continue
        except OSError:
            return b""


class PortAllocator:
    def __init__(self, start, end):
        self.start, self.end = start, end
        self.next = start
        self.lock = threading.Lock()

    def pair(self, bind_ip=""):
        with self.lock:
            for _ in range((self.end - self.start) // 2):
                p = self.next
                self.next = self.start if self.next + 2 >= self.end else self.next + 2
                a = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                b = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                try:
                    a.bind((bind_ip, p))
                    b.bind((bind_ip, p + 1))
                    a.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1 << 20)
                    return p, a, b
                except OSError:
                    a.close()
                    b.close()
        raise RuntimeError("no free RTP port pair")


class RtpSender:
    """Source consumer that turns Frames into RTP for one destination."""

    def __init__(self, server, source, mode, pt, on_end=None, *, writer=None, channels=(0, 1),
                 udp=None, dest=None, mcast=None):
        self.server = server
        self.source = source
        self.mode = mode  # udp | tcp | mcast
        self.pt = pt
        self.ssrc = int.from_bytes(os.urandom(4), "big")
        self.seq = int.from_bytes(os.urandom(2), "big")
        self.ts_offset = int.from_bytes(os.urandom(4), "big")
        self.writer = writer
        self.channels = channels
        self.udp = udp            # (port, rtp_sock, rtcp_sock)
        self.dest = dest          # (ip, rtp_port, rtcp_port)
        self.mcast = mcast        # (sock, group, port)
        self.on_end = on_end
        self.queue = deque()
        self.cond = threading.Condition()
        self.waiting_key = True
        self.packets = 0
        self.octets = 0
        self.stopped = False
        self.playing = False
        self.last_rr = time.time()
        self.max_queue = max(30, int(source.fps * 3))
        self.thread = None

    # consumer interface ----------------------------------------------------
    def on_frame(self, frame):
        with self.cond:
            if len(self.queue) >= self.max_queue:
                self.queue.clear()          # client too slow: resync on next key frame
                self.waiting_key = True
            self.queue.append(frame)
            self.cond.notify()

    def on_source_end(self, reason):
        log.info("stream ended (%s), ssrc=%08X", reason, self.ssrc)
        self.stop()
        if self.on_end:
            self.on_end(reason)

    # control ----------------------------------------------------------------
    def next_seq(self):
        return self.seq & 0xFFFF

    def rtptime_now(self):
        return (self.ts_offset + self.source.ts_at()) & 0xFFFFFFFF

    def start(self):
        if self.playing:
            return
        self.playing = True
        self.waiting_key = True
        self.source.subscribe(self)
        if self.thread is None:
            self.thread = threading.Thread(target=self._run, daemon=True,
                                           name=f"rtp-{self.mode}-{self.ssrc:08X}")
            self.thread.start()
        if self.mode == "udp" and not getattr(self, "_rr_thread", None):
            self._rr_thread = threading.Thread(target=self._udp_rx, daemon=True)
            self._rr_thread.start()

    def pause(self):
        self.playing = False
        self.source.unsubscribe(self)
        with self.cond:
            self.queue.clear()

    def stop(self):
        if self.stopped:
            return
        self.stopped = True
        self.playing = False
        self.source.unsubscribe(self)
        with self.cond:
            self.cond.notify_all()
        try:
            self._send_rtcp(rtp.rtcp_bye(self.ssrc))
        except Exception:
            pass
        if self.udp:
            for s in self.udp[1:]:
                try:
                    s.close()
                except OSError:
                    pass

    # wire ---------------------------------------------------------------------
    def _send_rtcp(self, pkt):
        if self.mode == "tcp":
            self.writer.send(b"$" + bytes((self.channels[1],)) + struct.pack(">H", len(pkt)) + pkt)
        elif self.mode == "udp":
            self.udp[2].sendto(pkt, (self.dest[0], self.dest[2]))
        else:
            sock, group, port = self.mcast
            sock.sendto(pkt, (group, port + 1))

    def _sr(self):
        ts = self.rtptime_now()
        self._send_rtcp(rtp.rtcp_sr(self.ssrc, ts, self.packets, self.octets))

    def _run(self):
        last_sr = 0.0
        while not self.stopped:
            with self.cond:
                if not self.queue:
                    self.cond.wait(1.0)
                frames = list(self.queue)
                self.queue.clear()
            try:
                for f in frames:
                    if self.waiting_key:
                        if not f.key:
                            continue
                        self.waiting_key = False
                    self._send_frame(f)
                now = time.time()
                if self.packets and now - last_sr >= 5.0 and self.playing:
                    self._sr()
                    last_sr = now
            except (OSError, ConnectionError) as exc:
                if not self.stopped:
                    log.info("rtp send failed (%s): ending session", exc)
                    self.on_source_end("connection lost")
                return

    def _send_frame(self, f):
        ts = (self.ts_offset + f.ts) & 0xFFFFFFFF
        n = len(f.payloads)
        if self.mode == "tcp":
            chunks = []
            ch = bytes((self.channels[0],))
            for i, p in enumerate(f.payloads):
                pkt = rtp.rtp_header(self.pt, i == n - 1, self.seq, ts, self.ssrc) + p
                self.seq = (self.seq + 1) & 0xFFFF
                chunks.append(b"$" + ch + struct.pack(">H", len(pkt)) + pkt)
            self.writer.send(b"".join(chunks))
        else:
            if self.mode == "udp":
                sock, addr = self.udp[1], (self.dest[0], self.dest[1])
            else:
                sock, addr = self.mcast[0], (self.mcast[1], self.mcast[2])
            for i, p in enumerate(f.payloads):
                pkt = rtp.rtp_header(self.pt, i == n - 1, self.seq, ts, self.ssrc) + p
                self.seq = (self.seq + 1) & 0xFFFF
                try:
                    sock.sendto(pkt, addr)
                except BlockingIOError:
                    pass
        self.packets += n
        self.octets += f.size
        if self.packets == n:
            self._sr()

    def _udp_rx(self):
        """Receive on both server ports. RTCP RR = session keep-alive (5.2.2.2). Packets from the
        client's IP also latch the destination port (symmetric RTP), so RTP still reaches clients
        behind NAT: Docker/WSL/Hyper-V/VMware networks where the client's port is rewritten."""
        socks = {self.udp[1]: 1, self.udp[2]: 2}
        while not self.stopped:
            try:
                ready, _, _ = select.select(list(socks), [], [], 1.0)
            except (OSError, ValueError):
                return  # sockets closed by stop()
            for s in ready:
                try:
                    data, addr = s.recvfrom(2048)
                except OSError:
                    continue  # Windows reports ICMP port-unreachable here (WSAECONNRESET)
                if addr[0] != self.dest[0]:
                    continue  # only the client itself may move its own stream
                i = socks[s]
                if i == 2 and len(data) >= 8 and 200 <= data[1] <= 204:
                    self.last_rr = time.time()
                if self.dest[i] != addr[1]:
                    log.info("udp ssrc=%08X: client %s %s port %d -> %d (NAT latching)", self.ssrc,
                             addr[0], "RTP" if i == 1 else "RTCP", self.dest[i], addr[1])
                    self.dest[i] = addr[1]


# =========================================================================== session
class Session:
    def __init__(self, server, cam, profile_token, vec_token, timeout, conn):
        self.id = secrets.token_hex(6).upper()
        self.server = server
        self.cam = cam
        self.profile = profile_token
        self.vec = vec_token
        self.timeout = timeout
        self.conn = conn  # RtspConnection that created it (for TCP transports)
        self.sender = None
        self.group = None
        self.transport = None
        self.state = "INIT"
        self.last = time.time()
        self.created = time.time()
        self.peer = conn.peer if conn else None
        self.user = None
        self.agent = "-"
        self.ended = False

    def touch(self):
        self.last = time.time()

    def end(self, reason="teardown", close_conn=False):
        if self.ended:
            return
        self.ended = True
        if self.sender:
            self.sender.stop()
        if self.group:
            self.group.release(self)
        self.server.remove_session(self)
        pk = self.sender.packets if self.sender else 0
        log.info("session %s %s/%s %s closed (%s) after %ds, %d RTP packets sent, client %s [%s]",
                 self.id, self.cam.id, self.profile, self.transport, reason,
                 time.time() - self.created, pk, self.peer[0] if self.peer else "?", self.agent)
        if close_conn and self.conn and self.transport == "tcp":
            self.conn.close()

    def on_stream_end(self, reason):
        # encoder reconfigured / stopped: drop the session so the client reconnects
        threading.Thread(target=self.end, args=(reason, True), daemon=True).start()


class MulticastGroup:
    def __init__(self, server, cam, profile_token, vec):
        self.server = server
        self.cam = cam
        self.profile = profile_token
        self.vec_token = vec.token
        self.group = vec.multicast.address
        self.port = vec.multicast.port
        self.ttl = vec.multicast.ttl
        self.sessions = set()
        self.persistent = False
        self.sender = None
        self.lock = threading.Lock()

    def ensure(self, source, pt):
        with self.lock:
            if self.sender and not self.sender.stopped:
                return self.sender
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, self.ttl)
            s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
            iface = self.server.app.multicast_iface
            if iface:
                s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(iface))
            self.sender = RtpSender(self.server, source, "mcast", pt, self._ended,
                                    mcast=(s, self.group, self.port))
            return self.sender

    def _ended(self, reason):
        for s in list(self.sessions):
            s.on_stream_end(reason)
        with self.lock:
            self.sender = None

    def attach(self, session):
        with self.lock:
            self.sessions.add(session)

    def release(self, session):
        with self.lock:
            self.sessions.discard(session)
            idle = not self.sessions and not self.persistent
            sender = self.sender if idle else None
            if idle:
                self.sender = None
        if sender:
            log.info("multicast %s:%d stopped (no receivers)", self.group, self.port)
            sender.stop()

    def stop(self):
        with self.lock:
            self.persistent = False
            sender, self.sender = self.sender, None
            sessions = list(self.sessions)
        for s in sessions:
            s.end("multicast stopped")
        if sender:
            sender.stop()


# =========================================================================== parsing
class Request:
    __slots__ = ("method", "url", "version", "headers", "body")

    def header(self, name, default=None):
        return self.headers.get(name.lower(), default)


class RtspParser:
    """Incremental parser for RTSP requests mixed with '$' interleaved frames."""

    def __init__(self):
        self.buf = bytearray()

    def feed(self, data):
        self.buf += data
        out = []
        while self.buf:
            if self.buf[0] == 0x24:  # '$'
                if len(self.buf) < 4:
                    break
                ln = struct.unpack_from(">H", self.buf, 2)[0]
                if len(self.buf) < 4 + ln:
                    break
                out.append(("data", self.buf[1], bytes(self.buf[4:4 + ln])))
                del self.buf[:4 + ln]
                continue
            end = self.buf.find(b"\r\n\r\n")
            sep = 4
            if end < 0:
                end = self.buf.find(b"\n\n")
                sep = 2
            if end < 0:
                if len(self.buf) > 65536:
                    self.buf.clear()
                break
            head = bytes(self.buf[:end]).decode("utf-8", "replace")
            lines = [ln.rstrip("\r") for ln in head.split("\n")]
            while lines and not lines[0].strip():
                lines.pop(0)
            if not lines:
                del self.buf[:end + sep]
                continue
            req = Request()
            parts = lines[0].split()
            if len(parts) < 3:
                del self.buf[:end + sep]
                out.append(("bad", lines[0], None))
                continue
            req.method, req.url, req.version = parts[0].upper(), parts[1], parts[2]
            req.headers = {}
            for ln in lines[1:]:
                if ":" in ln:
                    k, v = ln.split(":", 1)
                    req.headers[k.strip().lower()] = v.strip()
            clen = int(req.headers.get("content-length", "0") or 0)
            if len(self.buf) < end + sep + clen:
                break
            req.body = bytes(self.buf[end + sep:end + sep + clen])
            del self.buf[:end + sep + clen]
            out.append(("req", req, None))
        return out


# =========================================================================== connection
class RtspConnection:
    def __init__(self, server, writer, peer, local_ip, tunnel=False):
        self.server = server
        self.writer = writer
        self.peer = peer
        self.local_ip = local_ip
        self.tunnel = tunnel
        self.parser = RtspParser()
        self.sessions = {}
        self.auth = {}  # camera id -> (user, level)
        self.closed = False
        self.lock = threading.Lock()

    # ---------------------------------------------------------------- io
    def feed(self, data):
        for kind, a, b in self.parser.feed(data):
            if kind == "data":
                if a % 2 == 1:  # RTCP RR from client over interleaved channel = keep-alive
                    for s in self.sessions.values():
                        s.touch()
            elif kind == "req":
                with self.lock:
                    try:
                        self.handle(a)
                    except (OSError, ConnectionError):
                        self.close()
                        return
                    except Exception:
                        log.exception("RTSP request failed")
                        self.reply(a, 500)
            else:
                log.debug("bad RTSP line from %s: %r", self.peer, a)

    def close(self):
        if self.closed:
            return
        self.closed = True
        for s in list(self.sessions.values()):
            if s.transport in ("tcp", None) or not self.server.udp_outlives_tcp:
                s.end("connection closed")
        self.writer.close()

    def reply(self, req, code, headers=None, body=b""):
        lines = [f"RTSP/1.0 {code} {STATUS.get(code, 'Error')}",
                 f"CSeq: {req.header('cseq', '0') if req else '0'}",
                 f"Date: {formatdate(usegmt=True)}", f"Server: {self.server.server_name}"]
        for k, v in (headers or []):
            lines.append(f"{k}: {v}")
        if body:
            lines.append(f"Content-Length: {len(body)}")
        data = ("\r\n".join(lines) + "\r\n\r\n").encode() + (body or b"")
        log.debug("RTSP %s <- %s %s %d", self.peer, req.method if req else "?",
                  req.url if req else "", code)
        self.writer.send(data)

    # ---------------------------------------------------------------- dispatch
    def handle(self, req):
        log.debug("RTSP %s -> %s %s", self.peer, req.method, req.url)
        self.server.requests += 1
        sess = None
        sid = (req.header("session") or "").split(";")[0].strip()
        if sid:
            sess = self.server.sessions.get(sid)
            if sess:
                sess.touch()  # 5.2.2.2: any request other than TEARDOWN is a keep-alive
        req_tags = [t.strip() for t in (req.header("require") or "").split(",") if t.strip()]
        unsupported = [t for t in req_tags if t not in SUPPORTED_REQUIRE]
        if unsupported and req.method not in ("OPTIONS", "TEARDOWN"):
            return self.reply(req, 551, [("Unsupported", ", ".join(unsupported))])
        m = getattr(self, "do_" + req.method.replace("-", "_"), None)
        if m is None:
            return self.reply(req, 405, [("Allow", PUBLIC)])
        return m(req, sess)

    def resolve(self, req):
        """rtsp://h:p/<cam>/<profile>[/multicast][/trackID=n] -> (cam, profile, vec, mcast)."""
        url = req.url
        path = urlsplit(url).path if "://" in url else url.split("?")[0]
        segs = [s for s in path.split("/") if s]
        segs = [s for s in segs if not s.lower().startswith(("trackid=", "track"))]
        mcast = False
        if segs and segs[-1].lower() == "multicast":
            mcast = True
            segs = segs[:-1]
        app = self.server.app
        cam = app.camera_by_id.get(segs[0]) if segs else None
        if cam is None and len(app.cameras) == 1 and len(segs) <= 1:
            cam = app.cameras[0]
            segs = [cam.id] + segs
        if cam is None:
            return None
        with cam.lock:
            if len(segs) >= 2:
                prof = cam.profiles.get(segs[1])
                if prof is None:  # also accept profile names: /cam1/MainStream
                    prof = next((p for p in cam.profiles.values()
                                 if p.name.lower() == segs[1].lower()), None)
            else:
                prof = next(iter(cam.profiles.values()), None)
            if prof is None or not prof.vec or prof.vec not in cam.vecs:
                return None
            vec = cam.vecs[prof.vec]
        return cam, prof, vec, mcast

    def authorize(self, req, cam):
        authn = cam.authenticator
        if not authn.enabled or not self.server.rtsp_auth or cam.id in self.auth:
            return True
        try:
            who = authn.check_authorization(req.header("authorization"), req.method)
        except AuthError as exc:
            who = None
            stale = exc.kind == "stale"
        else:
            stale = False
        if who and authn.allowed(who[1], "READ_MEDIA"):
            self.auth[cam.id] = who
            return True
        hdrs = [("WWW-Authenticate", c) for c in authn.rtsp_challenges(stale)]
        self.reply(req, 401, hdrs)
        return False

    @staticmethod
    def base_url(req):
        u = req.url.split("?")[0]
        segs = u.split("/")
        if segs and segs[-1].lower().startswith("trackid="):
            u = "/".join(segs[:-1])
        return u if u.endswith("/") else u + "/"

    # ---------------------------------------------------------------- methods
    def do_OPTIONS(self, req, sess):
        hdrs = [("Public", PUBLIC)]
        if sess:
            hdrs.append(("Session", f"{sess.id};timeout={sess.timeout}"))
        self.reply(req, 200, hdrs)

    def do_DESCRIBE(self, req, sess):
        r = self.resolve(req)
        if r is None:
            return self.reply(req, 404)
        cam, prof, vec, mcast = r
        if not self.authorize(req, cam):
            return
        src = self.server.app.sources.get(cam, vec)
        if not src.wait_ready(self.server.describe_timeout):
            log.warning("%s/%s encoder not ready: %s", cam.id, vec.token, src.failed)
            return self.reply(req, 503)
        group = self.server.group(cam, prof, vec) if mcast else None
        sdp = self.server.sdp(src, cam, prof, self.local_ip, group)
        base = self.base_url(req)
        self.reply(req, 200, [("Content-Base", base), ("Content-Type", "application/sdp")],
                   sdp.encode())

    def do_SETUP(self, req, sess):
        r = self.resolve(req)
        if r is None:
            return self.reply(req, 404)
        cam, prof, vec, mcast_uri = r
        if not self.authorize(req, cam):
            return
        if sess and (sess.cam is not cam or sess.profile != prof.token):
            return self.reply(req, 459)
        sid = (req.header("session") or "").split(";")[0].strip()
        if sid and not sess:
            return self.reply(req, 454)
        tr = self.server.pick_transport(req.header("transport") or "", mcast_uri, self.tunnel)
        if tr is None:
            return self.reply(req, 461)
        src = self.server.app.sources.get(cam, vec)
        if not src.wait_ready(self.server.describe_timeout):
            return self.reply(req, 503)
        pt, _ = src.sdp_media()
        if sess is None:
            # a VMS may store a tiny SessionTimeout; never ask RTSP clients for keep-alives < 60 s
            sess = Session(self.server, cam, prof.token, vec.token, max(60, vec.session_timeout),
                           self)
            sess.user = self.auth.get(cam.id, (None,))[0]
            sess.agent = req.header("user-agent", "-")
            self.server.add_session(sess)
        elif sess.sender:
            sess.sender.stop()
            sess.sender = None
        self.sessions[sess.id] = sess
        kind = tr["kind"]
        sess.transport = kind
        if kind == "tcp":
            ch = tr.get("interleaved", (0, 1))
            sess.sender = RtpSender(self.server, src, "tcp", pt, sess.on_stream_end,
                                    writer=self.writer, channels=ch)
            resp_tr = f"RTP/AVP/TCP;unicast;interleaved={ch[0]}-{ch[1]};ssrc={sess.sender.ssrc:08X}"
        elif kind == "udp":
            port, a, b = self.server.ports.pair(self.server.bind_ip)
            cip = tr.get("destination") if self.server.allow_destination and \
                tr.get("destination") else self.peer[0]
            cp = tr["client_port"]
            sess.sender = RtpSender(self.server, src, "udp", pt, sess.on_stream_end,
                                    udp=(port, a, b), dest=[cip, cp[0], cp[1]])
            resp_tr = (f"RTP/AVP;unicast;client_port={cp[0]}-{cp[1]};"
                       f"server_port={port}-{port + 1};ssrc={sess.sender.ssrc:08X};mode=\"play\"")
            if cip != self.peer[0]:
                resp_tr += f";destination={cip}"
        else:
            group = self.server.group(cam, prof, vec)
            sess.group = group
            group.attach(sess)
            resp_tr = (f"RTP/AVP;multicast;destination={group.group};port={group.port}-"
                       f"{group.port + 1};ttl={group.ttl};mode=\"play\"")
        sess.state = "READY"
        log.info("SETUP %s %s/%s %s from %s (%s)", sess.id, cam.id, prof.token, kind,
                 self.peer[0], "tunnel" if self.tunnel else "rtsp")
        self.reply(req, 200, [("Transport", resp_tr), ("Session", f"{sess.id};timeout={sess.timeout}"),
                              ("Cache-Control", "no-cache")])

    def do_PLAY(self, req, sess):
        if sess is None:
            return self.reply(req, 454)
        if sess.state == "INIT":
            return self.reply(req, 455)
        cam = sess.cam
        if not self.authorize(req, cam):
            return
        vec = cam.vecs.get(sess.vec)
        if vec is None:
            return self.reply(req, 404)
        src = self.server.app.sources.get(cam, vec)
        if not src.wait_ready(self.server.describe_timeout):
            return self.reply(req, 503)
        if sess.group is not None:
            pt, _ = src.sdp_media()
            sender = sess.group.ensure(src, pt)
        else:
            sender = sess.sender
            if sender.source is not src:  # encoder restarted between SETUP and PLAY
                sender.source = src
        track = self.base_url(req) + "trackID=1"
        rtp_info = f"url={track};seq={sender.next_seq()};rtptime={sender.rtptime_now()}"
        start = dt.datetime.now(dt.timezone.utc)
        rng = start.strftime("clock=%Y%m%dT%H%M%S.") + f"{start.microsecond // 1000:03d}Z-"
        self.reply(req, 200, [("Session", f"{sess.id};timeout={sess.timeout}"), ("Range", rng),
                              ("RTP-Info", rtp_info)])
        sender.start()
        sess.state = "PLAYING"

    def do_PAUSE(self, req, sess):
        if sess is None:
            return self.reply(req, 454)
        if sess.sender and sess.group is None:
            sess.sender.pause()
        sess.state = "READY"
        self.reply(req, 200, [("Session", f"{sess.id};timeout={sess.timeout}")])

    def do_TEARDOWN(self, req, sess):
        if sess is None:
            return self.reply(req, 454)
        self.reply(req, 200, [("Session", sess.id)])
        self.sessions.pop(sess.id, None)
        sess.end("TEARDOWN")

    def do_GET_PARAMETER(self, req, sess):
        sid = (req.header("session") or "").split(";")[0].strip()
        if sid and sess is None:
            return self.reply(req, 454)
        hdrs = [("Session", f"{sess.id};timeout={sess.timeout}")] if sess else []
        self.reply(req, 200, hdrs)

    def do_SET_PARAMETER(self, req, sess):
        sid = (req.header("session") or "").split(";")[0].strip()
        if sid and sess is None:
            return self.reply(req, 454)
        hdrs = [("Session", f"{sess.id};timeout={sess.timeout}")] if sess else []
        if req.body and req.body.strip():
            return self.reply(req, 451, hdrs)
        self.reply(req, 200, hdrs)


def b64_stream_decode(pending: bytes):
    """Decode a stream of independently padded base64 blocks (one per RTSP request).

    Clients base64-encode every request separately, so '=' padding can appear mid-stream when
    several requests arrive in one read. Returns (decoded_bytes, undecoded_remainder).
    """
    out = bytearray()
    start = i = 0
    n = len(pending)
    while i + 4 <= n:
        quad = pending[i:i + 4]
        i += 4
        if b"=" in quad:
            out += base64.b64decode(pending[start:i])
            start = i
    if i > start:
        out += base64.b64decode(pending[start:i])
    return bytes(out), pending[i:]


# =========================================================================== server
class RtspServer:
    def __init__(self, app, cfg):
        self.app = app
        self.port = int(cfg.get("rtsp_port", 8554))
        self.bind_ip = cfg.get("bind", "0.0.0.0")
        if self.bind_ip == "0.0.0.0":
            self.bind_ip = ""
        pr = cfg.get("rtp_port_range", [50000, 59999])
        self.ports = PortAllocator(int(pr[0]), int(pr[1]))
        self.server_name = "ONVIF-VCam RTSP/1.0"
        self.describe_timeout = float(cfg.get("describe_timeout", 12))
        self.rtsp_auth = bool(cfg.get("rtsp_auth", True))
        self.allow_destination = bool(cfg.get("rtsp_allow_destination", False))
        self.strict_tcp_timeout = bool(cfg.get("strict_tcp_session_timeout", False))
        self.udp_outlives_tcp = True
        self.sessions = {}
        self.groups = {}
        self.tunnels = {}
        self.lock = threading.Lock()
        self.tunnel_cond = threading.Condition()
        self.requests = 0
        self.stop_evt = threading.Event()
        self.sock = None

    # ---------------------------------------------------------------- lifecycle
    def start(self):
        # Busy (VMS, mediamtx/go2rtc use 8554) or Windows-reserved (Hyper-V/WSL/Docker, WinError 10013)
        # ports are common; fall back, clients learn the real port from GetStreamUri.
        wanted, last = self.port, None
        for port in dict.fromkeys([wanted, 8554, 18554, 28554, 38554, 554]):
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            # Windows SO_REUSEADDR lets two servers share a port silently; exclusive use instead
            s.setsockopt(socket.SOL_SOCKET, getattr(socket, "SO_EXCLUSIVEADDRUSE", socket.SO_REUSEADDR), 1)
            try:
                s.bind((self.bind_ip, port))
                break
            except OSError as exc:
                s.close()
                last = exc
                log.warning("RTSP port %d unavailable: %s", port, exc)
        else:
            raise SystemExit(
                f"No RTSP port could be opened (tried {wanted}, 8554, 18554, 28554, 38554, 554):\n{last}\n\n"
                "Ports are used by another program or reserved by Windows (Hyper-V / WSL / Docker).\n"
                f"Check:  netstat -ano | findstr :{wanted}\n"
                "        netsh interface ipv4 show excludedportrange protocol=tcp\n\n"
                'Fix: set a free "rtsp_port" in config.json, then run START.bat again.')
        if port != wanted:
            log.warning("RTSP port %d unavailable, using %d instead", wanted, port)
        self.port = port
        s.listen(256)
        self.sock = s
        threading.Thread(target=self._accept, name="rtsp-accept", daemon=True).start()
        threading.Thread(target=self._reaper, name="rtsp-reaper", daemon=True).start()
        log.info("RTSP server listening on tcp/%d", self.port)

    def shutdown(self):
        self.stop_evt.set()
        for s in list(self.sessions.values()):
            s.end("shutdown")
        for g in list(self.groups.values()):
            g.stop()
        try:
            self.sock.close()
        except OSError:
            pass

    def _accept(self):
        while not self.stop_evt.is_set():
            try:
                c, addr = self.sock.accept()
            except OSError:
                continue
            threading.Thread(target=self._serve, args=(c, addr), daemon=True,
                             name=f"rtsp-{addr[0]}:{addr[1]}").start()

    def _serve(self, c, addr):
        local_ip = self.app.advertise_host or c.getsockname()[0]
        prepare_stream_socket(c)
        # Sniff RTSP vs. HTTP tunnel. A single recv/peek may return fewer bytes than needed,
        # so read until the first 4 bytes are known and hand them to whichever handler wins.
        first = b""
        while len(first) < 4:
            d = recv_loop(c)
            if not d:
                c.close()
                return
            first += d
        if first[:4] in (b"GET ", b"POST"):
            return self._raw_http_tunnel(c, addr, local_ip, first)
        writer = SockWriter(c)
        conn = RtspConnection(self, writer, addr, local_ip)
        try:
            conn.feed(first)
            while not conn.closed:
                data = recv_loop(c)
                if not data:
                    break
                conn.feed(data)
        finally:
            conn.close()

    # ---------------------------------------------------------------- sessions
    def add_session(self, s):
        with self.lock:
            self.sessions[s.id] = s

    def remove_session(self, s):
        with self.lock:
            self.sessions.pop(s.id, None)

    def _reaper(self):
        while not self.stop_evt.wait(5):
            now = time.time()
            for s in list(self.sessions.values()):
                limit = s.timeout + 10  # grace period over the advertised timeout
                if s.transport == "tcp" and s.conn and not s.conn.closed and \
                        not self.strict_tcp_timeout:
                    continue  # the TCP connection itself proves the client is alive
                last = s.last
                if s.sender and s.transport == "udp":
                    last = max(last, s.sender.last_rr)
                if now - last > limit:
                    s.end("session timeout")

    def drop_camera(self, cam):
        for s in list(self.sessions.values()):
            if s.cam is cam:
                s.end("camera reboot", close_conn=True)
        for k in [k for k in self.groups if k[0] == cam.id]:
            self.groups.pop(k).stop()

    def drop_vec(self, cam, vec_token):
        for s in list(self.sessions.values()):
            if s.cam is cam and s.vec == vec_token:
                s.end("encoder reconfigured", close_conn=True)
        for k, g in list(self.groups.items()):
            if k[0] == cam.id and g.vec_token == vec_token:
                self.groups.pop(k).stop()

    def drop_profile(self, cam, token):
        for s in list(self.sessions.values()):
            if s.cam is cam and s.profile == token:
                s.end("profile changed", close_conn=True)
        g = self.groups.pop((cam.id, token), None)
        if g:
            g.stop()

    # ---------------------------------------------------------------- multicast
    def group(self, cam, prof, vec):
        key = (cam.id, prof.token)
        with self.lock:
            g = self.groups.get(key)
            if g and (g.vec_token != vec.token or (g.group, g.port) !=
                      (vec.multicast.address, vec.multicast.port)):
                g.stop()
                g = None
            if g is None:
                g = MulticastGroup(self, cam, prof.token, vec)
                self.groups[key] = g
            return g

    def multicast_start(self, cam, profile_token, persistent=True):
        prof = cam.profiles[profile_token]
        vec = cam.vecs[prof.vec]
        g = self.group(cam, prof, vec)
        g.persistent = persistent
        src = self.app.sources.get(cam, vec)
        if not src.wait_ready(self.describe_timeout):
            raise RuntimeError("encoder not ready")
        pt, _ = src.sdp_media()
        g.ensure(src, pt).start()
        log.info("multicast %s/%s -> %s:%d ttl=%d", cam.id, profile_token, g.group, g.port, g.ttl)

    def multicast_stop(self, cam, profile_token):
        g = self.groups.pop((cam.id, profile_token), None)
        if g:
            g.stop()

    # ---------------------------------------------------------------- SDP / transport
    def sdp(self, src, cam, prof, server_ip, group=None):
        pt, attrs = src.sdp_media()
        sess_id = int(time.time())
        vec = src.vec
        lines = ["v=0", f"o=- {sess_id} 1 IN IP4 {server_ip}", f"s={cam.name}",
                 f"i={prof.name}", "t=0 0", "a=tool:onvif-vcam", "a=type:broadcast",
                 "a=control:*", "a=range:npt=now-"]
        if group:
            lines.append(f"c=IN IP4 {group.group}/{group.ttl}")
            lines.append(f"m=video {group.port} RTP/AVP {pt}")
        else:
            lines.append("c=IN IP4 0.0.0.0")
            lines.append(f"m=video 0 RTP/AVP {pt}")
        lines.append(f"b=AS:{int(vec.bitrate_kbps)}")
        lines += attrs
        lines.append(f"a=framerate:{src.fps:g}")
        lines.append(f"a=x-onvif-profile:{prof.token}")
        lines.append("a=recvonly")
        lines.append("a=control:trackID=1")
        return "\r\n".join(lines) + "\r\n"

    def pick_transport(self, header, mcast_uri, tunnel):
        for spec in [s.strip() for s in header.split(",") if s.strip()]:
            parts = [p.strip() for p in spec.split(";")]
            proto = parts[0].upper()
            params = {}
            for p in parts[1:]:
                k, _, v = p.partition("=")
                params[k.lower()] = v.strip('"')
            if proto not in ("RTP/AVP", "RTP/AVP/UDP", "RTP/AVP/TCP"):
                continue
            if proto == "RTP/AVP/TCP":
                ch = (0, 1)
                if "interleaved" in params:
                    try:
                        a, _, b = params["interleaved"].partition("-")
                        ch = (int(a), int(b) if b else int(a) + 1)
                    except ValueError:
                        pass
                return {"kind": "tcp", "interleaved": ch}
            if "multicast" in params or (mcast_uri and "unicast" not in params):
                return {"kind": "mcast"}
            if tunnel:
                continue  # UDP is meaningless through an HTTP tunnel
            cp = params.get("client_port")
            if not cp:
                continue
            try:
                a, _, b = cp.partition("-")
                ports = (int(a), int(b) if b else int(a) + 1)
            except ValueError:
                continue
            return {"kind": "udp", "client_port": ports, "destination": params.get("destination")}
        if mcast_uri and not header:
            return {"kind": "mcast"}
        if tunnel and header:
            return {"kind": "tcp", "interleaved": (0, 1)}
        return None

    # ---------------------------------------------------------------- RTSP over HTTP
    # Apple "Tunneling QuickTime RTSP and RTP over HTTP": a GET carries server->client data,
    # a POST (base64) carries client->server RTSP; both share the x-sessioncookie header.
    def _tunnel_get(self, cookie, send_raw, wait_closed, peer, local_ip):
        writer_sock = send_raw
        resp = ("HTTP/1.0 200 OK\r\nServer: ONVIF-VCam\r\nConnection: close\r\n"
                f"Date: {formatdate(usegmt=True)}\r\nCache-Control: no-store\r\nPragma: no-cache\r\n"
                "Content-Type: application/x-rtsp-tunnelled\r\n\r\n").encode()
        writer_sock.send(resp)
        conn = RtspConnection(self, writer_sock, peer, local_ip, tunnel=True)
        with self.tunnel_cond:
            self.tunnels[cookie] = conn
            self.tunnel_cond.notify_all()
        log.info("RTSP-over-HTTP tunnel opened by %s (cookie %s)", peer[0], cookie[:12])
        try:
            wait_closed()
        finally:
            with self.tunnel_cond:
                if self.tunnels.get(cookie) is conn:
                    del self.tunnels[cookie]
            conn.close()
            log.info("RTSP-over-HTTP tunnel closed (%s)", cookie[:12])

    def _tunnel_post(self, cookie, read):
        # clients open the POST right after the GET without waiting for its reply, so the POST
        # can win the race: give the GET side a moment to register the cookie
        deadline = time.time() + 5.0
        with self.tunnel_cond:
            while cookie not in self.tunnels and time.time() < deadline:
                self.tunnel_cond.wait(0.1)
            conn = self.tunnels.get(cookie)
        if conn is None:
            log.warning("tunnel POST for unknown cookie %s", cookie)
            return False
        pending = b""
        while not conn.closed:
            data = read()
            if not data:
                break
            pending += b"".join(data.split())
            try:
                decoded, pending = b64_stream_decode(pending)
            except Exception:
                log.warning("bad base64 in tunnel POST from cookie %s", cookie[:12])
                break
            if decoded:
                conn.feed(decoded)
        return True

    def _raw_http_tunnel(self, c, addr, local_ip, buf=b""):
        """HTTP tunnel arriving on the RTSP port itself."""
        while b"\r\n\r\n" not in buf:
            d = recv_loop(c, 4096)
            if not d or len(buf) > 16384:
                c.close()
                return
            buf += d
        head, _, rest = buf.partition(b"\r\n\r\n")
        lines = head.decode("latin-1").split("\r\n")
        method = lines[0].split()[0].upper()
        hdrs = {}
        for ln in lines[1:]:
            k, _, v = ln.partition(":")
            hdrs[k.strip().lower()] = v.strip()
        cookie = hdrs.get("x-sessioncookie")
        if not cookie:
            c.sendall(b"HTTP/1.0 400 Bad Request\r\nContent-Length: 0\r\n\r\n")
            c.close()
            return
        if method == "GET":
            writer = SockWriter(c)

            def wait_closed():
                while recv_loop(c, 4096):
                    pass
            self._tunnel_get(cookie, writer, wait_closed, addr, local_ip)
        else:
            # body bytes that arrived together with the headers; an empty leftover must not be
            # mistaken for end-of-stream (headers often arrive alone when the host is busy)
            first = [rest] if rest else []

            def read():
                if first:
                    return first.pop()
                return recv_loop(c)
            self._tunnel_post(cookie, read)
            try:
                c.close()
            except OSError:
                pass

    def http_tunnel_get(self, handler):
        """Called from the ONVIF HTTP server for GET + x-sessioncookie."""
        cookie = handler.headers.get("x-sessioncookie")
        sock = handler.connection
        handler.close_connection = True
        writer = SockWriter(sock)  # replaces the HTTP server's 120 s idle timeout

        def wait_closed():
            while recv_loop(sock, 4096):
                pass
        local_ip = self.app.advertise_host or sock.getsockname()[0]
        self._tunnel_get(cookie, writer, wait_closed, handler.client_address, local_ip)

    def http_tunnel_post(self, handler):
        cookie = handler.headers.get("x-sessioncookie")
        handler.close_connection = True
        rfile = handler.rfile
        prepare_stream_socket(handler.connection)

        def read():
            while True:
                try:
                    return rfile.read1(65536) if hasattr(rfile, "read1") else rfile.read(1)
                except socket.timeout:
                    continue
                except OSError:
                    return b""
        if not self._tunnel_post(cookie, read):
            handler.send_response(404)
            handler.send_header("Content-Length", "0")
            handler.end_headers()

    # ---------------------------------------------------------------- status
    def stats(self):
        out = []
        for s in list(self.sessions.values()):
            out.append({"id": s.id, "camera": s.cam.id, "profile": s.profile,
                        "transport": ("http-tunnel" if s.conn and s.conn.tunnel and
                                      s.transport == "tcp" else s.transport),
                        "client": s.peer[0] if s.peer else "?", "state": s.state,
                        "packets": s.sender.packets if s.sender else
                        (s.group.sender.packets if s.group and s.group.sender else 0),
                        "age_s": int(time.time() - s.created), "agent": s.agent})
        return out
