"""End-to-end test of the control panel API against a running vcam (stdlib + ffprobe).

    python tests/test_panel.py [host] [panel_port] [user] [password] [video_to_upload]
"""

import base64
import json
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

HOST = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 8000
USER = sys.argv[3] if len(sys.argv) > 3 else "admin"
PWD = sys.argv[4] if len(sys.argv) > 4 else "admin"
UPLOAD = sys.argv[5] if len(sys.argv) > 5 else None
BASE = f"http://{HOST}:{PORT}"
AUTH = {"Authorization": "Basic " + base64.b64encode(f"{USER}:{PWD}".encode()).decode()}
R = []


def call(method, path, body=None, raw=None, auth=True):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    h = dict(AUTH) if auth else {}
    if body is not None:
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            b = r.read()
            return r.status, (json.loads(b) if "json" in r.headers.get("Content-Type", "") else b)
    except urllib.error.HTTPError as e:
        b = e.read()
        try:
            return e.code, json.loads(b)
        except ValueError:
            return e.code, b


def ok(name, cond, detail=""):
    R.append(bool(cond))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  [{detail}]" if detail and not cond else ""))


def cam(state, cid):
    return next(c for c in state["cameras"] if c["config"]["id"] == cid)


def ffprobe(url):
    out = subprocess.run(["ffprobe", "-v", "error", "-rtsp_transport", "tcp", "-show_entries",
                          "stream=codec_name,width,height", "-of", "csv=p=0", url],
                         capture_output=True, text=True, timeout=40)
    return out.stdout.strip()


def port_open(p):
    s = socket.socket()
    s.settimeout(2)
    try:
        return s.connect_ex((HOST, p)) == 0
    finally:
        s.close()


def main():
    st, _ = call("GET", "/api/state", auth=False)
    ok("panel requires login (401)", st == 401)
    st, page = call("GET", "/")
    ok("panel page served", st == 200 and b"ONVIF VCam" in page)
    st, s0 = call("GET", "/api/state")
    ok("GET /api/state", st == 200 and "cameras" in s0 and "settings" in s0)
    n0 = len(s0["cameras"])

    st, v = call("GET", "/api/videos")
    ok("GET /api/videos", st == 200 and isinstance(v.get("files"), list))
    src = v["files"][0]["path"] if v["files"] else None
    if UPLOAD:
        with open(UPLOAD, "rb") as fh:
            st, up = call("PUT", "/api/videos?name=uploaded_test.mp4", raw=fh.read())
        ok("upload video", st == 200 and "x" in up.get("info", ""), str(up))
        src = up.get("path", src)
    st, e = call("PUT", "/api/videos?name=../../evil.exe", raw=b"x")
    ok("upload rejects bad file name", st == 400)

    # ---- validation (trust boundary)
    st, e = call("POST", "/api/cameras", {"name": "x", "source": "",
                                          "profiles": [{"name": "a"}]})
    ok("add rejects empty source", st == 400)
    st, e = call("POST", "/api/cameras", {"name": "x", "source": src,
                                          "profiles": [{"name": "a", "encoding": "VP9"}]})
    ok("add rejects unknown codec", st == 400)
    st, e = call("POST", "/api/cameras", {"name": "x", "source": "/no/such/file.mp4",
                                          "profiles": [{"name": "a"}]})
    ok("add rejects missing file (not saved)", st == 400 and
       len(call("GET", "/api/state")[1]["cameras"]) == n0, str(e))

    # ---- add two cameras at once
    st, r = call("POST", "/api/cameras", {
        "name": "PanelCam", "source": src, "count": 2,
        "profiles": [{"name": "Main", "encoding": "H264", "width": 1280, "height": 720, "fps": 20,
                      "bitrate_kbps": 1500},
                     {"name": "Sub", "encoding": "H264", "width": 640, "height": 360, "fps": 10}],
        "motion": {"enabled": True, "on_seconds": 5, "off_seconds": 30}})
    ok("add camera x2", st == 200 and len(r.get("ids", [])) == 2, str(r))
    ids = r.get("ids", [])
    s1 = call("GET", "/api/state")[1]
    c1 = cam(s1, ids[0])
    port, uuid = c1["config"]["http_port"], c1["config"]["uuid"]
    ok("new cameras running with unique ports",
       all(cam(s1, i)["running"] for i in ids) and
       len({cam(s1, i)["config"]["http_port"] for i in ids}) == 2)
    rtsp = c1["runtime"]["profiles"][0]["rtsp"].replace("rtsp://", f"rtsp://{USER}:{PWD}@")
    out = ffprobe(rtsp)
    ok("new camera streams 1280x720", out.startswith("h264,1280,720"), out)

    # ---- edit keeps identity
    st, r = call("PUT", f"/api/cameras/{ids[0]}", {
        "name": "PanelCam-edited",
        "profiles": [{"name": "Main", "encoding": "H265", "width": 960, "height": 540, "fps": 15}]})
    ok("edit camera", st == 200, str(r))
    s2 = call("GET", "/api/state")[1]
    c2 = cam(s2, ids[0])
    ok("edit keeps UUID and port (VMS sees the same device)",
       c2["config"]["uuid"] == uuid and c2["config"]["http_port"] == port)
    out = ffprobe(c2["runtime"]["profiles"][0]["rtsp"].replace("rtsp://", f"rtsp://{USER}:{PWD}@"))
    ok("edited camera streams H.265 960x540", out.startswith("hevc,960,540"), out)

    # ---- disable / enable
    call("PUT", f"/api/cameras/{ids[1]}", {"enabled": False})
    p2 = cam(s2, ids[1])["config"]["http_port"]
    time.sleep(0.5)
    ok("disable closes the camera's ONVIF port", not port_open(p2))
    call("PUT", f"/api/cameras/{ids[1]}", {"enabled": True})
    time.sleep(0.5)
    ok("enable reopens it", port_open(p2) and cam(call("GET", "/api/state")[1], ids[1])["running"])

    # ---- motion + snapshot
    st, _ = call("POST", f"/api/cameras/{ids[0]}/motion?state=1")
    time.sleep(0.3)
    ok("motion trigger", st == 200 and cam(call("GET", "/api/state")[1], ids[0])["runtime"]["motion"])
    st, jpg = call("GET", f"/api/cameras/{ids[0]}/snapshot.jpg")
    ok("snapshot JPEG", st == 200 and jpg[:2] == b"\xff\xd8")

    # ---- network selection
    ips = s2["settings"]["ips"]
    if ips:
        st, _ = call("PUT", "/api/settings", {"advertise_ip": ips[0]})
        s3 = call("GET", "/api/state")[1]
        ok("set advertise IP", st == 200 and s3["settings"]["advertise_ip"] == ips[0] and
           ips[0] in cam(s3, ids[0])["runtime"]["device_service"])
        call("PUT", "/api/settings", {"advertise_ip": ""})

    # ---- clone for stress tests: separate devices, shared encoder
    st, r = call("POST", f"/api/cameras/{ids[1]}/clone", {"count": 3})
    clones = r.get("ids", [])
    s5 = call("GET", "/api/state")[1]
    cports = [cam(s5, i)["config"]["http_port"] for i in clones]
    ok("clone x3: running, distinct ports and UUIDs",
       st == 200 and len(clones) == 3 and len(set(cports)) == 3 and
       all(cam(s5, i)["running"] for i in clones) and
       len({cam(s5, i)["config"]["uuid"] for i in clones + [ids[1]]}) == 4, str(r))
    ok("every clone answers ONVIF on its own port", all(port_open(p) for p in cports))
    urls = [cam(s5, i)["runtime"]["profiles"][1]["rtsp"].replace("rtsp://", f"rtsp://{USER}:{PWD}@")
            for i in clones]
    procs = [subprocess.Popen(["ffmpeg", "-v", "error", "-rtsp_transport", "tcp", "-i", u, "-t", "6",
                               "-f", "null", "-"]) for u in urls]
    time.sleep(4)
    enc = [e for e in call("GET", "/api/state")[1]["encoders"] if e["consumers"]]
    ok("3 clone viewers share one encoder", len(enc) == 1 and enc[0]["consumers"] == 3,
       str([(e["label"], e["consumers"]) for e in enc]))
    ok("all clone streams decode", all(p.wait(30) == 0 for p in procs))
    ids += clones

    # ---- delete
    for i in ids:
        call("DELETE", f"/api/cameras/{i}")
    s4 = call("GET", "/api/state")[1]
    ok("delete cameras", len(s4["cameras"]) == n0 and not port_open(port))
    st, logs = call("GET", "/api/logs?after=0")
    ok("logs endpoint", st == 200 and any("offline (removed)" in x["text"] for x in logs))
    if UPLOAD:
        call("DELETE", "/api/videos?name=uploaded_test.mp4")

    print(f"\n{sum(R)}/{len(R)} panel checks passed")
    return 0 if all(R) else 1


if __name__ == "__main__":
    sys.exit(main())
