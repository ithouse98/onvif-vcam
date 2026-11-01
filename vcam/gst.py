"""GStreamer engine, main-process side: one vcam.gsthelper process per source, shared by every
encoder (branch) and snapshot of that source. See gsthelper.py for the wire protocol."""

import io
import json
import logging
import os
import queue
import secrets
import socket
import struct
import subprocess
import sys
import threading
import time
from collections import deque

from .util import NO_WINDOW

log = logging.getLogger("vcam.gst")

DEVICE_PREFIXES = ("screen://", "webcam://", "test://", "agent://")
_PKG_PARENT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def is_device(src):
    return src.startswith(DEVICE_PREFIXES)


def _python():
    exe = sys.executable
    # pythonw has no usable stdio; the console python is hidden by CREATE_NO_WINDOW anyway
    if os.path.basename(exe).lower() == "pythonw.exe":
        alt = os.path.join(os.path.dirname(exe), "python.exe")
        if os.path.exists(alt):
            exe = alt
    return exe


def helper_cmd(*args):
    return [_python(), "-m", "vcam.gsthelper", *args]


def _env():
    env = dict(os.environ)
    env["PYTHONPATH"] = _PKG_PARENT + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env.setdefault("GST_DEBUG", "1")
    return env


def needs_user_session(src):
    """Screen / webcam capture from a Windows service must run on the user's desktop."""
    if not str(src).startswith(("screen://", "webcam://")):
        return False
    from . import winsession
    return winsession.in_service_session()


class SockProc:
    """Popen-like view of a helper started in the user session, talking over a socket."""

    def __init__(self, conn, rfile, proc):
        self.conn, self.proc = conn, proc
        self.stdout = rfile
        self.stdin = conn.makefile("wb")
        self.stderr = io.BytesIO()   # session helpers send their log lines as events
        self.pid = proc.pid

    @property
    def returncode(self):
        return self.proc.poll()

    def poll(self):
        return self.proc.poll()

    def wait(self, timeout=None):
        return self.proc.wait(timeout)

    def kill(self):
        self.proc.kill()
        try:
            self.conn.close()
        except OSError:
            pass


def spawn(args, src=None):
    """Start a helper -> Popen-like object with binary stdin/stdout/stderr."""
    if src is not None and needs_user_session(src):
        from . import winsession
        sid = winsession.user_session()
        if sid is None:
            raise RuntimeError("no user is logged on to Windows: screen and webcam capture need a "
                               "logged-in desktop")
        lst = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        lst.bind(("127.0.0.1", 0))
        lst.listen(1)
        token = secrets.token_hex(12)
        env = {k: v for k, v in _env().items()
               if k in ("PYTHONPATH", "PYTHONHOME", "PYTHONUTF8", "PYTHONNOUSERSITE", "GST_DEBUG")}
        env["PATH"] = os.pathsep.join(p for p in (os.path.dirname(_python()),
                                                  os.path.join(os.path.dirname(_python()), "Library", "bin"))
                                      if os.path.isdir(p))
        proc = winsession.launch(helper_cmd(*args, "--connect", f"127.0.0.1:{lst.getsockname()[1]}",
                                            "--token", token), sid, env=env,
                                 cwd=os.path.dirname(_PKG_PARENT))
        try:
            lst.settimeout(40)
            while True:
                conn, _ = lst.accept()
                conn.settimeout(None)
                rfile = conn.makefile("rb")
                try:
                    if json.loads(rfile.readline(1024) or b"{}").get("token") == token:
                        return SockProc(conn, rfile, proc)
                except ValueError:
                    pass
                conn.close()
        except OSError:
            proc.kill()
            raise RuntimeError("the capture helper did not start in the user session")
        finally:
            lst.close()
    return subprocess.Popen(helper_cmd(*args), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, env=_env(), creationflags=NO_WINDOW, bufsize=0)


def run_helper(*args, timeout=30, src=None):
    """One-shot helper call (check / probe / devices) -> parsed JSON."""
    if src is not None and needs_user_session(src):
        p = spawn(list(args), src)
        box = {}
        t = threading.Thread(target=lambda: box.setdefault("out", p.stdout.read()), daemon=True)
        t.start()
        t.join(timeout)
        p.kill()
        text, err = (box.get("out") or b"").decode("utf-8", "replace"), ""
    else:
        out = subprocess.run(helper_cmd(*args), capture_output=True, text=True, timeout=timeout,
                             env=_env(), creationflags=NO_WINDOW, stdin=subprocess.DEVNULL)
        text, err = out.stdout, out.stderr
    try:
        return json.loads(text.strip().splitlines()[-1])
    except (ValueError, IndexError):
        raise RuntimeError((err or text or "gstreamer helper failed").strip()[-400:])


def available():
    """'' when the engine works, else the reason."""
    try:
        r = run_helper("check", timeout=60)
    except Exception as exc:  # noqa
        return str(exc).splitlines()[-1] if str(exc) else "GStreamer not available"
    return ("missing elements: " + ", ".join(r["missing"])) if r.get("missing") else ""


# --------------------------------------------------------------------------- branch descriptions
def _scale(vec, fmt="I420", fixed_rate=False):
    """fixed_rate: the source already runs at a fixed frame rate (screen capture). videorate then
    only drops frames, which needs no look-ahead: the default mode holds every frame until the next
    one arrives (one frame of delay). Not for variable-rate sources: drop-only asserts on those."""
    q = "queue max-size-buffers=5 max-size-bytes=0 max-size-time=0 leaky=downstream"
    # skip-to-first: a branch joins a source that may have run for minutes (second profile, new
    # viewer); by default videorate first fills that whole time with copies of the first frame,
    # which came out as minutes of delay
    if fixed_rate and vec.fps >= 1:   # max-rate is a whole number: below 1 fps use caps
        rate, fr = f"videorate skip-to-first=true drop-only=true max-rate={max(1, round(vec.fps))}", ""
    else:
        rate, fr = "videorate skip-to-first=true", f"framerate={_frac(vec.fps)},"
    return (f"{q} ! {rate} ! videoscale add-borders=false ! video/x-raw,format={fmt},"
            f"width={vec.width},height={vec.height},{fr}pixel-aspect-ratio=1/1")


def _frac(fps):
    from fractions import Fraction
    f = Fraction(fps).limit_denominator(1001)
    return f"{f.numerator}/{f.denominator}"


PRESETS = ("ultrafast", "superfast", "veryfast", "faster", "fast", "medium")


def quality_preset(preset, quality):
    """Encoder effort from the stream quality: High (>= 7) one step slower than the configured
    preset (better picture at the same bitrate), Low (<= 3) one step faster."""
    i = PRESETS.index(preset) if preset in PRESETS else 2
    q = float(quality)
    return PRESETS[min(len(PRESETS) - 1, i + 1) if q >= 7 else max(0, i - 1) if q <= 3 else i]


def jpeg_quality(quality):
    """ONVIF Quality 1..10 -> jpegenc quality (Low 2 -> 37, Medium 5 -> 59, High 8 -> 81)."""
    return int(round(30 + (max(1.0, min(10.0, float(quality))) - 1) * 65 / 9))


def jpeg_rate(vec):
    """MJPEG has no bitrate control of its own: the helper steers jpegenc's quality per frame
    toward the frame size the bitrate allows. CBR holds that size; VBR keeps the chosen quality
    and only lowers it while frames would exceed the limit."""
    if vec.encoding != "JPEG" or not vec.bitrate_kbps:
        return None
    return {"target": int(vec.bitrate_kbps * 1000 / 8 / max(vec.fps, 1)), "cbr": bool(vec.cbr),
            "q": jpeg_quality(vec.quality)}


def crf(quality):
    """VBR quality target from the ONVIF Quality (1..10, default 5 -> CRF 26)."""
    return int(round(33 - 1.5 * max(1.0, min(10.0, float(quality)))))


def x264_rc(cbr, br, quality=5.0):
    """x264enc rate control -> (properties, option-string suffix). CBR: constant rate, padded with
    filler (nal-hrd=cbr). VBR: constant quality (CRF) capped at the bitrate, so a still picture
    (a screen) costs almost nothing and motion still never exceeds the bitrate."""
    if cbr:
        return f"pass=cbr bitrate={br} vbv-buf-capacity=1000", ":nal-hrd=cbr"
    return f"pass=qual quantizer={crf(quality)} bitrate={br} vbv-buf-capacity=1000", ""


def branch_desc(vec, preset="veryfast", threads=0, fixed_rate=False):
    """-> (gst-launch branch, ffmpeg argv or None) for a transcoding encoder config."""
    sink = "appsink name=out max-buffers=100 drop=false"
    br = max(32, int(vec.bitrate_kbps))
    gop = max(1, int(vec.gop))
    scale = _scale(vec, fixed_rate=fixed_rate)
    preset = quality_preset(preset, vec.quality)
    if vec.encoding == "H264":
        prof = {"baseline": "baseline", "main": "main", "high": "high",
                "extended": "main"}.get(vec.profile.lower(), "main")
        rc, hrd = x264_rc(vec.cbr, br, vec.quality)
        opts = f"min-keyint={gop}:scenecut=0:repeat-headers=1{hrd}"
        enc = (f"x264enc tune=zerolatency speed-preset={preset} {rc} key-int-max={gop} bframes=0 "
               f"byte-stream=true aud=true option-string=\"{opts}\""
               + (f" threads={threads}" if threads else ""))
        return (f"{scale} ! {enc} ! video/x-h264,profile={prof} ! h264parse config-interval=-1 ! "
                f"video/x-h264,stream-format=byte-stream,alignment=au ! {sink}"), None
    if vec.encoding == "JPEG":
        return f"{scale} ! jpegenc name=jenc quality={jpeg_quality(vec.quality)} ! {sink}", None
    # H.265: GStreamer scales / paces, libx265 (ffmpeg) encodes
    from .source import encoder_args
    ff = (["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "yuv4mpegpipe", "-i", "pipe:0"]
          + encoder_args(vec, preset, threads) + ["pipe:1"])
    return f"{scale} ! y4menc ! {sink}", ff


def snapshot_desc(w, h):
    return (f"queue max-size-buffers=2 leaky=downstream ! videoscale add-borders=false ! "
            f"video/x-raw,width={w},height={h},pixel-aspect-ratio=1/1 ! "
            "jpegenc quality=85 snapshot=true ! appsink name=out")


# --------------------------------------------------------------------------- process
class Branch:
    """Reader end of one encoder branch; quacks like the Popen object StreamSource expects."""

    def __init__(self, proc, bid):
        self.proc = proc
        self.id = bid
        self.q = queue.Queue(maxsize=600)
        self.ended = False
        self.error = None
        self.arrival = 0.0      # when the last data read() returned came out of the helper

    def push(self, data):
        try:
            self.q.put_nowait((time.time(), data))
        except queue.Full:  # consumer stuck: drop rather than block the shared reader
            pass

    def end(self, error=None):
        if not self.ended:
            self.ended = True
            self.error = error
            while True:  # never block: callers may hold the source manager lock
                try:
                    self.q.put_nowait((time.time(), b""))
                    return
                except queue.Full:
                    try:
                        self.q.get_nowait()
                    except queue.Empty:
                        pass

    def read(self, timeout=None):
        if self.ended and self.q.empty():
            return b""
        try:
            self.arrival, data = self.q.get(timeout=timeout)
            return data
        except queue.Empty:
            return None

    # Popen-like
    def poll(self):
        return 0 if self.ended else None

    def kill(self):
        self.proc.remove(self)

    def wait(self, timeout=None):
        return 0


class GstSource:
    """One helper process for one source; restarts it while branches need it."""

    def __init__(self, manager, source, start_pos=0.0, cbr=False):
        self.manager = manager
        self.source = source
        self.cbr = cbr          # agent sources: rate control of the agent's own encoder
        self.start_pos = start_pos
        self.lock = threading.Lock()
        self.branches = {}
        self.next_id = 1
        self.proc = None
        self.info = None
        self.alive = True
        self.idle_since = time.time()
        self.last_data = time.time()     # last real output of the helper
        self.added_at = 0.0              # last branch added (watchdog grace)
        self.stderr_tail = deque(maxlen=20)
        self.error = None
        self._start()

    def _start(self):
        src = self.source
        if src.startswith("agent://"):  # remote PC: the agent hub relays its stream locally
            from . import agents
            if agents.HUB is None:
                raise RuntimeError("agent hub is not running")
            src = agents.HUB.open_stream(src, cbr=self.cbr)
        log.info("%s: starting GStreamer source%s", self.source,
                 " in the user session" if needs_user_session(src) else "")
        self.proc = spawn(["run", src, "--start", f"{self.start_pos:.3f}"], src)
        self.last_data = time.time()
        threading.Thread(target=self._reader, args=(self.proc,), daemon=True,
                         name="gst-" + os.path.basename(self.source)).start()
        threading.Thread(target=self._stderr, args=(self.proc,), daemon=True).start()

    def _send(self, cmd):
        try:
            self.proc.stdin.write((json.dumps(cmd) + "\n").encode())
            self.proc.stdin.flush()
        except (OSError, ValueError):
            pass  # process died; the reader notices and restarts it

    def _stderr(self, proc):
        for line in iter(proc.stderr.readline, b""):
            s = line.decode("utf-8", "replace").strip()
            if s:
                self.stderr_tail.append(s)
                log.debug("gst[%s]: %s", self.source, s)

    def _reader(self, proc):
        f = proc.stdout
        while True:
            hdr = _read_exact(f, 6)
            if not hdr:
                break
            bid, n = struct.unpack(">HI", hdr)
            data = _read_exact(f, n)
            if data is None:
                break
            self.last_data = time.time()
            if bid == 0:
                self._event(json.loads(data))
                continue
            b = self.branches.get(bid)
            if b:
                b.push(data)
        proc.wait()
        if not self.alive:
            return
        tail = " | ".join(self.stderr_tail)
        self.error = self.error or tail or f"exit code {proc.returncode}"
        log.warning("%s: GStreamer source stopped (%s)", self.source, self.error)
        with self.lock:
            branches = list(self.branches.values())
            self.branches.clear()
        for b in branches:  # StreamSource restarts its encoder (with backoff) -> new process
            b.end(self.error)
        self.alive = False

    def _event(self, ev):
        t = ev.get("type")
        if t == "caps":
            self.info = ev
            self.error = None
        elif t in ("eos", "branch_error"):
            b = self.branches.get(int(ev.get("id") or 0))
            if b:
                if t == "branch_error":
                    log.warning("%s: encoder branch failed: %s", self.source, ev.get("msg"))
                b.end(ev.get("msg"))
        elif t == "error":
            self.error = ev.get("msg")
        elif t == "log":  # helper in the user session: no stderr pipe
            self.stderr_tail.append(str(ev.get("msg")))
            log.debug("gst[%s]: %s", self.source, ev.get("msg"))

    def add(self, desc, ffmpeg=None, jpeg=None):
        with self.lock:
            bid = self.next_id
            self.next_id = self.next_id % 65000 + 1
            b = Branch(self, bid)
            b.desc, b.ffmpeg = desc, ffmpeg
            self.branches[bid] = b
            # an idle source sends nothing: give a new branch time before calling the source frozen
            self.added_at = time.time()
            self._send({"op": "add", "id": bid, "desc": desc, "ffmpeg": ffmpeg, "jpeg": jpeg})
            return b

    def remove(self, b):
        with self.lock:
            if self.branches.pop(b.id, None) is not None:
                self._send({"op": "remove", "id": b.id})
            if not self.branches:
                self.idle_since = time.time()
        b.end()

    def branch_count(self):
        with self.lock:
            return len(self.branches)

    def stop(self):
        self.alive = False
        p = self.proc
        if p and p.poll() is None:
            try:
                p.stdin.close()
                p.wait(2)
            except Exception:  # noqa
                pass
            if p.poll() is None:
                p.kill()
        with self.lock:
            branches = list(self.branches.values())
            self.branches.clear()
        for b in branches:
            b.end("stopped")


def _read_exact(f, n):
    buf = bytearray()
    while len(buf) < n:
        chunk = f.read(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return bytes(buf)


def probe(src):
    r = run_helper("probe", src, timeout=40, src=src)
    if "error" in r:
        raise RuntimeError(f"{src}: {r['error']}")
    return r


def devices():
    return run_helper("devices", timeout=30, src="screen://")
