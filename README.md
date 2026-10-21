# ONVIF Virtual Camera (vcam)

**Developed by Behrouz Kazemi** — VMS Systems Specialist · **IPS-Co.** · Telegram [@B_8241](https://t.me/B_8241)  
© 1405 — All rights reserved. Version 1.1.0

Turns any video file into a **standards-compliant ONVIF network camera** that every VMS can
discover, add, configure and record — no physical camera needed. Built from the official ONVIF
specifications (Core 26.06, Streaming 26.06, Profile S 1.2, Media / Media2 services).

* Pure Python 3.9+ standard library, plus **FFmpeg** for decoding/encoding. No pip packages.
* One process can emulate **many cameras** (load / scale testing of recording servers).
* All protocol work — WS-Discovery, SOAP, auth, RTSP, RTP packetization, RTCP — is done by the
  tool itself, so it behaves like a camera, not like a media server.

```
            ┌────────────────────────── vcam process ──────────────────────────┐
 video ───► │ ffmpeg (1 per encoder) ─► AU parser ─► RTP packetizer ─► fan-out │ ─► RTSP clients
  file      │                                    (H.264/H.265/JPEG)            │    UDP / TCP /
            │ WS-Discovery  ·  Device · Media · Media2 · Events (SOAP 1.2)      │    HTTP tunnel /
            └──────────────────────────────────────────────────────────────────┘    multicast
```

## Quick start

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
