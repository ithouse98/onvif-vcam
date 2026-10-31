"""GStreamer worker process, one per video source (started with the same Python by vcam.gst).

    python -m vcam.gsthelper run SOURCE [--start SECONDS] [--no-loop]
    python -m vcam.gsthelper probe SOURCE
    python -m vcam.gsthelper devices
    python -m vcam.gsthelper check
    python -m vcam.gsthelper push SOURCE --to HOST:PORT --token T [--auth A] [--bitrate K]
  any command + --connect HOST:PORT --token T : stdin/stdout over that TCP connection instead of
  pipes (the Windows service starts screen helpers in the user's session this way)

`push` (screen agent): captures SOURCE, encodes H.264 in MPEG-TS and sends it to a VCam agent hub.
`run` opens SOURCE once (decode / capture -> I420 -> tee) so every stream of a camera shares
one decoder, one webcam handle and one timeline. Encoder branches (gst-launch syntax ending in an
appsink named "out") are added / removed at runtime with JSON lines on stdin:
    {"op": "add", "id": 3, "desc": "queue ! ... ! appsink name=out", "ffmpeg": null | [argv]}
    {"op": "remove", "id": 3}
With "ffmpeg" the branch output (y4m) is piped through that encoder (libx265: GStreamer builds
have no software H.265 encoder). stdout carries frames: struct ">HI" (branch id, length) +
payload; id 0 is a JSON event (caps, eos, branch_error, error).

SOURCE: file path or URL (rtsp/http/...), screen://[N|all|N+M+...][?fps=F], webcam://[N|name],
test://[pattern]. screen://all = every monitor as arranged in Windows display settings,
screen://0+1 = the listed monitors side by side, both composed into one picture.
"""

import json
import os
import struct
import subprocess
import sys
import threading
import time
import urllib.parse

WIN = os.name == "nt"
if WIN:  # unactivated conda layout: DLLs, typelibs and plugins live under Library\
    _lib = os.path.join(sys.prefix, "Library")
    os.environ["PATH"] = os.path.join(_lib, "bin") + os.pathsep + os.environ.get("PATH", "")
    os.environ.setdefault("GI_TYPELIB_PATH", os.path.join(_lib, "lib", "girepository-1.0"))
    try:
        os.add_dll_directory(os.path.join(_lib, "bin"))
    except (AttributeError, OSError):
        pass

import gi  # noqa: E402

gi.require_version("Gst", "1.0")
from gi.repository import GLib, Gst  # noqa: E402

NO_WINDOW = 0x08000000 if WIN else 0
DEVICE_API_PREF = ("mediafoundation", "v4l2", "ks", "directshow", "avfoundation")


# --------------------------------------------------------------------------- output
class Out:
    def __init__(self):
        self.f = sys.stdout.buffer
        self.lock = threading.Lock()
        global OUT
        OUT = self

    def frame(self, bid, data):
        try:
            with self.lock:
                self.f.write(struct.pack(">HI", bid, len(data)))
                self.f.write(data)
                self.f.flush()
        except (BrokenPipeError, OSError, ValueError):
            os._exit(0)  # parent is gone

    def event(self, **ev):
        self.frame(0, json.dumps(ev).encode())


class RawSockOut:
    """push mode: branch output straight to the agent hub socket."""

    def __init__(self, sock):
        self.sock = sock
        self.lock = threading.Lock()

    def frame(self, _bid, data):
        try:
            with self.lock:
                self.sock.sendall(data)
        except OSError:
            log("hub connection closed")
            os._exit(0)

    def event(self, **ev):
        if ev.get("type") in ("error", "branch_error"):
            log(f"error: {ev.get('msg')}")
            os._exit(1)


OUT = None          # framed output of `run`
CONNECTED = False   # stdin/stdout are a TCP connection (session helper): logs travel as events


def log(msg):
    if CONNECTED and OUT is not None:
        try:
            OUT.event(type="log", msg=msg)
        except Exception:  # noqa
            pass
        return
    try:
        sys.stderr.write(msg + "\n")
        sys.stderr.flush()
    except (OSError, ValueError, AttributeError):
        pass


# --------------------------------------------------------------------------- sources
def parse_source(src):
    """-> (kind, arg, params) for device URIs, ("file"|"uri", src, {}) otherwise."""
    for kind in ("screen", "webcam", "test"):
        if src.startswith(kind + "://"):
            u = urllib.parse.urlsplit(src)
            arg = urllib.parse.unquote(u.netloc + u.path).strip("/")
            return kind, arg, dict(urllib.parse.parse_qsl(u.query))
    if "://" in src and not (WIN and len(src) > 1 and src[1] == ":"):
        return "uri", src, {}
    return "file", src, {}


def video_devices():
    """Webcams, one entry per physical device (best API first)."""
    mon = Gst.DeviceMonitor.new()
    mon.add_filter("Video/Source", None)
    mon.start()
    devs = mon.get_devices() or []
    mon.stop()
    best = {}
    for d in devs:
        props = d.get_properties()
        api = (props.get_string("device.api") if props else None) or ""
        name = d.get_display_name()
        rank = DEVICE_API_PREF.index(api) if api in DEVICE_API_PREF else len(DEVICE_API_PREF)
        best.setdefault(name, []).append((rank, api, d))
    out = []
    for name, cands in best.items():
        cands.sort(key=lambda c: c[0])
        out.append({"name": name, "apis": [c[1] for c in cands], "devices": [c[2] for c in cands]})
    return out


def _prop(props, key, default=None):
    if props is None or not props.has_field(key):
        return default
    try:
        return props.get_value(key)
    except TypeError:
        return default


def monitors():
    """[{index, name, width, height, x, y, primary, hmonitor}] in capture-index order. x/y are
    desktop coordinates (D3D11 provider on Windows)."""
    fake = os.environ.get("VCAM_TEST_MONITORS")  # tests: "1280x720+0+0,640x480+1280+0"
    if fake:
        out = []
        for i, m in enumerate(fake.split(",")):
            wh, x, y = m.split("+")
            w, h = wh.split("x")
            out.append({"index": i, "name": f"Test {i}", "width": int(w), "height": int(h),
                        "x": int(x), "y": int(y), "primary": i == 0, "hmonitor": None})
        return out
    mon = Gst.DeviceMonitor.new()
    mon.add_filter("Source/Monitor", None)
    mon.start()
    devs = mon.get_devices() or []
    mon.stop()
    out = []
    for i, d in enumerate(devs):
        caps = d.get_caps()
        w = h = 0
        if caps and caps.get_size():
            s = caps.get_structure(0)
            w, h = s.get_int("width")[1], s.get_int("height")[1]
        p = d.get_properties()
        left, top = _prop(p, "desktop.coordinates.left"), _prop(p, "desktop.coordinates.top")
        right, bottom = _prop(p, "desktop.coordinates.right"), _prop(p, "desktop.coordinates.bottom")
        if None not in (left, top, right, bottom):
            w, h = int(right) - int(left), int(bottom) - int(top)
        out.append({"index": i, "name": d.get_display_name(), "width": w, "height": h,
                    "x": int(left or 0), "y": int(top or 0),
                    "primary": bool(_prop(p, "device.primary", i == 0)),
                    "hmonitor": _prop(p, "device.hmonitor")})
    return out


def _monitor_srcs(m, fps, test=False, composite=False):
    """Capture alternatives for one monitor (best first). composite: the branch feeds a
    compositor; on real Windows the D3D11 capture must then download its GPU frames explicitly
    (with system-memory caps right after the source the composite pipeline ends at once)."""
    caps = f"video/x-raw,framerate={fps}/1"
    if test:  # VCAM_TEST_MONITORS
        return [f"videotestsrc is-live=true pattern={'ball' if m['index'] == 0 else 'smpte'} ! "
                f"video/x-raw,width={m['width']},height={m['height']},framerate={fps}/1"]
    c = []
    if has("d3d11screencapturesrc"):
        sel = (f"monitor-handle={int(m['hmonitor'])}" if m.get("hmonitor")
               else f"monitor-index={m['index']}")
        if composite and has("d3d11download"):
            c.append(f"d3d11screencapturesrc {sel} show-cursor=true ! "
                     f"video/x-raw(memory:D3D11Memory),framerate={fps}/1 ! d3d11download")
        else:
            c.append(f"d3d11screencapturesrc {sel} show-cursor=true ! {caps}")
    if has("gdiscreencapsrc"):
        c.append(f"gdiscreencapsrc monitor={m['index']} cursor=true ! {caps}")
    return c


def composite_desc(parts, srcs, fps):
    """parts: [(monitor, x, y)], srcs: one launch fragment per part -> one pipeline fragment whose
    last chain is the compositor (the caller appends '! tail')."""
    x0 = min(x for _, x, _ in parts)
    y0 = min(y for _, _, y in parts)
    w = max(x - x0 + m["width"] for m, x, _ in parts)
    h = max(y - y0 + m["height"] for m, _, y in parts)
    w, h = w + w % 2, h + h % 2  # I420 / x264 need even sizes
    branches, pads = [], []
    for i, ((m, x, y), src) in enumerate(zip(parts, srcs)):
        # no ignore-inactive-pads / leaky queues: until the first desktop frame arrives every pad
        # is "inactive" and the compositor would end the stream (seen on real Windows)
        branches.append(f"{src} ! videoconvert ! queue ! mix.sink_{i}")
        pads.append(f"sink_{i}::xpos={x - x0} sink_{i}::ypos={y - y0}")
    return (" ".join(branches) + " compositor name=mix background=black "
            + " ".join(pads) + f" ! video/x-raw,width={w},height={h},framerate={fps}/1")


def screen_candidates(arg, fps):
    test = bool(os.environ.get("VCAM_TEST_MONITORS"))
    caps = f"video/x-raw,framerate={fps}/1"
    multi = arg == "all" or "+" in arg
    if not WIN and not test:
        if sys.platform == "darwin":
            return [f"avfvideosrc capture-screen=true ! {caps}"]
        # X11: ximagesrc already covers the whole screen (every monitor) in one picture
        c = []
        if has("ximagesrc"):
            c.append(f"ximagesrc use-damage=false show-pointer=true ! videorate ! {caps}")
        if has("pipewiresrc"):
            c.append(f"pipewiresrc ! videorate ! {caps}")
        return c
    mons = monitors()
    if not multi:
        idx = int(arg) if arg.lstrip("-").isdigit() else -1
        if idx >= 0 and mons and idx >= len(mons):
            raise RuntimeError(f"monitor {idx} not found ({len(mons)} monitor(s))")
        if idx < 0:  # primary
            prim = [m for m in mons if m["primary"]]
            if prim:
                return _monitor_srcs(prim[0], fps, test)
            return [f"d3d11screencapturesrc show-cursor=true ! {caps}",
                    f"gdiscreencapsrc cursor=true ! {caps}"]
        return _monitor_srcs(mons[idx], fps, test) if mons else \
            [f"d3d11screencapturesrc monitor-index={idx} show-cursor=true ! {caps}",
             f"gdiscreencapsrc monitor={idx} cursor=true ! {caps}"]
    if not mons:
        raise RuntimeError("no monitors found for a multi-monitor capture")
    if arg == "all":  # desktop layout as arranged in Windows display settings
        parts = [(m, m["x"], m["y"]) for m in mons]
    else:  # listed monitors side by side, top aligned
        parts, x = [], 0
        for tok in arg.split("+"):
            if not tok.strip().isdigit() or int(tok) >= len(mons):
                raise RuntimeError(f"monitor '{tok}' not found ({len(mons)} monitor(s))")
            m = mons[int(tok)]
            parts.append((m, x, 0))
            x += m["width"]
    per = [_monitor_srcs(m, fps, test, composite=True) for m, _, _ in parts]
    n = min(len(p) for p in per)  # same capture method for every monitor, best first
    return [composite_desc(parts, [p[k] for p in per], fps) for k in range(n)]


def has(name):
    return Gst.ElementFactory.find(name) is not None


def source_candidates(src):
    """Alternatives to try in order: ('desc', launch_string) or ('device', Gst.Device)."""
    kind, arg, params = parse_source(src)
    if kind == "file":
        return [("desc", f'uridecodebin name=dec uri="{Gst.filename_to_uri(os.path.abspath(arg))}" '
                         'caps="video/x-raw(ANY)" expose-all-streams=false')], False
    if kind == "uri" and arg.startswith("tcp://"):  # agent stream relayed by the hub (MPEG-TS)
        host, _, port = arg[6:].strip("/").rpartition(":")
        return [("desc", f"tcpclientsrc host={host} port={port} ! decodebin")], True
    if kind == "uri":
        return [("desc", f'uridecodebin name=dec uri="{arg}" caps="video/x-raw(ANY)" '
                         'expose-all-streams=false')], True
    if kind == "test":
        pat = arg or "ball"
        return [("desc", f"videotestsrc is-live=true pattern={pat} ! "
                         "video/x-raw,width=1280,height=720,framerate=25/1")], True
    if kind == "screen":
        fps = int(float(params.get("fps", 25)))
        c = [("desc", d) for d in screen_candidates(arg.lower().replace(" ", ""), fps)]
        if not c:
            raise RuntimeError("no screen capture element in this GStreamer build")
        return c, True
    # webcam
    devs = video_devices()
    if not devs:
        if not WIN and arg.startswith("/dev/"):
            return [("desc", f"v4l2src device={arg}")], True
        raise RuntimeError("no webcam / USB camera found")
    if arg == "" or arg.isdigit():
        i = int(arg or 0)
        if i >= len(devs):
            raise RuntimeError(f"webcam index {i} not found ({len(devs)} camera(s) connected)")
        d = devs[i]
    else:
        match = [d for d in devs if d["name"] == arg] or \
                [d for d in devs if arg.lower() in d["name"].lower()]
        if not match:
            raise RuntimeError(f"webcam '{arg}' not found; connected: "
                               + ", ".join(d["name"] for d in devs))
        d = match[0]
    return [("device", dev) for dev in d["devices"]], True


def demote_hw_decoders():
    # servers / VMs often have no usable GPU; a failing D3D11/NV/QSV decoder stalls decodebin
    reg = Gst.Registry.get()
    for f in reg.get_feature_list(Gst.ElementFactory):
        k = f.get_metadata("klass") or ""
        if "Decoder" in k and "Video" in k and "Hardware" in k:
            f.set_rank(Gst.Rank.NONE)


# --------------------------------------------------------------------------- worker
class Worker:
    def __init__(self, src, out, start=0.0, loop=True, probe=False, control=True, branch=None):
        self.control = control and not probe   # read branch commands from stdin
        self.initial_branch = branch
        self.src = src
        self.out = out
        self.start_pos = start
        self.loop = loop
        self.probe_mode = probe
        self.candidates, self.live = source_candidates(src)
        self.ml = GLib.MainLoop()
        self.pipeline = None
        self.tee = None
        self.branches = {}      # id -> dict(bin, pad, sink, ff)
        self.got_buffer = False
        self.seeked = False
        self._anchor = None     # (wall, pts) pacing reference for files
        self._last_pts = 0.0
        self.result = None
        self.exit_code = 0

    # ---------------------------------------------------------------- pipeline
    def build(self):
        what, val = self.candidates.pop(0)
        tail = "videoconvert ! video/x-raw,format=I420 ! tee name=t allow-not-linked=true"
        if what == "desc":
            p = Gst.parse_launch(f"{val} ! {tail}")
        else:  # Gst.Device: decodebin also covers MJPEG webcams
            p = Gst.parse_launch(f"decodebin name=devdec ! {tail}")
            el = val.create_element("cam")
            p.add(el)
            if not el.link(p.get_by_name("devdec")):
                raise RuntimeError("cannot link camera element")
        dec = p.get_by_name("dec")
        if dec is not None:
            dec.connect("source-setup", self._source_setup)
        self.pipeline = p
        self.tee = p.get_by_name("t")
        pad = self.tee.get_static_pad("sink")
        pad.add_probe(Gst.PadProbeType.BUFFER | Gst.PadProbeType.EVENT_DOWNSTREAM, self._tee_probe)
        bus = p.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self._on_message)

    @staticmethod
    def _source_setup(_dec, source):
        for prop, val in (("protocols", "tcp"), ("latency", 200)):
            if source.find_property(prop) is not None:
                try:
                    source.set_property(prop, val)
                except TypeError:
                    pass

    def _tee_probe(self, pad, info):
        if info.type & Gst.PadProbeType.BUFFER:
            if not self.got_buffer:
                self.got_buffer = True
                GLib.idle_add(self._first_buffer, pad)
            elif not self.live:
                self._pace(info.get_buffer().pts)
            return Gst.PadProbeReturn.OK
        ev = info.get_event()
        if ev is not None and ev.type == Gst.EventType.EOS:
            GLib.idle_add(self._source_eos)
            return Gst.PadProbeReturn.DROP  # branches keep running across loops
        return Gst.PadProbeReturn.OK

    def _first_buffer(self, pad):
        caps = pad.get_current_caps()
        s = caps.get_structure(0) if caps else None
        w = s.get_int("width")[1] if s else 0
        h = s.get_int("height")[1] if s else 0
        ok, num, den = s.get_fraction("framerate") if s else (False, 0, 1)
        fps = num / den if ok and num > 0 and den else 0.0
        dur = 0.0
        if not self.live:
            ok, d = pad.peer_query_duration(Gst.Format.TIME)  # no sinks yet: ask upstream
            dur = d / Gst.SECOND if ok and d > 0 else 0.0
        info = {"width": w, "height": h, "fps": round(fps, 3), "duration": round(dur, 3),
                "live": self.live}
        if self.probe_mode:
            self.result = info
            self.ml.quit()
            return False
        self.out.event(type="caps", **info)
        if not self.live and not self.seeked and self.start_pos > 0.5 and \
                (not dur or self.start_pos < dur - 1):
            self.seeked = True
            self.pipeline.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT,
                                      int(self.start_pos * Gst.SECOND))
        return False

    def _pace(self, pts):
        """Release decoded file frames in real time. Plain wall clock vs. PTS, re-anchored when
        PTS jumps (loop / seek): GStreamer's own sync (clocksync, sync=true sinks) bursts after
        the flushing loop seek in a sink-less pipeline, and non-flushing SEGMENT loops stall
        videorate in the branches."""
        if pts == Gst.CLOCK_TIME_NONE:
            return
        t = pts / Gst.SECOND
        now = time.monotonic()
        a = self._anchor
        if a is None or t < self._last_pts or t - self._last_pts > 2 or a[0] + t - a[1] < now - 1:
            self._anchor = a = (now, t)
        self._last_pts = t
        delay = a[0] + (t - a[1]) - now
        if delay > 0:
            time.sleep(delay)

    def _source_eos(self):
        if self.loop and not self.live:
            self.pipeline.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH, 0)
        else:
            log("source ended")
            self.exit_code = 0
            self.ml.quit()
        return False

    def _on_message(self, _bus, msg):
        t = msg.type
        if t == Gst.MessageType.ERROR:
            err, dbg = msg.parse_error()
            bid = self._branch_of(msg.src)
            if bid is not None:
                log(f"branch {bid} error: {err.message} ({dbg})")
                self.out.event(type="branch_error", id=bid, msg=err.message)
                self.remove(bid)
                return
            log(f"error: {err.message} ({dbg})")
            if not self.got_buffer and self.candidates:
                log("trying next capture method")
                GLib.idle_add(self._restart)
                return
            if not self.probe_mode:
                self.out.event(type="error", msg=err.message)
            self.result = {"error": err.message}
            self.exit_code = 1
            self.ml.quit()
        elif t == Gst.MessageType.WARNING:
            w, _ = msg.parse_warning()
            log(f"warning: {w.message}")

    def _restart(self):
        self.pipeline.set_state(Gst.State.NULL)
        self.pipeline.get_bus().remove_signal_watch()
        self.build()
        self.pipeline.set_state(Gst.State.PLAYING)
        return False

    def _branch_of(self, obj):
        while obj is not None:
            for bid, b in self.branches.items():
                if obj is b["bin"]:
                    return bid
            obj = obj.get_parent()
        return None

    # ---------------------------------------------------------------- branches
    def add(self, bid, desc, ffmpeg=None):
        if bid in self.branches:
            return
        b = Gst.parse_bin_from_description(desc, True)
        sink = b.get_by_name("out")
        sink.set_property("emit-signals", True)
        sink.set_property("sync", False)
        sink.set_property("async", False)  # joining a PLAYING pipeline must not re-preroll it
        ff = None
        if ffmpeg:
            ff = subprocess.Popen(ffmpeg, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  creationflags=NO_WINDOW, bufsize=0)
            threading.Thread(target=self._ff_reader, args=(bid, ff), daemon=True).start()
        entry = {"bin": b, "pad": None, "sink": sink, "ff": ff}
        sink.connect("new-sample", self._on_sample, bid, entry)
        sink.connect("eos", lambda *_: self.out.event(type="eos", id=bid))
        self.pipeline.add(b)
        pad = self.tee.request_pad_simple("src_%u")
        pad.link(b.get_static_pad("sink"))
        entry["pad"] = pad
        self.branches[bid] = entry
        b.sync_state_with_parent()

    def _on_sample(self, sink, bid, entry):
        sample = sink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.EOS
        buf = sample.get_buffer()
        data = buf.extract_dup(0, buf.get_size())
        ff = entry["ff"]
        if ff is None:
            self.out.frame(bid, data)
        else:
            try:
                ff.stdin.write(data)
            except (OSError, ValueError):
                return Gst.FlowReturn.FLUSHING
        return Gst.FlowReturn.OK

    def _ff_reader(self, bid, ff):
        while True:
            data = ff.stdout.read(65536)
            if not data:
                break
            self.out.frame(bid, data)
        if ff.wait() not in (0, None) and bid in self.branches:
            self.out.event(type="branch_error", id=bid, msg=f"ffmpeg exited with {ff.returncode}")

    def remove(self, bid):
        entry = self.branches.pop(bid, None)
        if entry is None:
            return
        if entry["ff"]:
            try:
                entry["ff"].kill()
            except OSError:
                pass
        pad = entry["pad"]

        def finish():
            entry["bin"].set_state(Gst.State.NULL)
            self.pipeline.remove(entry["bin"])
            return False

        def idle(p, _info):
            p.unlink(entry["bin"].get_static_pad("sink"))
            self.tee.release_request_pad(p)
            GLib.idle_add(finish)
            return Gst.PadProbeReturn.REMOVE

        pad.add_probe(Gst.PadProbeType.IDLE, idle)

    # ---------------------------------------------------------------- control
    def _stdin(self):
        for line in sys.stdin:
            line = line.strip()
            if line:
                try:
                    GLib.idle_add(self._command, json.loads(line))
                except ValueError:
                    log(f"bad command: {line[:200]}")
        GLib.idle_add(self.ml.quit)  # parent closed the pipe

    def _command(self, cmd):
        try:
            if cmd.get("op") == "add":
                self.add(int(cmd["id"]), cmd["desc"], cmd.get("ffmpeg"))
            elif cmd.get("op") == "remove":
                self.remove(int(cmd["id"]))
        except Exception as exc:  # noqa - report, keep the shared source running
            log(f"command {cmd.get('op')} {cmd.get('id')} failed: {exc}")
            self.out.event(type="branch_error", id=cmd.get("id"), msg=str(exc))
        return False

    def run(self, timeout=None):
        self.build()
        if self.control:
            threading.Thread(target=self._stdin, daemon=True).start()
        if self.initial_branch:
            self.add(1, self.initial_branch)
        if timeout:
            GLib.timeout_add_seconds(timeout, self._timeout)
        while self.pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            msg = self.pipeline.get_bus().timed_pop_filtered(0, Gst.MessageType.ERROR)
            why = msg.parse_error()[0].message if msg else "cannot start the source pipeline"
            if not self.candidates:
                raise RuntimeError(why)
            log(f"error: {why}; trying next capture method")
            self.pipeline.set_state(Gst.State.NULL)
            self.pipeline.get_bus().remove_signal_watch()
            self.build()
        self.ml.run()
        for bid in list(self.branches):
            ff = self.branches[bid]["ff"]
            if ff:
                ff.kill()
        self.pipeline.set_state(Gst.State.NULL)
        return self.exit_code

    def _timeout(self):
        if self.result is None:
            self.result = {"error": "timed out waiting for video"}
        self.ml.quit()
        return False


# --------------------------------------------------------------------------- entry
def _arg(argv, name, default=None):
    if name in argv:
        i = argv.index(name)
        val = argv[i + 1]
        del argv[i:i + 2]
        return val
    return default


def _connect(addr, token):
    """Use a TCP connection to the parent as stdin/stdout."""
    import io
    import socket
    global CONNECTED
    host, port = addr.rsplit(":", 1)
    s = socket.create_connection((host, int(port)), timeout=15)
    s.settimeout(None)
    s.sendall((json.dumps({"token": token}) + "\n").encode())
    w = s.makefile("wb")
    sys.stdout = io.TextIOWrapper(w, encoding="utf-8", write_through=True)
    sys.stdin = io.TextIOWrapper(s.makefile("rb"), encoding="utf-8")
    CONNECTED = True
    return s


def push(src, argv):
    """Agent side: capture -> H.264 / MPEG-TS -> hub data connection."""
    import socket
    host, port = _arg(argv, "--to").rsplit(":", 1)
    token, auth = _arg(argv, "--token", ""), _arg(argv, "--auth", "")
    br = int(_arg(argv, "--bitrate", "6000"))
    fps = int(float(urllib.parse.parse_qs(urllib.parse.urlsplit(src).query).get("fps", ["25"])[0]))
    s = socket.create_connection((host, int(port)), timeout=15)
    s.recv(4096)  # challenge line (not needed for data connections)
    s.sendall((json.dumps({"type": "data", "stream": token, "auth": auth}) + "\n").encode())
    s.settimeout(None)
    branch = ("queue max-size-buffers=5 max-size-bytes=0 max-size-time=0 leaky=downstream ! "
              f"x264enc tune=zerolatency speed-preset=veryfast bitrate={br} vbv-buf-capacity=1000 "
              f"key-int-max={max(1, fps * 2)} bframes=0 byte-stream=true "
              'option-string="repeat-headers=1:scenecut=0" ! h264parse config-interval=-1 ! '
              "mpegtsmux ! appsink name=out max-buffers=200 drop=false")
    w = Worker(src, RawSockOut(s), loop=False, control=False, branch=branch)
    return w.run()


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    conn = _arg(argv, "--connect")
    if conn:
        _connect(conn, _arg(argv, "--token", ""))
        if not argv or argv[0] != "run":  # probe / devices answer on the socket, log to a file
            import tempfile
            sys.stderr = open(os.path.join(tempfile.gettempdir(), "vcam-gsthelper.log"), "a",
                              encoding="utf-8")
    Gst.init(None)
    demote_hw_decoders()
    cmd = argv.pop(0) if argv else "check"
    if cmd == "check":
        need = ["uridecodebin", "videoconvert", "videoscale", "videorate", "tee", "appsink",
                "x264enc", "h264parse", "jpegenc", "y4menc"]
        print(json.dumps({"version": Gst.version_string(),
                          "missing": [e for e in need if not has(e)]}))
        return 0
    if cmd == "devices":
        cams = [{"index": i, "name": d["name"], "api": d["apis"][0]}
                for i, d in enumerate(video_devices())]
        print(json.dumps({"webcams": cams, "screens": monitors()}), flush=True)
        return 0
    if not argv:
        print(__doc__, file=sys.stderr)
        return 2
    src = argv.pop(0)
    if cmd == "push":
        try:
            return push(src, argv)
        except Exception as exc:  # noqa
            log(f"error: {exc}")
            return 1
    if cmd == "probe":
        try:
            w = Worker(src, None, probe=True)
            w.run(timeout=20)
            res = w.result or {"error": "no video"}
        except Exception as exc:  # noqa
            res = {"error": str(exc)}
        print(json.dumps(res), flush=True)
        return 0 if "error" not in res else 1
    if cmd == "run":
        start = float(argv[argv.index("--start") + 1]) if "--start" in argv else 0.0
        out = Out()
        try:
            w = Worker(src, out, start=start, loop="--no-loop" not in argv)
        except Exception as exc:  # noqa
            log(f"error: {exc}")
            out.event(type="error", msg=str(exc))
            return 1
        return w.run()
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
