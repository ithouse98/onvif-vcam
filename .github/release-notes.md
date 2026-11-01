## ONVIF VCam 1.4.0: low-delay live streams, CBR / VBR per stream, English web console

### New: setup program for the whole platform
- **`ONVIF-VCam-Setup-1.4.0.exe`** asks what to install: the **server** (cameras + control panel),
  optionally **as a Windows service** with the tray icon (else it starts in the background at logon),
  the **screen agent**, or **server + agent**. It asks for the panel / camera login, adds the firewall
  rules, Start-menu / desktop shortcuts and an Installed apps entry, and upgrades in place keeping
  cameras, videos and settings. Silent: `/S [/INSTALL=server,service,agent] [/USER=] [/PASSWORD=]
  [/NOFIREWALL] [/SERVER=] [/PORT=] [/KEY=] [/NAME=]`.

### Stream delay fixed
Users reported an unacceptable delay on screen cameras and on the Android app. Measured end to end
(the source flips black / white, an RTSP client times the change):

| Situation | 1.3.1 | 1.4.0 |
|---|---|---|
| Screen sub stream opened 30 s after the main stream | **24.6 s** | 0.15 s |
| Agent / phone stream after a 2 s network stall | **2.3 s, permanently** | 0.25 s |
| Screen of this PC, steady state | 0.24 s | 0.15 s |
| Screen of an agent PC, steady state | 0.32 s | 0.25 s |

What was wrong, and what changed:
- **Old frames at stream start:** a stream that joined a source already running (a second profile,
  a new viewer) first received copies of the first frame for the whole time the source had run,
  sent at real time. That came out as seconds to minutes of delay.
- **Delay that never went away:** after any network or CPU hiccup, the delayed frames were spread
  out at the normal frame rate, so the delay stayed for good. Live sources now pass through a
  150 ms jitter buffer that drains itself; bursts go out at once.
- **One frame less** on every H.264 stream and on screen capture; the agent / phone decoder no
  longer holds one frame per CPU core (up to ~0.6 s on a 16-thread server).
- **Agents and phones:** they start capturing only when the server is ready to decode (no backlog
  at stream start) and keep at most ~0.25 s of video in their send buffer when the network slows.
  The phone encoder runs with realtime / low-latency settings.
- Slow RTSP clients resync after 2 s of backlog instead of 3 s.

### CBR / VBR for each stream
- New **Bitrate** column in the camera editor: **CBR** (constant bitrate, fixed bandwidth) or
  **VBR** (constant quality, capped at the kbps: a still screen costs almost nothing). H.264 and
  H.265, both engines; also settable over ONVIF Media2 (ConstantBitRate).
- The screen agent and the Android app encode with the camera's main stream setting.

### Web console
- **English** beside Persian: 🌐 language switch in the top bar (English uses a left-to-right layout).
- **IP addresses:** each camera shows the IP of the encoder feeding it (agent PC, phone, RTSP
  camera) and the IPs of connected clients; the Encoders tab lists source IP and client IPs.

### Android agent app 1.4.0
- Lower delay (see above), CBR / VBR from the server.

### نسخه 1.4.0 (فارسی)
- **رفع تأخیر استریم:** تأخیر صفحه نمایش و اپلیکیشن اندروید برطرف شد. استریمی که بعد از شروع منبع باز
  می‌شد (مثلاً استریم فرعی) قبلاً تا ده‌ها ثانیه عقب بود (۲۴٫۶ ثانیه در تست) و حالا ۰٫۱۵ ثانیه است؛
  بعد از قطعی کوتاه شبکه هم تأخیر دیگر دائمی نمی‌ماند (۲٫۳ ثانیه ← ۰٫۲۵ ثانیه).
- **CBR / VBR برای هر استریم:** ستون «نرخ بیت» در ویرایش دوربین. CBR = نرخ ثابت، VBR = کیفیت ثابت
  با سقف kbps (صفحه ثابت تقریباً پهنای باندی مصرف نمی‌کند). ایجنت و گوشی از تنظیم استریم اصلی پیروی می‌کنند.
- **برنامه نصب کامل (`ONVIF-VCam-Setup-1.4.0.exe`):** هنگام نصب انتخاب کنید: سرور، سرور به‌صورت سرویس ویندوز، ایجنت یا سرور + ایجنت.
- **پنل وب انگلیسی** در کنار فارسی (انتخاب زبان در نوار بالا).
- **نمایش آی‌پی** انکودر هر دوربین (کامپیوتر ایجنت، گوشی، دوربین RTSP) و آی‌پی کلاینت‌های متصل.

### Downloads
- **ONVIF-VCam-Setup-1.4.0.exe**: setup for the server and / or the agent (64-bit Windows 8.1+).
- **ONVIF-VCam-1.4.0-Portable-win64.zip**: the VCam server, standalone and offline (no installation).
- **ONVIF-VCam-Agent-Setup-1.4.0.exe**: the small agent-only setup (45 MB) for rolling out to many PCs.
  Agents from 1.3.x keep working; update them for the lower delay and CBR / VBR.
- **ONVIF-VCam-Agent-Android-1.4.0.apk**: the agent app for Android phones (Android 8+), signed
  with the same test key, so it installs over 1.3.1.

---
Created by **Behrouz Kazemi**, VMS Systems Specialist · IPS-Co. · Telegram: [@B_8241](https://t.me/B_8241)
© 1405 IPS-Co. All rights reserved.
_Bundled third-party software: GStreamer (LGPL; x264 / gst-libav GPL), FFmpeg (GPL), PyGObject (LGPL),
pywin32 (PSF), NSIS installers (zlib licence). Sources: https://gstreamer.freedesktop.org · https://ffmpeg.org ·
https://github.com/conda-forge_
