<p align="center">
  <img src="docs/images/logo.png" width="220" alt="IPS-Co.">
</p>

<h1 align="center">ONVIF VCam</h1>
<p align="center"><b>Turn any video file, your screens (local or on other PCs) or a USB webcam into a standards-compliant ONVIF IP camera for VMS testing.</b><br>
Virtual ONVIF Profile S / T camera with WS-Discovery, RTSP streaming, events and a web control panel. Fully offline and portable.</p>

<p align="center">
  <img alt="version" src="https://img.shields.io/badge/version-1.3.1-0f6fb3">
  <img alt="ONVIF" src="https://img.shields.io/badge/ONVIF-Profile%20S%20%2F%20T-38bdf8">
  <img alt="platform" src="https://img.shields.io/badge/Windows%20x64-portable%20%26%20offline-2fbf71">
  <img alt="GStreamer" src="https://img.shields.io/badge/engine-GStreamer-e8202a">
  <img alt="python" src="https://img.shields.io/badge/Python-3.9%2B%20stdlib%20only-3776ab">
</p>

<p align="center"><b>Developed by Behrouz Kazemi</b> (بهروز کاظمی), VMS Systems Specialist · <b>IPS-Co.</b> ·
Telegram <a href="https://t.me/B_8241">@B_8241</a><br>
<a href="README.fa.md">راهنمای فارسی</a></p>

---

![Control panel](docs/images/panel.jpg)

## Why

VMS engineers need cameras to test with: many of them, of different models and codecs, in
reproducible conditions. ONVIF VCam turns recorded clips into real ONVIF devices. A VMS discovers
them, adds them, configures them, records them and receives their motion events, exactly as it would
with physical cameras. No hardware is needed, and the same footage plays every time.

## Features

**ONVIF device (built from the official ONVIF specifications)**
- **WS-Discovery:** Hello / Bye / Probe / Resolve, scopes, stable `urn:uuid` per device
- **Services:** Device, Media (ver10), Media2 (ver20, H.265), Events (pull-point + WS-BaseNotification push)
- **Authentication:** WS-UsernameToken digest + HTTP Digest, ONVIF user levels and access policy
- **SOAP faults** with correct ONVIF `ter:` sub-codes

**Video sources**
- **Video files** (looped seamlessly), **RTSP/HTTP streams** (re-publish a real camera)
- **Screen capture** (`screen://`): the desktop of the PC, with the mouse pointer, as an ONVIF camera.
  Windows: Direct3D 11 desktop duplication (GDI fallback); Linux: X11
- **Several monitors in one picture:** `screen://all` composes every monitor as arranged in Windows
  display settings (e.g. two 1080p monitors side by side = one 3840×1080 stream); `screen://0+1`
  puts chosen monitors side by side
- **Screens of other PCs** (`agent://`): the screen agent runs as a service on the other PC, finds the
  server on the network, registers in the panel and streams its monitors on demand
- **Android phones** (agent app): a phone's back / front camera as an ONVIF camera, with hardware
  H.264, through the same agent hub
- **USB webcams / capture cards** (`webcam://`): Windows Media Foundation (KS / DirectShow
  fallback), Linux V4L2. The device is opened once and feeds every stream of the camera

**Streaming**
- **Engine:** GStreamer by default: one process per source decodes or captures once and feeds every
  encoder of that camera (main, sub, MJPEG, snapshots), so all streams show the same moment. The
  previous ffmpeg engine stays selectable (`"engine": "ffmpeg"`)
- **Codecs:** H.264, H.265 (HEVC) and MJPEG, transcoded from any file, or passed through untouched (`copy`)
- **RTSP transports:** RTP/UDP, RTP/RTSP/TCP, RTSP-over-HTTP tunnel, RTP multicast
- **RTP/RTCP:** RTCP sender reports, NAT-friendly UDP (symmetric RTP)
- **Configurable profiles:** main/sub/MJPEG; the VMS can change resolution, fps, bitrate, GOP and codec live
- **RTSP relay:** re-publish a real camera's RTSP stream as a new ONVIF device

**Built for VMS testing**
- **Simulated motion events** (`MotionAlarm`, `CellMotionDetector`), scheduled or triggered by hand
- **Clones for load testing:** one click makes N copies of a camera, each a separate ONVIF device
  (own port and UUID)
- **Shared encoders:** clones share one encoder, so 20 viewers on 20 devices cost a single encode
- **Robust under VMS behaviour:**
  - re-applied settings don't drop streams
  - an encoder watchdog restarts a frozen encoder or capture
  - the device keeps a stable address on hosts with several network adapters
- **Diagnostics:** every RTSP session logs its client software, transport, duration and packet count

**Web control panel** (port 8000, English or Persian, Vazirmatn font, works offline)
- **Cameras:** add from the video library (with upload), a file path, an RTSP URL, a screen or a
  webcam (device list built in); edit, enable/disable, delete
- **Live view:** snapshot previews with an on/off switch to save CPU; copy ONVIF/RTSP URLs in one click
- **Monitoring:** active sessions, encoders and a live log; the IP of the encoder feeding each
  camera (agent PC, phone, RTSP camera) and the IPs of its viewers
- **Network:** choose the IP announced to the VMS; change credentials
- **Themes:** data-center blue, operational green, dark, amber NOC, night purple, light

**Portable Windows bundle**
- **Self-contained:** Python 3.12, GStreamer 1.28 (x264, screen capture, webcam, PyGObject) and a GPL
  ffmpeg (libx265, ffprobe) are inside one folder; nothing to install, no internet
- **Windows service + tray icon:** VCam starts with Windows (even before anyone logs in); a tray
  icon next to the clock shows the status and starts / stops / restarts it
- **Background mode:** without the service it runs without a console window; stop from the panel,
  STOP.bat or autostart scripts; one-click firewall setup

![Themes](docs/images/themes.jpg)

| Camera editor | Clones for stress tests |
|---|---|
| ![editor](docs/images/editor.jpg) | ![clone](docs/images/clone.jpg) |

## Download & run (Windows)

### Setup program (recommended)

Run **`ONVIF-VCam-Setup-<version>.exe`** from [Releases](../../releases) (64-bit Windows 8.1+) and choose
what to install:

| Choice | What you get |
|---|---|
| **Server** (default) | the virtual cameras and control panel; with **Run as a Windows service** (default) it starts with Windows, even before anyone logs in, with the tray icon. Without the service it starts in the background when a user logs in. |
| **Agent** | the screen agent service: this PC's monitors are streamed to a VCam server (asks for its IP, port, key and name) |
| **Server + agent** | both on one PC |

The setup also asks for the panel / camera login (first install), adds the firewall rules, makes
Start-menu and desktop shortcuts and an entry in Windows **Installed apps**. Everything goes to
`C:\ProgramData\ONVIF-VCam`; running a newer setup upgrades in place and keeps cameras, videos and
settings. It replaces a separate agent setup installation (the agent keeps its identity).

Silent install: `ONVIF-VCam-Setup-<version>.exe /S [/INSTALL=server,service,agent] [/USER=admin]
[/PASSWORD=...] [/NOFIREWALL] [/SERVER=192.168.1.10] [/PORT=8600] [/KEY=...] [/NAME=PC-01]`
(default `/INSTALL=server,service`). Silent uninstall: `"C:\ProgramData\ONVIF-VCam\uninstall.exe" /S`
(keeps cameras, settings and videos).

### Portable bundle

1. Download **`ONVIF-VCam-<version>-Portable-win64.zip`** from [Releases](../../releases) and extract it
   (e.g. to `C:\ONVIF-VCam`).
2. Run **`FIREWALL.bat`** once as administrator.
3. Run **`START.bat`**. The control panel opens at `http://127.0.0.1:8000` (login `admin` / `admin`).
4. In your VMS, discover the cameras or add them manually. The ONVIF port of each camera is shown in
   the panel (8080, 8081, …).

| File | Purpose |
|---|---|
| `START.bat` / `STOP.bat` | run in the background / stop |
| `START-DEBUG.bat` | run with a console and verbose log |
| `SERVICE-INSTALL.bat` / `SERVICE-UNINSTALL.bat` | install / remove the Windows service + tray icon (as administrator) |
| `TRAY.bat` | start the tray icon by hand |
| `AGENT-INSTALL.bat` / `AGENT-UNINSTALL.bat` | **on another PC:** install / remove the screen agent service (or use the separate agent setup) |
| `AGENT-RUN.bat` | run the agent in a window (quick test, no service) |
| `AUTOSTART-ON.bat` / `AUTOSTART-OFF.bat` | start at user logon (alternative to the service) |
| `TEST.bat` | automated test suites (125 checks) |
| `SHELL.bat` | prompt with python / ffmpeg / ffprobe / ffplay / gst-launch-1.0 / gst-device-monitor-1.0 |

**Screen agent for other PCs:** `ONVIF-VCam-Agent-Setup-<version>.exe` on the same release page
(see [Screens of other PCs](#screens-of-other-pcs-agent)). **Android phones as cameras:**
`ONVIF-VCam-Agent-Android-<version>.apk` (see [Android phones](#android-phones-as-cameras-agent-app)).

The Windows launchers live in [`windows/`](windows). [`scripts/build_portable.sh`](scripts/build_portable.sh)
rebuilds the bundle from conda-forge and PyPI.

## Quick start (Python / any OS)

```bash
# needs: python3 >= 3.9, ffmpeg/ffprobe in PATH, and for the GStreamer engine (screen / webcam)
# GStreamer 1.20+ with PyGObject, e.g.  conda install -c conda-forge gstreamer gst-plugins-base \
#   gst-plugins-good gst-plugins-bad gst-plugins-ugly gst-libav pygobject
# Without GStreamer, file and RTSP cameras fall back to the ffmpeg engine.
python -m vcam lobby.mp4
```

```
 VirtualCam-1-lobby  [h264 1920x1080@25 120.0s]
   ONVIF   http://192.168.1.50:8080/onvif/device_service
   RTSP    rtsp://192.168.1.50:8554/cam1/Profile_1        H264 1920x1080@25
   RTSP    rtsp://192.168.1.50:8554/cam1/Profile_2        H264 640x360@15
 Status page: http://192.168.1.50:8080/
```

In the VMS: run a discovery (or add `192.168.1.50:8080` manually as an ONVIF device),
credentials **admin / admin**.

More examples:

```bash
python -m vcam a.mp4 b.mkv --cameras 8 --user admin --password Cam12345   # 16 devices
python -m vcam a.mp4 --main H265:2560x1440@20:3000:gop=40 --sub H264:640x360@10:384
python -m vcam a.mp4 --main copy --sub none           # pass the file's own bitstream through
python -m vcam a.mp4 --mjpeg                          # add a JPEG (RFC 2435) profile
python -m vcam rtsp://user:pw@10.0.0.64/stream1 --main copy   # re-publish a real camera
python -m vcam screen://                              # the primary screen as a camera
python -m vcam "screen://1?fps=15"                    # second monitor, captured at 15 fps
python -m vcam webcam://0                             # first USB webcam
python -m vcam "webcam://Logitech C920"               # webcam by (part of its) name
python -m vcam --list-devices                         # webcams and monitors found
python -m vcam a.mp4 --engine ffmpeg                  # previous engine
python -m vcam -c config.example.json                 # full control (see file)
python -m vcam a.mp4 --http-port 80 --rtsp-port 554   # camera-like ports (needs admin/root)
```

Stream spec: `ENC[:WxH][@FPS][:KBPS][:gop=N]` with `ENC` = `H264 | H265 | JPEG`, or `copy`.

### Screen and webcam cameras

![A Windows desktop received over RTSP from a screen:// camera](docs/images/screen-capture.jpg)


| Source | Meaning |
|---|---|
| `screen://` | primary monitor, 25 fps |
| `screen://N?fps=F` | monitor N (0, 1, … as listed by `--list-devices` / the panel), F fps |
| `screen://all` | every monitor in one picture, as arranged in Windows display settings |
| `screen://0+1` | monitors 0 and 1 side by side (any list, any order) |
| `agent://ID/SCREEN` | a screen of another PC (`SCREEN` as above); made by the panel's Agents tab |
| `webcam://` or `webcam://N` | first / N-th USB camera |
| `webcam://NAME` | camera whose name contains NAME |

The camera's resolution ladder follows the captured size, so a 1920×1080 screen offers 1080p, 720p,
360p, … to the VMS. A webcam is opened only once, even with main + sub + MJPEG + snapshots and any
number of clones. Notes:
* Screen capture needs a logged-in desktop. When VCam runs as a Windows service, it starts the
  capture inside the logged-in user's session (Session 0 cannot see the screen); before anyone logs
  in, screen cameras wait and start automatically at logon. RDP sessions capture only while the RDP
  window is open (minimised = frozen).
* H.264 and MJPEG are encoded by GStreamer (x264, jpegenc). For H.265, GStreamer prepares the frames
  and libx265 (bundled ffmpeg) encodes them, since GStreamer builds ship no software H.265 encoder.
* `copy` mode needs an encoded source and is ignored for screens and webcams.

## What is implemented (and where it comes from in the specs)

| Area | Details | Spec |
|---|---|---|
| **WS-Discovery** | Hello on start/scope change/reboot, Bye on stop, Probe→ProbeMatches (Types `dn:NetworkVideoTransmitter tds:Device`), rfc3986 + strcmp0 scope matching, Resolve, random 0–500 ms reply delay, stable `urn:uuid` | Core §7, Profile S §9 |
| **Scopes** | fixed: `type/…`, `Profile/Streaming`, `Profile/T`, `hardware/…`, `name/…`, `MAC/…`; configurable `location/…` | Core §7.3.2.2 |
| **Device service** | GetCapabilities, GetServices(+caps), GetServiceCapabilities, GetWsdlUrl, GetDeviceInformation, Get/SetSystemDateAndTime, SetSystemFactoryDefault, SystemReboot, Get/Set/Add/RemoveScopes, Get/SetDiscoveryMode, Get/SetHostname, Get/SetDNS, Get/SetNTP, Get/SetNetworkInterfaces, Get/SetNetworkProtocols, Get/SetNetworkDefaultGateway, GetUsers/CreateUsers/DeleteUsers/SetUser, GetRelayOutputs, GetEndpointReference | Profile S §7.2–7.6 |
| **Media (ver10)** | Get/Create/DeleteProfile(s), video sources & source configs (+options, compatible, add/remove/set), video encoder configs (+options incl. **JPEG option**, add/remove/set, guaranteed instances), metadata configs (all 7 ops), GetStreamUri (Unicast/Multicast × UDP/TCP/RTSP/HTTP), GetSnapshotUri, SetSynchronizationPoint, Start/StopMulticastStreaming | Profile S §7.8–7.13, §8.12 |
| **Media2 (ver20)** | GetProfiles (Type filter), Create/DeleteProfile, Add/RemoveConfiguration, encoder configs & options for **H.264 / H.265 / JPEG**, GetVideoEncoderInstances, GetStreamUri (RtspUnicast/RtspMulticast/RTSP/RtspOverHttp), snapshot, multicast, video source modes | Media2 spec, Profile T style |
| **Events** | GetEventProperties (fixed topic set), CreatePullPointSubscription, PullMessages, Renew, Unsubscribe, SetSynchronizationPoint, WS-BaseNotification Subscribe + push Notify, ConcreteSet topic filters, `Initialized`/`Changed` property semantics | Core §9, Profile S §7.7 |
| **Event topics** | `tns1:VideoSource/MotionAlarm`, `tns1:RuleEngine/CellMotionDetector/Motion` (simulated motion cycle + manual trigger), `tns1:Media/ConfigurationChanged` | Core §9.6 |
| **Authentication** | WS-UsernameToken PasswordDigest (nonce + Created required, optional clock tolerance & replay check), HTTP Digest RFC 2617 (MD5, optional SHA-256 RFC 7616), PRE_AUTH operations, Core default access policy (Administrator / Operator / User) | Core §5.9 |
| **Faults** | SOAP 1.2 faults with `ter:` subcodes: NotAuthorized, ActionNotSupported, InvalidArgVal/NoProfile/NoConfig/ConfigModify, FixedScope, DeletionOfFixedProfile, WellFormed …; Sender→HTTP 400, Receiver→500, missing creds→401 | Core §5.8 |
| **RTSP** | OPTIONS, DESCRIBE, SETUP, PLAY, PAUSE, TEARDOWN, GET_PARAMETER, SET_PARAMETER; aggregate control; `Session: …;timeout=60`; any request / RTCP RR = keep-alive; PLAY returns `Range: clock=…` + `RTP-Info` (seq, rtptime); `Require` unknown → 551 | Streaming §5.2 |
| **Transports** | RTP/UDP unicast, RTP/RTSP/TCP interleaved, **RTP/RTSP/HTTP/TCP** (Apple tunnel, on the RTSP *and* the ONVIF HTTP port), RTP/UDP **multicast** (per-encoder group/port/TTL, AutoStart) | Streaming §5.1.1 |
| **Payloads** | H.264 RFC 6184 (single NAL + FU-A, `sprop-parameter-sets`, SPS/PPS before every IDR), H.265 RFC 7798 (FU, `sprop-vps/sps/pps`), JPEG RFC 2435 (quant tables, DRI) | Streaming §5.1.2 |
| **RTCP** | Sender Reports (NTP wall-clock ↔ RTP time) + SDES CNAME, BYE on teardown | Streaming §5.1.2.2 |

Behaviour that makes it feel like a real camera:

* **One encoder per encoder configuration**, shared by all viewers (8 viewers = 1 encode).
* Encoders start on demand and stop when idle; every encoder joins the file at the
  same **live position**, so main and sub stream always show the same moment.
* The file loops forever with **continuous RTP timestamps** (no discontinuity at the loop).
* `SetVideoEncoderConfiguration` really re-encodes (resolution / fps / bitrate / GOP / codec);
  active sessions are dropped so the VMS reconnects to the new stream (like real devices).
* All configuration changes persist in `--state-dir` (default `./vcam-state`).
* Snapshot URI returns a JPEG of the current frame.

## Testing tools included

```bash
python tests/test_raw_soap.py   127.0.0.1 8080 8554 admin admin   # 40 protocol checks (stdlib)
pip install onvif-zeep-async                                       # optional 3rd-party client
python tests/test_with_onvif_client.py 127.0.0.1 8080 admin admin # 59 checks via official WSDLs
```

`test_with_onvif_client.py` uses the same library as Home Assistant's ONVIF integration; every
request/response is (de)serialised through the official ONVIF WSDL/XSD files.

Useful manual checks:

```bash
ffprobe -rtsp_transport udp  rtsp://admin:admin@HOST:8554/cam1/Profile_1
ffprobe -rtsp_transport http rtsp://admin:admin@HOST:8080/cam1/Profile_1   # tunnel via ONVIF port
ffplay  -rtsp_transport udp_multicast rtsp://admin:admin@HOST:8554/cam1/Profile_2/multicast
curl --digest -u admin:admin -X POST "http://HOST:8080/api/motion?state=1"  # fire motion event
```

## Control panel

`python -m vcam -c config.json --open` starts the web panel on `http://HOST:8000/` (same
admin credentials as the cameras; `--ui-port 0` disables it). Everything in it is offline, with no
CDN or web fonts. From the panel you can:

* add cameras from the video library (upload included), a file path or an RTSP URL, N at once
* edit streams (codec / resolution / fps / bitrate / CBR or VBR / GOP / copy), enable or disable, delete;
  edits keep the device's UUID and ONVIF port, so a VMS still sees the same device
* view live snapshots, copy ONVIF/RTSP URLs, fire motion events
* pick the IP announced to VMSs (multi-adapter hosts), change credentials
* watch active RTSP sessions, running encoders and the live log, with encoder and client IPs
* switch the language (🌐 English / فارسی, top bar); the choice is kept per browser

**CBR / VBR** (per stream, "Bitrate" column):
* **CBR** – constant bitrate: the stream uses its kbps all the time (filler data when the picture
  is still). Predictable bandwidth, like most real cameras' default.
* **VBR** (default) – constant quality, never above the kbps: a still screen costs almost nothing
  and motion is capped. Best for screen cameras.
* Agent PCs and the Android app encode with the camera's **main stream** setting.

Changes are saved to the `-c` file. On first run it is created from the given videos.
API: `tests/test_panel.py` exercises every endpoint (`vcam/ui.py` lists them).

## Stability notes for VMS testing

* **Several network adapters** (Hyper-V, WSL, Docker, VMware): choose the VMS-facing IP in the
  panel (or `--advertise-ip`). Discovery then announces only that address.
* **VMS in Docker or a NAT'd VM**: prefer RTP over RTSP (TCP) in the VMS. For UDP the device
  latches to the port the client's packets actually come from (symmetric RTP).
* Re-applying identical encoder settings (VMSs do this on every connect) no longer restarts the
  encoder or drops viewers. Only changes that alter the encoded stream do.
* Every RTSP session close is logged with its duration, packets sent and the client User-Agent.
* **Shared encoders:** cameras with the same source and effective settings (e.g. panel clones for
  stress tests) share one encoder. 20 viewers on 20 clone devices cost one encode. With GStreamer,
  all encoders of one source also share a single decoder / capture process.
* **Encoder watchdog:** an encoder or GStreamer source that produces no frames for 10-15 s
  (stalled RTSP relay, unplugged webcam, I/O hang) is restarted, and viewers resync on the next
  key frame.
* **Low delay:** live sources (screens, webcams, agents, phones, RTSP relays) go out as soon as
  they are encoded, through a 150 ms jitter buffer that drains instead of holding delay. A network
  or CPU hiccup no longer adds permanent delay, and a stream opened while its source is already
  running starts in real time. Typical end-to-end delay: ~0.15 s for this PC's screen, ~0.25 s for
  an agent PC (the VMS adds its own buffering on top).
* **Windowless mode** (`pythonw`, `START.bat`): encoders run without console windows and logs go
  to a rotating `vcam.log`. A second start only opens the panel; `--stop` / ⏻ shuts it down.

## Status page / API

`http://HOST:8080/` — devices, stream URIs, active RTSP sessions (transport, client, packets),
running encoders and SOAP call counters. JSON at `/api/status`.

### Windows service and tray icon

Run **`SERVICE-INSTALL.bat`** as administrator. It installs the `ONVIF-VCam` service (automatic
start, restart on failure) and a tray icon for every user:

* The service runs the cameras, RTSP server and panel from boot, without anyone logged in.
* Screen and webcam cameras are captured in the logged-in user's session, as remote-support tools do.
* The tray icon is coloured while VCam runs and grey when stopped. Its menu: open panel ·
  start / stop / restart service · agent start / stop · log · program folder. Interactive users
  may start and stop the service without elevation.
* `START.bat` / `STOP.bat` keep working; they start / stop the service once it is installed.
* `SERVICE-UNINSTALL.bat` removes the service and the tray autostart; settings and videos stay.

### Screens of other PCs (agent)

```
  other PC (agent service)                         VCam server
  screen -> GStreamer H.264 / MPEG-TS  ───TCP 8600 (outbound)──>  agent hub -> decode once ->
                                                                  main / sub / MJPEG / snapshot -> VMS
```

![Agents tab](docs/images/agents.jpg)

1. On the other PC run **`ONVIF-VCam-Agent-Setup-<version>.exe`** (from [Releases](../../releases),
   45 MB). The setup installs into `C:\ProgramData\ONVIF-VCam-Agent` and asks for the server IP
   (empty = find it by broadcast), port and agent key. It then registers and starts the service, and
   adds an uninstall entry under Windows "Installed apps". Alternatively copy the portable folder and
   run `AGENT-INSTALL.bat`.
2. The agent runs as a service, starts with Windows, and appears in the panel's **Agents** tab with
   its monitors.
3. Click **+ monitor N** or **+ all monitors**: a new ONVIF camera streams that screen. Or pick
   "screen of another PC" as the source in the camera editor.

Notes:
* The agent connects *out* to the server, so the other PC needs no inbound firewall rule. The server
  needs TCP 8600 (and UDP 8600 for auto-discovery) open; `FIREWALL.bat` already allows Python.
* **Agent key** (panel → Network & access): when set, only agents that know it are accepted. The key
  never crosses the network: it is checked with a challenge/response (HMAC-SHA256).
* The agent sends the native resolution at the camera's fps (`agent_bitrate_kbps`, default 6000);
  the server transcodes it to every profile the VMS asks for.
* The capture runs while a user is logged in to the other PC. When the agent goes offline, its
  cameras retry and resume by themselves.
* Silent install for many PCs (GPO, PDQ, SCCM…):
  `ONVIF-VCam-Agent-Setup-<version>.exe /S /SERVER=192.168.1.10 /KEY=secret [/PORT=8600] [/NAME=PC-01]`;
  silent uninstall: `"C:\ProgramData\ONVIF-VCam-Agent\uninstall.exe" /S`.
* Agent log: `C:\ProgramData\ONVIF-VCam-Agent\logs\vcam-agent.log` (Start menu → ONVIF VCam Agent).
* Linux / macOS agent: `python -m vcam.agent --server HOST` (X11 screen).

### Android phones as cameras (agent app)

Install **`ONVIF-VCam-Agent-Android-<version>.apk`** (from [Releases](../../releases), Android 8+)
on a phone. The phone becomes a screen agent: the VCam server sees its image sources as the
agent's "monitors" (0 = back camera, 1 = front camera, last = the phone's own screen), so the
panel's Agents tab turns them into ONVIF cameras like any PC screen. No change on the server is
needed.

1. Open **VCam Agent** and enter the server IP (empty = find it by broadcast). Add the agent key
   if one is set and press **Start**. Allow the camera.
2. In the VCam panel → **Agents**, the phone is listed. Press **+ monitor 0** (back camera),
   **+ monitor 1** (front camera) or the last monitor (phone screen).
3. **Image source** in the app: *Chosen by the panel* (default) follows the monitor picked in the
   panel; *Back camera*, *Front camera* or *Phone screen* force that source, and changing it
   switches the running stream within a few seconds.
4. **Phone screen** uses Android's screen capture: the app asks once ("Allow screen capture") each
   time the agent starts; the capture keeps running with the app in the background.

The app opens with a splash screen, has 6 colour themes (Cyber desk, Operational green, Data-center
blue, Amber NOC, Night purple, Light) and English / Persian, chosen in **⚙ Settings** together with
resolution, FPS, max bitrate and start after restart.

How it works: CameraX + the phone's hardware H.264 encoder (MediaCodec) → MPEG-TS → agent hub, in
a foreground service (persistent notification), so streaming continues with the screen off.

Notes:
* One source streams at a time (Android phones rarely allow both cameras together).
* **Allow running in the background** (button in the app). On some brands (Xiaomi, Huawei, Oppo…)
  also set the app's battery mode to *No restrictions*, or the system stops it.
* Keep the phone on a charger for long tests: continuous camera + encoder use heats it up.
* "Start after restart": Android 14+ doesn't allow a camera service to start at boot. The
  phone then shows a notification; one tap starts the agent.
* The APK is signed with a public test key (`android/keystore`). Build it yourself with your own key
  for production use (`android/keystore/README.md`).
* Source: [`android/`](android). GitHub Actions builds it and tests it in the Android emulator
  against a VCam server ([`.github/workflows/android.yml`](.github/workflows/android.yml)).

## Configuration file

See `config.example.json`. Per camera: `id`, `name`, `source`, `http_port`, identity fields
(`manufacturer`, `model`, `firmware`, `serial`, `mac`, `location`, `hostname`), `users`,
`motion`, and `profiles` (`name`, `encoding`, `width`, `height`, `fps`, `bitrate_kbps`,
`gop`, `profile`, `quality`, `cbr`, `mode: transcode|copy`). `source` is a file path, a URL,
`screen://N|all|N+M?fps=F`, `webcam://N|NAME` or `agent://ID/SCREEN?fps=F`.

Global keys: `rtsp_port`, `http_port_base`, `bind`, `advertise_ip` (NAT / Docker),
`discovery_interfaces`, `auth`, `rtsp_auth`, `digest_algorithms` (`["MD5","SHA-256"]`),
`allow_basic`, `wsse_clock_tolerance` (seconds, 0 = off), `wsse_strict`, `snapshot_auth`,
`engine` (`gstreamer` | `ffmpeg`), `x264_preset`, `encoder_idle_timeout`, `prestart_encoders`, `rtp_mtu`, `rtp_port_range`,
`max_profiles`, `media1_h265_as`, `state_dir`, `agent_port` (8600), `agent_key`,
`agent_bitrate_kbps`, `agents_enabled`.

## Deployment notes

* **Discovery** uses multicast `239.255.255.250:3702` — the VMS must be on the same L2 segment
  (or add devices by IP). In Docker use `--network host`.
* Each emulated camera has its own ONVIF HTTP port (8080, 8081, …) on the same IP; all share one
  RTSP port with per-camera paths. VMSs identify devices by endpoint UUID, so this works for
  discovery and recording. If your VMS insists on one device per IP, add IP aliases to the
  host and run one instance per IP with `--bind <ip>`.
* Windows: allow Python through the firewall (TCP 8080+/8554/8600, UDP 3702/8600 and RTP 50000-59999).
* For thousands of cameras prefer `--main copy` (no transcoding, near-zero CPU).

```bash
docker build -t vcam .
docker run --rm --network host -v $PWD/videos:/videos -v $PWD/state:/state vcam /videos/lobby.mp4 -n 4
```

## Troubleshooting: `WinError 10013` / `WinError 10048` (port in use)

```
PermissionError: [WinError 10013] An attempt was made to access a socket in a way forbidden by its access permissions
OSError: [WinError 10048] Only one usage of each socket address ... is normally permitted
```

The default ports (RTSP **8554**, ONVIF HTTP **8080+**) are already taken on that machine by another
program (a VMS / NVR, IIS, MediaMTX, go2rtc, ...) or reserved by Windows (Hyper-V, WSL, Docker).

**Fixed in 1.1.1** — VCam now moves to a free port by itself and logs it:

```
WARNING vcam.rtsp   RTSP port 8554 unavailable, using 18554 instead
WARNING vcam.soap   cam1: HTTP port 8080 unavailable, using 8081 instead
```

* RTSP falls back to 18554, 28554, 38554, 554. ONVIF clients get the real port from
  `GetStreamUri`, so nothing changes on the VMS side.
* A camera's HTTP port moves to the next free one and is **saved in `config.json`**, so the address
  stays the same on later starts. Add the camera in the VMS with the port shown in the panel /
  `vcam.log` (WS-Discovery already advertises the right address).
* Still on 1.1.0? Download 1.1.1 (keep your `config.json` and `videos\`), or set free ports
  yourself in `config.json`: `"rtsp_port": 18554`, `"http_port_base": 18080`.

Find out what holds a port (cmd):

```
netstat -ano | findstr :8554
tasklist /fi "PID eq <pid>"
netsh interface ipv4 show excludedportrange protocol=tcp
```

The control panel port (`ui_port`, 8000) does not move, because `PANEL.url` points to it; if it
is taken, change `ui_port` in `config.json`.

## Known limitations

* No TLS (HTTPS / RTSPS / SRTP), no audio, no PTZ, no imaging service, no recording/replay
  (Profile G), no RTSP-over-WebSocket. All are advertised as unsupported in the capabilities,
  so clients do not try them.
* Network settings (`SetNetworkInterfaces`, `SetNetworkProtocols`) are accepted and stored but the
  emulator keeps its host address and ports.
* `copy` mode sends the file's bitstream as-is: sources with B-frames produce non-monotonic
  timestamps (a warning is logged). The default transcode mode never uses B-frames.
* Media (ver10) cannot express H.265; such encoders are reported as `H264` there (configurable
  via `media1_h265_as`) and correctly as `H265` in Media2.


## Author

**Behrouz Kazemi** (بهروز کاظمی), VMS Systems Specialist
**IPS-Co.** · Telegram: [@B_8241](https://t.me/B_8241)

© 1405 IPS-Co. All rights reserved.

Third-party components in the portable bundle keep their own licences:
- FFmpeg (GPL): source at https://ffmpeg.org and https://github.com/conda-forge/ffmpeg-feedstock
- GStreamer (LGPL; gst-plugins-ugly x264 and gst-libav: GPL / LGPL): source at
  https://gstreamer.freedesktop.org and https://github.com/conda-forge/gstreamer-feedstock
- PyGObject (LGPL)
- Python (PSF)
- Vazirmatn font (SIL OFL)
