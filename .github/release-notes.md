## ONVIF VCam 1.4.1: stream quality, MJPEG bitrate and sub-1 fps, redesigned control panel

### New
- **Quality per stream: High / Medium / Low.** VBR: picture quality (CRF 21 / 26 / 30). H.264 / H.265
  (CBR too): encoder effort, High = a better picture at the same bitrate. JPEG: JPEG quality
  (81 / 59 / 37). Measured on the test clip: H.264 VBR High 4.1 Mbps vs Low 1.5 Mbps. Also set over
  ONVIF (Quality).
- **MJPEG below 1 fps:** e.g. 0.5 (one picture every 2 s) or 0.3 fps, from the panel or ONVIF Media2.
- **Redesigned control panel:** a camera wall where every camera is a monitor with CCTV-style
  on-screen text (name, a red tally light while someone watches, resolution and fps, snapshot time,
  id and port), quieter controls, six reworked themes (Control room, Night shift, Graphite, NOC amber,
  Dusk, Daylight). Persian in Vazirmatn, English in IBM Plex (both bundled, offline).
- **Wider camera editor:** two columns, the whole stream table visible: no scroll bars at
  1366×768 or larger.

### Fixed
- **MJPEG ignored the bitrate (CBR or VBR):** JPEG quality is now steered frame by frame toward
  the size the bitrate allows: CBR holds the bitrate (600 kbps set: 637 measured), VBR keeps the
  chosen quality and only lowers it to stay under the limit (800 kbps cap: 765 measured). The editor
  no longer greys out CBR for JPEG.
- Low-quality MJPEG frames could be cut short (their quantisation tables can contain the JPEG end
  marker), which stopped such streams.
- Snapshots of an idle source could fail: the source watchdog took the silent source for frozen and
  stopped it. A source that really goes silent while encoders wait is now restarted.
- Two MJPEG streams with the same size but a different bitrate shared one encoder.

### نسخه 1.4.1 (فارسی)
- **کیفیت برای هر استریم (بالا / متوسط / پایین):** روی کیفیت تصویر VBR، دقت انکودر H.264 / H.265 و کیفیت JPEG اعمال می‌شود.
- **MJPEG:** سقف بیت‌ریت و حالت CBR حالا اعمال می‌شود و نرخ فریم می‌تواند کمتر از ۱ باشد (مثلاً 0.5 یعنی هر ۲ ثانیه یک تصویر).
- **طراحی جدید پنل مدیریت:** دیوار تصویر با نوشته‌های روی تصویر مثل دستگاه‌های CCTV، شش تم بازطراحی‌شده، فونت وزیر برای فارسی.
- **پنجره ویرایش دوربین عریض‌تر:** بدون نوار اسکرول افقی و عمودی.

### Downloads
- **ONVIF-VCam-Setup-1.4.1.exe**: setup for the server and / or the agent (64-bit Windows 8.1+).
- **ONVIF-VCam-1.4.1-Portable-win64.zip**: the VCam server, standalone and offline (no installation).
- **ONVIF-VCam-Agent-Setup-1.4.1.exe**: the small agent-only setup for rolling out to many PCs.
- **ONVIF-VCam-Agent-Android-1.4.0.apk**: the Android agent app (unchanged in this release).

---
Created by **Behrouz Kazemi**, VMS Systems Specialist · IPS-Co. · Telegram: [@B_8241](https://t.me/B_8241)
© 1405 IPS-Co. All rights reserved.
_Bundled third-party software: GStreamer (LGPL; x264 / gst-libav GPL), FFmpeg (GPL), PyGObject (LGPL),
pywin32 (PSF), NSIS installers (zlib licence), Vazirmatn and IBM Plex Sans Arabic fonts (OFL).
Sources: https://gstreamer.freedesktop.org · https://ffmpeg.org · https://github.com/conda-forge_
