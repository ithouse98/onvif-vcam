"""Turns a video source into a paced, packetized, multi-consumer live stream.

Engines: "gstreamer" (default) runs one GStreamer process per source with one encoder branch per
configuration (vcam.gst); "ffmpeg" runs one ffmpeg process per (source, configuration). The
encoder only decodes/encodes; all framing, timing and RTP packetization is done here, so the RTSP
server can serve any number of unicast/multicast/tunnelled clients from a single encoder (like a
real camera does). Screen and webcam sources always use GStreamer.
"""

import base64
import copy
import logging
import os
import subprocess
import threading
import time
from collections import deque

from . import gst, rtp
from .util import NO_WINDOW

log = logging.getLogger("vcam.source")

MAX_HOLD = 0.15   # live sources: longest a frame waits for its paced send slot (jitter buffer)
LIVE_PREFIXES = ("rtsp://", "rtmp://", "http://", "https://", "udp://", "srt://", "tcp://") + \
    gst.DEVICE_PREFIXES


class Frame:
    """One access unit, already split into RTP payloads (headers are added per session)."""
    __slots__ = ("ts", "payloads", "key", "wall", "size")

    def __init__(self, ts, payloads, key, wall):
        self.ts = ts
        self.payloads = payloads
        self.key = key
        self.wall = wall
        self.size = sum(len(p) for p in payloads)


def ffmpeg_cmd(path, vec, preset="veryfast", threads=0, loop=True, start_pos=0.0):
    live = path.startswith(LIVE_PREFIXES)
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin"]
    if path.startswith("rtsp://"):
        cmd += ["-rtsp_transport", "tcp"]
    if loop and not live:
        cmd += ["-stream_loop", "-1"]
    if start_pos > 0.5 and not live:
        # all encoders of a camera show the same 'live' moment of the looping file
        cmd += ["-ss", f"{start_pos:.3f}"]
    cmd += ["-fflags", "+genpts", "-i", path, "-map", "0:v:0", "-an", "-sn", "-dn"]
    enc = vec.encoding
    if vec.mode == "copy":
        if enc == "H264":
            cmd += ["-c:v", "copy", "-bsf:v", "h264_mp4toannexb", "-f", "h264"]
        elif enc == "H265":
            cmd += ["-c:v", "copy", "-bsf:v", "hevc_mp4toannexb", "-f", "hevc"]
        else:
            cmd += ["-c:v", "copy", "-f", "mjpeg"]
        return cmd + ["pipe:1"]
    pix = "yuvj420p" if enc == "JPEG" else "yuv420p"
    vf = f"fps={vec.fps:g},scale={vec.width}:{vec.height}:flags=bicubic,setsar=1,format={pix}"
    return cmd + ["-vf", vf] + encoder_args(vec, preset, threads) + ["pipe:1"]


def encoder_args(vec, preset="veryfast", threads=0):
    """ffmpeg output options for one encoder config (shared with the GStreamer H.265 branch)."""
    from .gst import crf
    enc = vec.encoding
    br = max(32, int(vec.bitrate_kbps))
    cmd = []
    if threads:
        cmd += ["-threads", str(threads)]
    gop = max(1, int(vec.gop))
    if enc == "H264":
        prof = {"baseline": "baseline", "main": "main", "high": "high",
                "extended": "main"}.get(vec.profile.lower(), "main")
        x264p = "repeat-headers=1:aud=1"
        # CBR: constant rate with filler; VBR: constant quality capped at the bitrate (see gst.x264_rc)
        rc = (["-b:v", f"{br}k", "-minrate", f"{br}k"] if vec.cbr else ["-crf", str(crf(vec.quality))])
        if vec.cbr:
            x264p += ":nal-hrd=cbr"
        cmd += ["-c:v", "libx264", "-preset", preset, "-tune", "zerolatency", "-profile:v", prof,
                "-bf", "0", "-g", str(gop), "-keyint_min", str(gop), "-sc_threshold", "0",
                *rc, "-maxrate", f"{br}k", "-bufsize", f"{br}k",
                "-x264-params", x264p, "-f", "h264"]
    elif enc == "H265":
        rc = f"bitrate={br}:strict-cbr=1" if vec.cbr else f"crf={crf(vec.quality) + 2}"
        cmd += ["-c:v", "libx265", "-preset", preset, "-tune", "zerolatency",
                "-x265-params",
                f"keyint={gop}:min-keyint={gop}:scenecut=0:bframes=0:repeat-headers=1:aud=1:"
                f"log-level=error:{rc}:vbv-maxrate={br}:vbv-bufsize={br}",
                "-f", "hevc"]
    else:  # JPEG
        q = int(round(31 - (max(1.0, min(10.0, vec.quality)) - 1) * (29 / 9)))
        cmd += ["-c:v", "mjpeg", "-huffman", "default", "-q:v", str(max(2, min(31, q))),
                "-f", "mjpeg"]
    return cmd


class AnnexBSplitter:
    """Incremental Annex-B start-code splitter."""

    def __init__(self):
        self.buf = bytearray()

    def feed(self, data):
        self.buf += data
        nals = []
        buf = self.buf
        start = buf.find(b"\x00\x00\x01")
        if start < 0:
            return nals
        while True:
            nxt = buf.find(b"\x00\x00\x01", start + 3)
            if nxt < 0:
                break
            nal = bytes(buf[start + 3:nxt]).rstrip(b"\x00")
            if nal:
                nals.append(nal)
            start = nxt
        del buf[:start]
        return nals

    def flush(self):
        nal = bytes(self.buf[3:]).rstrip(b"\x00") if self.buf.startswith(b"\x00\x00\x01") else b""
        self.buf.clear()
        return [nal] if nal else []


class JpegSplitter:
    def __init__(self):
        self.buf = bytearray()

    def feed(self, data):
        self.buf += data
        out = []
        while True:
            s = self.buf.find(b"\xff\xd8")
            if s < 0:
                self.buf.clear()
                break
            e = self.buf.find(b"\xff\xd9", s + 2)
            # EOI inside entropy data is impossible (0xFF is stuffed), but an embedded thumbnail
            # could contain one; ffmpeg's encoder never writes one.
            if e < 0:
                if s:
                    del self.buf[:s]
                break
            out.append(bytes(self.buf[s:e + 2]))
            del self.buf[:e + 2]
        return out


class StreamSource:
    """Single encoder feeding many consumers."""

    def __init__(self, manager, camera, vec):
        self.manager = manager
        self.camera = camera
        # private copy: the encoder is shared by every camera with the same source + settings,
        # and apply_vec mutates a camera's config in place
        self.vec = copy.copy(vec)
        if gst.is_device(camera.source):
            self.vec.mode = "transcode"  # nothing to copy from a capture device
        vec = self.vec
        self.use_gst = gst.is_device(camera.source) or (manager.engine == "gstreamer"
                                                        and vec.mode != "copy")
        self.key = (os.path.basename(camera.source) or camera.source,
                    "copy" if vec.mode == "copy" else
                    f"{vec.encoding} {vec.width}x{vec.height}@{vec.fps:g}")
        self.stream_key = vec.stream_key()
        self.encoding = vec.encoding
        self.fps = vec.fps if vec.mode != "copy" else camera.info.fps
        self.mtu = manager.mtu
        self.params = {}
        self.ready = threading.Event()
        self.failed = None
        self.consumers = set()
        self.lock = threading.Lock()
        self.stop_evt = threading.Event()
        self.last_ts = 0
        self.last_wall = time.time()
        self.idle_since = time.time()
        self.frames = 0
        self.bytes = 0
        self.started = time.time()
        self.proc = None
        self.stderr_tail = deque(maxlen=20)
        # RTP clock is continuous across encoder restarts / file loops
        self._ts_acc = float(int.from_bytes(os.urandom(2), "big") * 90)
        self._t_next = time.time()
        self.live = camera.source.startswith(LIVE_PREFIXES)
        self._anchor = None     # (wall, rtp) of the first live frame
        self._prev_ts = self._ts_acc - 1
        self._arrival = time.time()   # when the data of the frame being emitted arrived
        if vec.mode == "copy" and camera.info.has_bframes:
            log.warning("%s/%s: source has B-frames; 'copy' mode sends them in decode order "
                        "(timestamps may be non-monotonic). Real cameras never use B-frames - "
                        "prefer transcode mode for this file.", camera.id, vec.token)
        self.thread = threading.Thread(target=self._run, name=f"src-{camera.id}-{vec.token}",
                                       daemon=True)
        self.thread.start()

    # ------------------------------------------------------------------ consumers
    def subscribe(self, consumer):
        with self.lock:
            self.consumers.add(consumer)

    def unsubscribe(self, consumer):
        with self.lock:
            self.consumers.discard(consumer)
            if not self.consumers:
                self.idle_since = time.time()

    @property
    def consumer_count(self):
        with self.lock:
            return len(self.consumers)

    def ts_at(self, now=None):
        now = time.time() if now is None else now
        return int(self.last_ts + (now - self.last_wall) * 90000) & 0xFFFFFFFF

    def stop(self, reason="stopped"):
        self.stop_evt.set()
        p = self.proc
        if p and p.poll() is None:
            try:
                p.kill()
            except OSError:
                pass
        with self.lock:
            consumers = list(self.consumers)
            self.consumers.clear()
        for c in consumers:
            try:
                c.on_source_end(reason)
            except Exception:  # noqa
                pass

    def wait_ready(self, timeout=10.0):
        self.ready.wait(timeout)
        return self.ready.is_set()

    # ------------------------------------------------------------------ SDP helpers
    def sdp_media(self, pt=96):
        """Returns (payload_type, [attribute lines])."""
        lines = []
        if self.encoding == "JPEG":
            lines.append(f"a=rtpmap:{rtp.PT_JPEG} JPEG/90000")
            lines.append(f"a=x-dimensions:{self.vec.width},{self.vec.height}")
            return rtp.PT_JPEG, lines
        if self.encoding == "H264":
            sps, pps = self.params.get("sps"), self.params.get("pps")
            fmtp = f"a=fmtp:{pt} packetization-mode=1"
            if sps and len(sps) >= 4:
                fmtp += f";profile-level-id={sps[1]:02X}{sps[2]:02X}{sps[3]:02X}"
            if sps and pps:
                fmtp += ";sprop-parameter-sets=" + base64.b64encode(sps).decode() + "," + \
                    base64.b64encode(pps).decode()
            lines.append(f"a=rtpmap:{pt} H264/90000")
            lines.append(fmtp)
        else:
            parts = []
            for k in ("vps", "sps", "pps"):
                if self.params.get(k):
                    parts.append(f"sprop-{k}=" + base64.b64encode(self.params[k]).decode())
            lines.append(f"a=rtpmap:{pt} H265/90000")
            if parts:
                lines.append(f"a=fmtp:{pt} " + ";".join(parts))
        return pt, lines

    # ------------------------------------------------------------------ main loop
    def _run(self):
        backoff = 1.0
        while not self.stop_evt.is_set():
            t_start = time.time()
            try:
                self._run_once()
            except Exception as exc:  # keep the device alive whatever happens
                log.exception("%s/%s source error: %s", *self.key, exc)
            if self.stop_evt.is_set():
                break
            if time.time() - t_start > 10:
                backoff = 1.0
            tail = " | ".join(self.stderr_tail)
            log.warning("%s/%s encoder exited%s; restarting in %.0fs", *self.key,
                        f" ({tail})" if tail else "", backoff)
            if not self.ready.is_set():
                self.failed = tail or "encoder failed to start"
            self.stop_evt.wait(backoff)
            backoff = min(backoff * 2, 30)

    def _drain_stderr(self, proc):
        for line in iter(proc.stderr.readline, b""):
            s = line.decode("utf-8", "replace").strip()
            if s:
                self.stderr_tail.append(s)
                log.debug("ffmpeg[%s/%s]: %s", *self.key, s)

    def _run_once(self):
        if self.use_gst:
            return self._run_gst()
        dur = self.camera.info.duration
        pos = (time.time() - self.manager.epoch) % dur if dur > 1 else 0.0
        cmd = ffmpeg_cmd(self.camera.source, self.vec, self.manager.preset, self.manager.threads,
                         self.manager.loop, pos)
        log.info("%s/%s starting encoder: %s", *self.key, " ".join(cmd))
        self.proc = proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                            creationflags=NO_WINDOW,
                                            stdin=subprocess.DEVNULL, bufsize=0)
        threading.Thread(target=self._drain_stderr, args=(proc,), daemon=True).start()
        try:
            if self.encoding == "JPEG":
                self._loop_jpeg(proc)
            else:
                self._loop_annexb(proc)
        finally:
            if proc.poll() is None:
                try:
                    proc.kill()
                except OSError:
                    pass
            proc.wait()

    def _run_gst(self):
        desc, ff = gst.branch_desc(self.vec, self.manager.preset, self.manager.threads,
                                   fixed_rate=self.camera.source.startswith("screen://"))
        src = self.manager.gst_source(self.camera)
        log.info("%s/%s starting GStreamer encoder: %s", *self.key, desc)
        self.proc = branch = src.add(desc, ff)
        try:
            if self.encoding == "JPEG":
                self._loop_jpeg(branch)
            else:
                self._loop_annexb(branch)
        finally:
            src.remove(branch)
            if branch.error and branch.error != "stopped":
                self.stderr_tail.append(branch.error)

    # pacing --------------------------------------------------------------
    def _pace_and_emit(self, payloads, key):
        frame_dur = 1.0 / self.fps if self.fps > 0 else 0.04
        now = time.time()
        if self.frames == 0 or now - self._t_next > 1.0:  # first frame or fell behind (stall)
            self._t_next = now
        elif self.live:
            # live: pacing only smooths jitter. A burst (network / CPU hiccup) is sent at once
            # instead of being spread out, which used to turn every hiccup into permanent delay,
            # and a smaller hold drains (frames go out 1/8 frame early) so no delay is kept
            hold = self._t_next - self._arrival
            if hold > MAX_HOLD:
                self._t_next = now
            elif hold > 0:
                self._t_next -= min(hold, frame_dur / 8)
        delay = self._t_next - now
        if delay > 0:
            time.sleep(delay)  # high-resolution on Windows (3.11+), unlike Event.wait
            if self.stop_evt.is_set():
                return
        wall = time.time()
        self._t_next += frame_dur
        if self.live:
            # RTP time = send time: smooth while paced, compressed in a catch-up burst (players
            # catch up too), right for a source slower than the profile's frame rate
            if self._anchor is None:
                self._anchor = (wall, self._ts_acc)
            self._ts_acc = max(self._anchor[1] + (wall - self._anchor[0]) * 90000.0,
                               self._prev_ts + 90)   # >= 1 ms apart (ms-based recorders)
        ts = int(self._ts_acc) & 0xFFFFFFFF
        self._prev_ts = self._ts_acc
        self._ts_acc += 90000.0 * frame_dur
        frame = Frame(ts, payloads, key, wall)
        self.last_ts, self.last_wall = ts, wall
        self.frames += 1
        self.bytes += frame.size
        with self.lock:
            consumers = list(self.consumers)
        for c in consumers:
            try:
                c.on_frame(frame)
            except Exception:  # noqa
                log.exception("consumer error")

    def _read(self, proc):
        if isinstance(proc, gst.Branch):
            while not self.stop_evt.is_set():
                data = proc.read(1.0)
                if data is not None:
                    self._arrival = proc.arrival
                    return data
            return b""
        data = proc.stdout.read(65536)
        self._arrival = time.time()
        return data

    # H.264 / H.265 -----------------------------------------------------------
    def _loop_annexb(self, proc):
        hevc = self.encoding == "H265"
        splitter = AnnexBSplitter()
        au, au_has_vcl, au_key = [], False, False
        # GStreamer H.264 branches deliver exactly one access unit per read (h264parse alignment=au):
        # emit it at once instead of waiting for the next frame's start code (one frame of delay)
        aligned = isinstance(proc, gst.Branch) and not proc.ffmpeg
        while not self.stop_evt.is_set():
            data = self._read(proc)
            if not data:
                break
            nals = splitter.feed(data)
            if aligned:
                nals += splitter.flush()
            for nal in nals:
                if hevc:
                    if len(nal) < 3:
                        continue
                    t = (nal[0] >> 1) & 0x3F
                    vcl = t < 32
                    first = vcl and bool(nal[2] & 0x80)
                    starts_au = (vcl and first) or (not vcl and t in
                                                    (32, 33, 34, 35, 39, 41, 42, 43, 44) or
                                                    48 <= t <= 55)
                    is_key = 16 <= t <= 21
                    if t == 32:
                        self.params["vps"] = nal
                    elif t == 33:
                        self.params["sps"] = nal
                    elif t == 34:
                        self.params["pps"] = nal
                else:
                    t = nal[0] & 0x1F
                    vcl = 1 <= t <= 5
                    first = vcl and len(nal) > 1 and bool(nal[1] & 0x80)
                    starts_au = (vcl and first) or (not vcl and t in (6, 7, 8, 9, 14, 15, 18))
                    is_key = t == 5
                    if t == 7:
                        self.params["sps"] = nal
                    elif t == 8:
                        self.params["pps"] = nal
                if starts_au and au_has_vcl:
                    self._emit_annexb(au, au_key, hevc)
                    au, au_has_vcl, au_key = [], False, False
                au.append(nal)
                au_has_vcl |= vcl
                au_key |= is_key
                if not self.ready.is_set():
                    need = ("vps", "sps", "pps") if hevc else ("sps", "pps")
                    if all(k in self.params for k in need):
                        self.ready.set()
            if aligned and au_has_vcl:
                self._emit_annexb(au, au_key, hevc)
                au, au_has_vcl, au_key = [], False, False
        if au and au_has_vcl:
            self._emit_annexb(au, au_key, hevc)

    def _emit_annexb(self, au, key, hevc):
        if key:
            # make sure every IDR is self-contained (clients joining mid-stream)
            types = {((n[0] >> 1) & 0x3F) if hevc else (n[0] & 0x1F) for n in au}
            need = (("vps", 32), ("sps", 33), ("pps", 34)) if hevc else (("sps", 7), ("pps", 8))
            prefix = [self.params[k] for k, t in need if t not in types and k in self.params]
            if prefix:
                au = prefix + au
        payloads = rtp.packetize_h265(au, self.mtu) if hevc else rtp.packetize_h264(au, self.mtu)
        if payloads:
            self._pace_and_emit(payloads, key)

    # JPEG ---------------------------------------------------------------------
    def _loop_jpeg(self, proc):
        splitter = JpegSplitter()
        while not self.stop_evt.is_set():
            data = self._read(proc)
            if not data:
                break
            for img in splitter.feed(data):
                try:
                    info = rtp.parse_jpeg(img)
                except ValueError as exc:
                    log.debug("bad jpeg: %s", exc)
                    continue
                self.params["jpeg"] = (info.width, info.height)
                self.ready.set()
                self._pace_and_emit(rtp.packetize_jpeg(info, self.mtu), True)


class SourceManager:
    """Creates encoders on demand, shares them, and stops idle ones."""

    def __init__(self, cfg):
        self.mtu = int(cfg.get("rtp_mtu", 1400))
        self.preset = cfg.get("x264_preset", "veryfast")
        self.threads = int(cfg.get("encoder_threads", 0))
        self.idle_timeout = float(cfg.get("encoder_idle_timeout", 15))
        self.loop = bool(cfg.get("loop", True))
        self.engine = cfg.get("_engine") or cfg.get("engine", "gstreamer")
        self.epoch = time.time()  # the shared 'live' timeline of every camera
        self.sources = {}
        self.gst = {}  # source -> gst.GstSource (shared by its encoders and snapshots)
        self.lock = threading.Lock()
        self.stop_evt = threading.Event()
        threading.Thread(target=self._reaper, name="src-reaper", daemon=True).start()

    def get(self, camera, vec):
        """One encoder per (source, effective settings), shared by all cameras using it,
        e.g. clones for stress tests. Changed settings simply map to a new encoder; the old one
        is stopped by the reaper once its last viewer is gone."""
        with self.lock:
            key = (camera.source, vec.stream_key())
            src = self.sources.get(key)
            if src and src.stop_evt.is_set():
                src = None
            if src is None:
                src = StreamSource(self, camera, vec)
                self.sources[key] = src
            src.idle_since = time.time()
            return src

    def gst_source(self, camera):
        with self.lock:
            g = self.gst.get(camera.source)
            if g is None or not g.alive:
                dur = camera.info.duration
                pos = (time.time() - self.epoch) % dur if dur > 1 else 0.0
                main = next(iter(camera.vecs.values()), None)   # agents encode like the main stream
                g = self.gst[camera.source] = gst.GstSource(self, camera.source, pos,
                                                            cbr=bool(main and main.cbr))
            g.idle_since = time.time()
            return g

    def stats(self):
        with self.lock:
            return [{"source": k[0], "label": "%s / %s" % s.key, "encoding": s.encoding,
                     "resolution": f"{s.vec.width}x{s.vec.height}", "fps": s.fps,
                     "frames": s.frames, "mbytes": round(s.bytes / 1e6, 2),
                     "consumers": s.consumer_count, "ready": s.ready.is_set(),
                     "error": None if s.ready.is_set() else s.failed}
                    for k, s in self.sources.items()]

    def _reaper(self):
        while not self.stop_evt.wait(2):
            now = time.time()
            with self.lock:
                for key, src in list(self.sources.items()):
                    if src.consumer_count == 0 and now - src.idle_since > self.idle_timeout:
                        log.info("%s/%s idle, stopping encoder", *src.key)
                        self.sources.pop(key).stop("idle")
                    elif (src.consumer_count and src.ready.is_set() and
                          now - src.last_wall > max(10.0, 5.0 / max(src.fps, 0.1))):
                        # watchdog: a frozen ffmpeg (stalled RTSP relay, disk hiccup) is killed;
                        # _run() restarts it and viewers resync on the next key frame
                        log.warning("%s/%s no frames for %.0fs, restarting encoder", *src.key,
                                    now - src.last_wall)
                        src.last_wall = now
                        p = src.proc
                        if p and p.poll() is None:
                            p.kill()
                for key, g in list(self.gst.items()):
                    n = g.branch_count()
                    if not g.alive or (n == 0 and now - g.idle_since > self.idle_timeout):
                        if g.alive:
                            log.info("%s: idle, stopping GStreamer source", key)
                        self.gst.pop(key).stop()
                    elif n and now - g.last_data > 15:
                        log.warning("%s: GStreamer source frozen for %.0fs, restarting", key,
                                    now - g.last_data)
                        self.gst.pop(key).stop()  # branches end -> encoders restart it

    def shutdown(self):
        self.stop_evt.set()
        with self.lock:
            for src in self.sources.values():
                src.stop("shutdown")
            self.sources.clear()
            for g in self.gst.values():
                g.stop()
            self.gst.clear()


# --------------------------------------------------------------------------- snapshots
_snap_cache = {}
_snap_lock = threading.Lock()


def snapshot(camera, vec, manager):
    """JPEG still of what the camera is 'seeing' right now (position in the looping file)."""
    key = (camera.source, vec.width if vec else 0, vec.height if vec else 0)
    now = time.time()
    with _snap_lock:
        hit = _snap_cache.get(key)
        if hit and now - hit[0] < 1.0:
            return hit[1]
    w = vec.width if vec else camera.info.width
    h = vec.height if vec else camera.info.height
    if manager.engine == "gstreamer" or gst.is_device(camera.source):
        jpg = _gst_snapshot(manager, camera, w, h)
        with _snap_lock:
            _snap_cache[key] = (now, jpg)
        return jpg
    epoch = manager.epoch
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin"]
    path = camera.source
    if not path.startswith(LIVE_PREFIXES) and camera.info.duration > 0:
        pos = (now - epoch) % camera.info.duration
        cmd += ["-ss", f"{pos:.3f}"]
    cmd += ["-i", path, "-frames:v", "1", "-vf", f"scale={w}:{h}:out_range=full,format=yuvj420p", "-q:v", "4", "-f", "image2",
            "-c:v", "mjpeg", "pipe:1"]
    out = subprocess.run(cmd, capture_output=True, timeout=15, creationflags=NO_WINDOW)
    if out.returncode != 0 or not out.stdout:
        raise RuntimeError(out.stderr.decode("utf-8", "replace")[-300:])
    with _snap_lock:
        _snap_cache[key] = (now, out.stdout)
    return out.stdout


def _gst_snapshot(manager, camera, w, h):
    # from the shared source process: same frame the streams show, webcam opened only once
    src = manager.gst_source(camera)
    branch = src.add(gst.snapshot_desc(w, h))
    splitter = JpegSplitter()
    deadline = time.time() + 15
    try:
        while time.time() < deadline:
            data = branch.read(0.5)
            if data == b"":
                raise RuntimeError(branch.error or src.error or "source ended")
            if data:
                imgs = splitter.feed(data)
                if imgs:
                    return imgs[0]
        raise RuntimeError(src.error or "timed out")
    finally:
        src.remove(branch)
