"""CI diagnostics: run multi-monitor capture variants with gst-launch for a few seconds."""
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(sys.argv[1], "app"))
from vcam import gsthelper as g  # noqa: E402

g.Gst.init(None)
m = g.monitors()[0]
h, w, hh = m.get("hmonitor"), m["width"], m["height"]
sel = f"monitor-handle={h}" if h else "monitor-index=0"
d3d = "video/x-raw(memory:D3D11Memory),framerate=25/1"
variants = {
    "current all": g.screen_candidates("all", 25)[0],  # fixed variant (download first)
    "current 0+0": g.screen_candidates("0+0", 25)[0],
    "download first": (f"d3d11screencapturesrc {sel} show-cursor=true ! {d3d} ! d3d11download ! videoconvert ! "
                       f"queue ! mix.sink_0 d3d11screencapturesrc {sel} show-cursor=true ! {d3d} ! d3d11download ! "
                       f"videoconvert ! queue ! mix.sink_1 compositor name=mix background=black sink_1::xpos={w} ! "
                       f"video/x-raw,width={2 * w},height={hh},framerate=25/1"),
    "d3d11compositor": (f"d3d11screencapturesrc {sel} show-cursor=true ! {d3d} ! queue ! mix.sink_0 "
                        f"d3d11screencapturesrc {sel} show-cursor=true ! {d3d} ! queue ! mix.sink_1 "
                        f"d3d11compositor name=mix background=black sink_1::xpos={w} ! "
                        f"video/x-raw(memory:D3D11Memory),width={2 * w},height={hh} ! d3d11download ! "
                        "video/x-raw,framerate=25/1"),
    "single + download": f"d3d11screencapturesrc {sel} show-cursor=true ! {d3d} ! d3d11download",
}
for name, desc in variants.items():
    print(f"===== {name}\n{desc}\n", flush=True)
    env = dict(os.environ, GST_DEBUG="2")
    cmd = ["gst-launch-1.0.exe", "-v"] + (desc + " ! videoconvert ! fakesink num-buffers=60").split()
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=15, env=env)
        out, err, rc = p.stdout, p.stderr, p.returncode
    except subprocess.TimeoutExpired as e:
        dec = lambda b: b.decode(errors="replace") if isinstance(b, bytes) else (b or "")  # noqa
        out, err, rc = dec(e.stdout), dec(e.stderr), "TIMEOUT"
    print(f"--- rc={rc}")
    print("\n".join(l for l in out.splitlines() if "caps = " not in l or "mix" in l or "fakesink" in l)[-3000:])
    print(err[-6000:], flush=True)
