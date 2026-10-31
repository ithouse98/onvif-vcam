"""CI diagnostics: run the multi-monitor capture pipeline with gst-launch for a few seconds."""
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(sys.argv[1], "app"))
from vcam import gsthelper as g  # noqa: E402

g.Gst.init(None)
for arg in ("all", "0+0"):
    desc = g.screen_candidates(arg, 25)[0]
    print(f"===== screen://{arg}\n{desc}\n", flush=True)
    env = dict(os.environ, GST_DEBUG="3")
    cmd = ["gst-launch-1.0", "-v"] + (desc + " ! videoconvert ! fakesink silent=false num-buffers=50").split()
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=15, env=env)
        out, err = p.stdout, p.stderr
    except subprocess.TimeoutExpired as e:
        out, err = (e.stdout or b"").decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or ""), \
                   (e.stderr or b"").decode(errors="replace") if isinstance(e.stderr, bytes) else (e.stderr or "")
        print("TIMEOUT (still running after 15 s)")
    print(out[-5000:])
    print(err[-10000:], flush=True)
