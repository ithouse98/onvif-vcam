"""Camera data model: device identity, media profiles, encoder configurations, users, state."""

import json
import logging
import os
import subprocess
import threading
from dataclasses import dataclass, field, asdict
from fractions import Fraction

from .util import stable_uuid, fake_mac, NO_WINDOW

log = logging.getLogger("vcam.model")

USER_LEVELS = ("Administrator", "Operator", "User", "Extended")


# --------------------------------------------------------------------------- probing
@dataclass
class MediaInfo:
    path: str
    codec: str = "h264"
    width: int = 1920
    height: int = 1080
    fps: float = 25.0
    duration: float = 0.0
    bitrate_kbps: int = 0
    has_bframes: bool = False

    @property
    def aspect(self):
        return self.width / self.height if self.height else 16 / 9


_probe_cache = {}


def probe(path: str) -> MediaInfo:
    """Query the first video stream with ffprobe (files cached by size+mtime: clones are fast)."""
    if os.path.isfile(path):
        st = os.stat(path)
        key = (os.path.abspath(path), st.st_size, st.st_mtime)
        if key not in _probe_cache:
            _probe_cache[key] = _probe(path)
        return MediaInfo(**vars(_probe_cache[key]))
    return _probe(path)


def _probe(path: str) -> MediaInfo:
    if not path.startswith(("rtsp://", "rtmp://", "http://", "https://", "udp://", "srt://")) \
            and not os.path.isfile(path):
        raise FileNotFoundError(path)
    cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
           "stream=codec_name,width,height,r_frame_rate,avg_frame_rate,bit_rate,has_b_frames,duration:"
           "format=duration,bit_rate", "-of", "json", path]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=30, creationflags=NO_WINDOW)
    if out.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {path}: {out.stderr.strip()}")
    data = json.loads(out.stdout or "{}")
    streams = data.get("streams") or []
    if not streams:
        raise RuntimeError(f"no video stream in {path}")
    st = streams[0]
    fps = 25.0
    for key in ("avg_frame_rate", "r_frame_rate"):
        try:
            fr = Fraction(st.get(key, "0/1"))
            if 0 < fr < 241:
                fps = float(fr)
                break
        except (ValueError, ZeroDivisionError):
            pass
    fmt = data.get("format") or {}
    br = st.get("bit_rate") or fmt.get("bit_rate") or 0
    return MediaInfo(
        path=path,
        codec=st.get("codec_name", "h264"),
        width=int(st.get("width") or 1920),
        height=int(st.get("height") or 1080),
        fps=round(fps, 3),
        # video stream first: a longer audio track must not push seeks past the last frame
        duration=float(st.get("duration") or fmt.get("duration") or 0.0),
        bitrate_kbps=int(int(br) / 1000) if br else 0,
        has_bframes=bool(int(st.get("has_b_frames") or 0)),
    )


def even(v: int) -> int:
    return max(16, int(v) // 2 * 2)


def mult(v: int, m: int) -> int:
    return max(m, int(v) // m * m)


def standard_resolutions(info: MediaInfo, max_w=None, max_h=None):
    """Resolution ladder with the source aspect ratio, never upscaled."""
    heights = [2160, 1520, 1440, 1296, 1080, 960, 720, 576, 540, 480, 360, 288, 240, 180]
    max_w = min(max_w or info.width, info.width)
    max_h = min(max_h or info.height, info.height)
    res = []
    if info.width <= max_w and info.height <= max_h:
        res.append((even(info.width), even(info.height)))
    for h in heights:
        w = even(round(h * info.aspect))
        if w <= max_w and h <= max_h and (w, h) not in res:
            res.append((w, h))
    if not res:
        res.append((even(min(max_w, info.width)), even(min(max_h, info.height))))
    return res


# --------------------------------------------------------------------------- config objects
@dataclass
class Multicast:
    address: str = "239.255.1.1"
    port: int = 40000
    ttl: int = 16
    auto_start: bool = False


@dataclass
class VideoEncoderConfig:
    token: str
    name: str
    encoding: str = "H264"          # H264 | H265 | JPEG
    width: int = 1920
    height: int = 1080
    fps: float = 25.0
    bitrate_kbps: int = 4096
    gop: int = 25
    profile: str = "Main"           # H264: Baseline|Main|High ; H265: Main
    quality: float = 5.0            # 1..10
    cbr: bool = False
    mode: str = "transcode"         # transcode | copy (copy ignores size/fps/bitrate)
    session_timeout: int = 60
    multicast: Multicast = field(default_factory=Multicast)

    def stream_key(self):
        # only what changes the ffmpeg command: VMSs re-apply settings on every connect, and
        # restarting the encoder for an unused field (e.g. Quality on H.264) drops all viewers
        if self.mode == "copy":
            return ("copy",)
        jpeg = self.encoding == "JPEG"
        return (self.encoding, self.width, self.height, round(self.fps, 3),
                None if jpeg else self.bitrate_kbps, None if jpeg else self.gop,
                self.profile if self.encoding == "H264" else None,
                round(self.quality) if jpeg else None, None if jpeg else self.cbr)


@dataclass
class MetadataConfig:
    token: str = "MDC_1"
    name: str = "MetadataConfig"
    analytics: bool = False
    events: bool = False
    session_timeout: int = 60
    multicast: Multicast = field(default_factory=Multicast)


@dataclass
class Profile:
    token: str
    name: str
    fixed: bool = False
    vsc: str = None                 # video source configuration token
    vec: str = None                 # video encoder configuration token
    mdc: str = None                 # metadata configuration token


@dataclass
class User:
    username: str
    password: str
    level: str = "Administrator"


# --------------------------------------------------------------------------- camera
class Camera:
    """One virtual ONVIF device."""

    def __init__(self, index: int, cfg: dict, global_cfg: dict):
        self.index = index
        self.cfg = cfg
        self.gcfg = global_cfg
        self.id = cfg.get("id") or f"cam{index}"
        self.name = cfg.get("name") or f"VirtualCam-{index}"
        self.source = cfg["source"]
        self.http_port = int(cfg.get("http_port") or (global_cfg.get("http_port_base", 8080) + index - 1))
        self.uuid = cfg.get("uuid") or stable_uuid(f"{self.id}:{self.source}:{self.http_port}")
        self.manufacturer = cfg.get("manufacturer", "ONVIF-VCam")
        self.model = cfg.get("model", "VirtualCamera-S")
        self.firmware = cfg.get("firmware", "1.0.0")
        self.serial = cfg.get("serial") or self.uuid.replace("-", "")[:16].upper()
        self.hardware_id = cfg.get("hardware_id", "VCAM-HW-1")
        self.mac = cfg.get("mac") or fake_mac(self.uuid)
        self.location = cfg.get("location", "lab")
        self.lock = threading.RLock()
        self.stop_evt = threading.Event()  # set when the camera is removed/disabled at runtime
        self.state_path = None
        self.info = probe(self.source)
        self.motion = dict(global_cfg.get("motion", {}), **cfg.get("motion", {}))
        self._defaults()

    # ---------------------------------------------------------------- defaults/state
    def _defaults(self):
        info = self.info
        # one physical "sensor" = the file
        self.video_source_token = "VS_1"
        self.vsc_token = "VSC_1"
        self.vecs = {}
        self.profiles = {}
        prof_cfgs = self.cfg.get("profiles") or [
            {"name": "MainStream"},
            {"name": "SubStream", "width": 640, "bitrate_kbps": 768},
        ]
        for i, pc in enumerate(prof_cfgs, start=1):
            w = int(pc.get("width") or info.width)
            if pc.get("height"):
                h = int(pc["height"])
            else:
                h = round(w / info.aspect)
            w, h = even(min(w, info.width)), even(min(h, info.height))
            enc = pc.get("encoding", "H264").upper().replace(".", "")
            if enc in ("MJPEG", "JPG"):
                enc = "JPEG"
            if enc == "HEVC":
                enc = "H265"
            if enc == "JPEG":
                w, h = mult(min(w, 2040), 16), mult(min(h, 2040), 16)
            fps = float(pc.get("fps") or min(info.fps, 30))
            mode = pc.get("mode", "transcode")
            if mode == "copy":
                w, h, fps = info.width, info.height, info.fps
                enc = {"h264": "H264", "hevc": "H265", "mjpeg": "JPEG"}.get(info.codec, enc)
            tok = f"VEC_{i}"
            self.vecs[tok] = VideoEncoderConfig(
                token=tok, name=f"VideoEncoder_{i}", encoding=enc, width=w, height=h, fps=fps,
                bitrate_kbps=int(pc.get("bitrate_kbps") or (4096 if i == 1 else 768)),
                gop=int(pc.get("gop") or round(fps)), profile=pc.get("profile", "Main"),
                quality=float(pc.get("quality", 5)), cbr=bool(pc.get("cbr", False)), mode=mode,
                multicast=Multicast(address=f"239.255.{self.index % 256}.{i}",
                                    port=40000 + (self.index % 1000) * 10 + i * 2))
            ptok = pc.get("token") or f"Profile_{i}"
            self.profiles[ptok] = Profile(token=ptok, name=pc.get("name", f"Profile{i}"), fixed=True,
                                          vsc=self.vsc_token, vec=tok)
        self.mdcs = {"MDC_1": MetadataConfig(multicast=Multicast(
            address=f"239.255.{self.index % 256}.200", port=40000 + (self.index % 1000) * 10 + 8))}
        self.fixed_scopes = [
            "onvif://www.onvif.org/type/video_encoder",
            "onvif://www.onvif.org/type/Network_Video_Transmitter",
            "onvif://www.onvif.org/Profile/Streaming",
            "onvif://www.onvif.org/Profile/T",
            f"onvif://www.onvif.org/hardware/{self.model}",
            f"onvif://www.onvif.org/name/{self.name.replace(' ', '_')}",
            f"onvif://www.onvif.org/MAC/{self.mac}",
        ]
        self.scopes = [f"onvif://www.onvif.org/location/{self.location}"]
        self.discoverable = True
        self.hostname = self.cfg.get("hostname") or self.id
        self.dns = ["8.8.8.8"]
        self.ntp = ["pool.ntp.org"]
        self.ntp_mode = False
        self.gateway = ["0.0.0.0"]
        self.time_offset = 0.0
        self.tz = "UTC0"
        users = self.cfg.get("users") or self.gcfg.get("users") or [
            {"username": "admin", "password": "admin", "level": "Administrator"}]
        self.users = {u["username"]: User(u["username"], u["password"], u.get("level", "Administrator"))
                      for u in users}
        self.protocols = {"HTTP": self.http_port, "RTSP": int(self.gcfg.get("rtsp_port", 8554))}

    def to_state(self):
        with self.lock:
            return {
                "vecs": {k: asdict(v) for k, v in self.vecs.items()},
                "profiles": {k: asdict(v) for k, v in self.profiles.items()},
                "mdcs": {k: asdict(v) for k, v in self.mdcs.items()},
                "scopes": self.scopes, "discoverable": self.discoverable,
                "hostname": self.hostname, "dns": self.dns, "ntp": self.ntp,
                "ntp_mode": self.ntp_mode, "gateway": self.gateway, "time_offset": self.time_offset,
                "tz": self.tz, "users": {k: asdict(v) for k, v in self.users.items()},
            }

    def load_state(self, path):
        self.state_path = path
        if not path or not os.path.isfile(path):
            return
        try:
            with open(path) as fh:
                st = json.load(fh)
            if st.get("source") not in (None, self.source):
                log.info("%s: source changed, ignoring saved state", self.id)
                return
            self.vecs = {k: VideoEncoderConfig(**dict(v, multicast=Multicast(**v["multicast"])))
                         for k, v in st["vecs"].items()}
            self.profiles = {k: Profile(**v) for k, v in st["profiles"].items()}
            self.mdcs = {k: MetadataConfig(**dict(v, multicast=Multicast(**v["multicast"])))
                         for k, v in st["mdcs"].items()}
            self.users = {k: User(**v) for k, v in st["users"].items()}
            for k in ("scopes", "discoverable", "hostname", "dns", "ntp", "ntp_mode", "gateway",
                      "time_offset", "tz"):
                if k in st:
                    setattr(self, k, st[k])
            log.info("%s: restored state from %s", self.id, path)
        except Exception as exc:  # corrupted state must never stop the device
            log.warning("%s: could not load state %s: %s", self.id, path, exc)

    def save(self):
        if not self.state_path:
            return
        try:
            st = self.to_state()
            st["source"] = self.source
            tmp = self.state_path + ".tmp"
            with open(tmp, "w") as fh:
                json.dump(st, fh, indent=2)
            os.replace(tmp, self.state_path)
        except OSError as exc:
            log.warning("%s: cannot save state: %s", self.id, exc)

    def factory_reset(self, hard=False):
        with self.lock:
            users = self.users
            self._defaults()
            if not hard:
                self.users = users
            self.save()

    # ---------------------------------------------------------------- helpers
    @property
    def all_scopes(self):
        return self.fixed_scopes + self.scopes

    def use_count(self, kind, token):
        attr = {"vsc": "vsc", "vec": "vec", "mdc": "mdc"}[kind]
        return sum(1 for p in self.profiles.values() if getattr(p, attr) == token)

    def resolutions(self, encoding="H264"):
        if encoding == "JPEG":
            return [(mult(w, 16), mult(h, 16)) for w, h in standard_resolutions(self.info, 2040, 2040)]
        return standard_resolutions(self.info)

    def max_fps(self):
        return max(1, int(round(self.info.fps)))
