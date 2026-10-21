"""Small helpers shared by all modules."""

import datetime as _dt
import logging
import re
import socket
import subprocess
import threading
import uuid
from xml.sax.saxutils import escape as _xml_escape

log = logging.getLogger("vcam")

# child processes (ffmpeg/ffprobe) must not pop up console windows in windowless mode
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def esc(value) -> str:
    """XML-escape any value (bool -> 'true'/'false')."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return _xml_escape(str(value), {'"': "&quot;"})


def xbool(value) -> str:
    return "true" if value else "false"


def localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def nsof(tag: str) -> str:
    return tag[1:].split("}", 1)[0] if tag.startswith("{") else ""


def child(elem, name):
    """First direct child with local name `name` (namespace agnostic)."""
    if elem is None:
        return None
    for c in elem:
        if localname(c.tag) == name:
            return c
    return None


def children(elem, name):
    if elem is None:
        return []
    return [c for c in elem if localname(c.tag) == name]


def find_path(elem, *names):
    cur = elem
    for n in names:
        cur = child(cur, n)
        if cur is None:
            return None
    return cur


def text(elem, *names, default=None):
    node = find_path(elem, *names) if names else elem
    if node is None or node.text is None:
        return default
    return node.text.strip()


def to_bool(value, default=False):
    if value is None:
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def to_int(value, default=0):
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default


def utcnow() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def iso(ts: _dt.datetime) -> str:
    return ts.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + \
        f"{ts.microsecond // 1000:03d}Z"


_DUR_RE = re.compile(
    r"^-?P(?:(?P<y>\d+(?:\.\d+)?)Y)?(?:(?P<mo>\d+(?:\.\d+)?)M)?(?:(?P<w>\d+(?:\.\d+)?)W)?"
    r"(?:(?P<d>\d+(?:\.\d+)?)D)?(?:T(?:(?P<h>\d+(?:\.\d+)?)H)?(?:(?P<mi>\d+(?:\.\d+)?)M)?"
    r"(?:(?P<s>\d+(?:\.\d+)?)S)?)?$")


def parse_duration(value, default=None):
    """xs:duration -> seconds (float)."""
    if not value:
        return default
    m = _DUR_RE.match(value.strip())
    if not m:
        return default
    g = {k: float(v) if v else 0.0 for k, v in m.groupdict().items()}
    secs = (g["y"] * 365 * 86400 + g["mo"] * 30 * 86400 + g["w"] * 7 * 86400 +
            g["d"] * 86400 + g["h"] * 3600 + g["mi"] * 60 + g["s"])
    return -secs if value.strip().startswith("-") else secs


def parse_datetime(value):
    if not value:
        return None
    v = value.strip().replace("z", "Z")
    if v.endswith("Z"):
        v = v[:-1] + "+00:00"
    try:
        dt = _dt.datetime.fromisoformat(v)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    return dt


def parse_time_or_duration(value, now: _dt.datetime):
    """InitialTerminationTime / TerminationTime: absolute dateTime or duration."""
    if not value:
        return None
    if value.strip().startswith(("P", "-P")):
        secs = parse_duration(value)
        return now + _dt.timedelta(seconds=secs) if secs is not None else None
    return parse_datetime(value)


def new_uuid() -> str:
    return str(uuid.uuid4())


def stable_uuid(name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "onvif-vcam:" + name))


def route_ip(remote_ip: str = "8.8.8.8") -> str:
    """Local IPv4 address used to reach `remote_ip`."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((remote_ip, 9))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def list_ipv4():
    """Best-effort list of non-loopback local IPv4 addresses (stdlib only)."""
    ips = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    ips.add(route_ip())
    try:  # Linux: read kernel table, catches secondary/alias IPs too
        with open("/proc/net/fib_trie") as fh:
            lines = fh.read().splitlines()
        for i, line in enumerate(lines):
            if "/32 host LOCAL" in line and i > 0:
                ip = lines[i - 1].split()[-1]
                ips.add(ip)
    except OSError:
        pass
    return sorted(ip for ip in ips if not ip.startswith("127."))


def fake_mac(seed: str) -> str:
    h = uuid.uuid5(uuid.NAMESPACE_DNS, seed).bytes
    return "02:%02x:%02x:%02x:%02x:%02x" % tuple(h[:5])


class Stoppable:
    """Mixin providing a shared stop event."""

    def __init__(self):
        self._stop_evt = threading.Event()

    def stop(self):
        self._stop_evt.set()

    @property
    def stopped(self):
        return self._stop_evt.is_set()
