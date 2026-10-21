<p align="center">
  <img src="docs/images/logo.png" width="220" alt="IPS-Co.">
</p>

<h1 align="center">ONVIF VCam</h1>
<p align="center"><b>Turn any video file into a standards-compliant ONVIF IP camera for VMS testing.</b><br>
Virtual ONVIF Profile S / T camera with WS-Discovery, RTSP streaming, events and a web control panel. Fully offline and portable.</p>

<p align="center">
  <img alt="version" src="https://img.shields.io/badge/version-1.1.0-0f6fb3">
  <img alt="ONVIF" src="https://img.shields.io/badge/ONVIF-Profile%20S%20%2F%20T-38bdf8">
  <img alt="platform" src="https://img.shields.io/badge/Windows%20x64-portable%20%26%20offline-2fbf71">
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

**Streaming**
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
  - an encoder watchdog restarts a frozen ffmpeg
  - the device keeps a stable address on hosts with several network adapters
- **Diagnostics:** every RTSP session logs its client software, transport, duration and packet count

**Web control panel** (port 8000, Persian UI, Vazirmatn font, works offline)
- **Cameras:** add from the video library (with upload), a file path or an RTSP URL; edit, enable/disable, delete
- **Live view:** snapshot previews with an on/off switch to save CPU; copy ONVIF/RTSP URLs in one click
- **Monitoring:** active sessions, encoders and a live log
- **Network:** choose the IP announced to the VMS; change credentials
- **Themes:** data-center blue, operational green, dark, amber NOC, night purple, light

**Portable Windows bundle**
- **Self-contained:** Python 3.12 and a GPL ffmpeg (libx264/libx265) are inside one folder; nothing to install, no internet
- **Background mode:** runs without a console window; stop from the panel, STOP.bat or autostart scripts; one-click firewall setup

![Themes](docs/images/themes.jpg)

| Camera editor | Clones for stress tests |
|---|---|
| ![editor](docs/images/editor.jpg) | ![clone](docs/images/clone.jpg) |

## Download & run (Windows)

1. Download **`ONVIF-VCam-1.1.0-Portable-win64.zip`** from [Releases](../../releases) and extract it
   (e.g. to `C:\ONVIF-VCam`).
2. Run **`FIREWALL.bat`** once as administrator.
3. Run **`START.bat`**. The control panel opens at `http://127.0.0.1:8000` (login `admin` / `admin`).
4. In your VMS, discover the cameras or add them manually. The ONVIF port of each camera is shown in
   the panel (8080, 8081, …).

| File | Purpose |
|---|---|
| `START.bat` / `STOP.bat` | run in the background / stop |
| `START-DEBUG.bat` | run with a console and verbose log |
| `AUTOSTART-ON.bat` / `AUTOSTART-OFF.bat` | start with Windows |
| `TEST.bat` | automated test suites (125 checks) |
| `SHELL.bat` | prompt with python / ffmpeg / ffprobe / ffplay |

The Windows launchers live in [`windows/`](windows). [`scripts/build_portable.sh`](scripts/build_portable.sh)
rebuilds the bundle from conda-forge and PyPI.

## Quick start (Python / any OS)

```bash
# needs: python3 >= 3.9 and ffmpeg/ffprobe in PATH
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
python -m vcam -c config.example.json                 # full control (see file)
python -m vcam a.mp4 --http-port 80 --rtsp-port 554   # camera-like ports (needs admin/root)
```

Stream spec: `ENC[:WxH][@FPS][:KBPS][:gop=N]` with `ENC` = `H264 | H265 | JPEG`, or `copy`.

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
* edit streams (codec / resolution / fps / bitrate / GOP / copy), enable or disable, delete;
  edits keep the device's UUID and ONVIF port, so a VMS still sees the same device
* view live snapshots, copy ONVIF/RTSP URLs, fire motion events
* pick the IP announced to VMSs (multi-adapter hosts), change credentials
* watch active RTSP sessions, running encoders and the live log

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
  stress tests) share one ffmpeg encoder. 20 viewers on 20 clone devices cost one encode.
* **Encoder watchdog:** an ffmpeg that produces no frames for 10 s (stalled RTSP relay, I/O hang)
  is restarted, and viewers resync on the next key frame.
* **Windowless mode** (`pythonw`, `START.bat`): ffmpeg runs without console windows and logs go
  to a rotating `vcam.log`. A second start only opens the panel; `--stop` / ⏻ shuts it down.

## Status page / API

`http://HOST:8080/` — devices, stream URIs, active RTSP sessions (transport, client, packets),
running encoders and SOAP call counters. JSON at `/api/status`.

## Configuration file

See `config.example.json`. Per camera: `id`, `name`, `source`, `http_port`, identity fields
(`manufacturer`, `model`, `firmware`, `serial`, `mac`, `location`, `hostname`), `users`,
`motion`, and `profiles` (`name`, `encoding`, `width`, `height`, `fps`, `bitrate_kbps`,
`gop`, `profile`, `quality`, `cbr`, `mode: transcode|copy`).

Global keys: `rtsp_port`, `http_port_base`, `bind`, `advertise_ip` (NAT / Docker),
`discovery_interfaces`, `auth`, `rtsp_auth`, `digest_algorithms` (`["MD5","SHA-256"]`),
`allow_basic`, `wsse_clock_tolerance` (seconds, 0 = off), `wsse_strict`, `snapshot_auth`,
`x264_preset`, `encoder_idle_timeout`, `prestart_encoders`, `rtp_mtu`, `rtp_port_range`,
`max_profiles`, `media1_h265_as`, `state_dir`.

## Deployment notes

* **Discovery** uses multicast `239.255.255.250:3702` — the VMS must be on the same L2 segment
  (or add devices by IP). In Docker use `--network host`.
* Each emulated camera has its own ONVIF HTTP port (8080, 8081, …) on the same IP; all share one
  RTSP port with per-camera paths. VMSs identify devices by endpoint UUID, so this works for
  discovery and recording. If your VMS insists on one device per IP, add IP aliases to the
  host and run one instance per IP with `--bind <ip>`.
* Windows: allow Python through the firewall (TCP 8080+/8554, UDP 3702 and RTP 50000-59999).
* For thousands of cameras prefer `--main copy` (no transcoding, near-zero CPU).

```bash
docker build -t vcam .
docker run --rm --network host -v $PWD/videos:/videos -v $PWD/state:/state vcam /videos/lobby.mp4 -n 4
```

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
- Python (PSF)
- Vazirmatn font (SIL OFL)
