"""GStreamer engine, main-process side: one vcam.gsthelper process per source, shared by every
encoder (branch) and snapshot of that source. See gsthelper.py for the wire protocol."""

import json
import logging
import os
import queue
import struct
import subprocess
import sys
import threading
import time
from collections import deque

from .util import NO_WINDOW

log = logging.getLogger("vcam.gst")

DEVICE_PREFIXES = ("screen://", "webcam://", "test://")
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


def run_helper(*args, timeout=30):
    """One-shot helper call (check / probe / devices) -> parsed JSON."""
    out = subprocess.run(helper_cmd(*args), capture_output=True, text=True, timeout=timeout,
                         env=_env(), creationflags=NO_WINDOW, stdin=subprocess.DEVNULL)
    try:
        return json.loads(out.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        raise RuntimeError((out.stderr or out.stdout or "gstreamer helper failed").strip()[-400:])


def available():
    """'' when the engine works, else the reason."""
    try:
        r = run_helper("check", timeout=60)
    except Exception as exc:  # noqa
        return str(exc).splitlines()[-1] if str(exc) else "GStreamer not available"
    return ("missing elements: " + ", ".join(r["missing"])) if r.get("missing") else ""


# --------------------------------------------------------------------------- branch descriptions
def _scale(vec, fmt="I420"):
    q = "queue max-size-buffers=5 max-size-bytes=0 max-size-time=0 leaky=downstream"
    return (f"{q} ! videorate ! videoscale add-borders=false ! video/x-raw,format={fmt},"
            f"width={vec.width},height={vec.height},framerate={_frac(vec.fps)},"
            "pixel-aspect-ratio=1/1")


def _frac(fps):
    from fractions import Fraction
    f = Fraction(fps).limit_denominator(1001)
    return f"{f.numerator}/{f.denominator}"


def branch_desc(vec, preset="veryfast", threads=0):
    """-> (gst-launch branch, ffmpeg argv or None) for a transcoding encoder config."""
    sink = "appsink name=out max-buffers=100 drop=false"
    br = max(32, int(vec.bitrate_kbps))
    gop = max(1, int(vec.gop))
    if vec.encoding == "H264":
        prof = {"baseline": "baseline", "main": "main", "high": "high",
                "extended": "main"}.get(vec.profile.lower(), "main")
        opts = f"min-keyint={gop}:scenecut=0:repeat-headers=1" + (":nal-hrd=cbr" if vec.cbr else "")
        enc = (f"x264enc tune=zerolatency speed-preset={preset} bitrate={br} "
               f"vbv-buf-capacity={1000 if vec.cbr else 2000} key-int-max={gop} bframes=0 "
               f"byte-stream=true aud=true option-string=\"{opts}\""
               + (f" threads={threads}" if threads else ""))
        return (f"{_scale(vec)} ! {enc} ! video/x-h264,profile={prof} ! h264parse config-interval=-1 ! "
                f"video/x-h264,stream-format=byte-stream,alignment=au ! {sink}"), None
    if vec.encoding == "JPEG":
        q = int(round(30 + (max(1.0, min(10.0, vec.quality)) - 1) * 65 / 9))
        return f"{_scale(vec)} ! jpegenc quality={q} ! {sink}", None
    # H.265: GStreamer scales / paces, libx265 (ffmpeg) encodes
    from .source import encoder_args
    ff = (["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "yuv4mpegpipe", "-i", "pipe:0"]
          + encoder_args(vec, preset, threads) + ["pipe:1"])
    return f"{_scale(vec)} ! y4menc ! {sink}", ff


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

    def push(self, data):
        try:
            self.q.put_nowait(data)
        except queue.Full:  # consumer stuck: drop rather than block the shared reader
            pass

    def end(self, error=None):
        if not self.ended:
            self.ended = True
            self.error = error
            while True:  # never block: callers may hold the source manager lock
                try:
                    self.q.put_nowait(b"")
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
            return self.q.get(timeout=timeout)
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

    def __init__(self, manager, source, start_pos=0.0):
        self.manager = manager
        self.source = source
        self.start_pos = start_pos
        self.lock = threading.Lock()
        self.branches = {}
        self.next_id = 1
        self.proc = None
        self.info = None
        self.alive = True
        self.idle_since = time.time()
        self.last_data = time.time()
        self.stderr_tail = deque(maxlen=20)
        self.error = None
        self._start()

    def _start(self):
        cmd = helper_cmd("run", self.source, "--start", f"{self.start_pos:.3f}")
        log.info("%s: starting GStreamer source", self.source)
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, env=_env(), creationflags=NO_WINDOW,
                                     bufsize=0)
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

    def add(self, desc, ffmpeg=None):
        with self.lock:
            bid = self.next_id
            self.next_id = self.next_id % 65000 + 1
            b = Branch(self, bid)
            b.desc, b.ffmpeg = desc, ffmpeg
            self.branches[bid] = b
            self._send({"op": "add", "id": bid, "desc": desc, "ffmpeg": ffmpeg})
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
    r = run_helper("probe", src, timeout=40)
    if "error" in r:
        raise RuntimeError(f"{src}: {r['error']}")
    return r


def devices():
    return run_helper("devices", timeout=30)
