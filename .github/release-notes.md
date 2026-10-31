## ONVIF VCam 1.2.0: GStreamer engine, screen and webcam cameras

### New
- **GStreamer stream engine (default).** One GStreamer process per source decodes the file (or
  captures the device) once and feeds every encoder of that camera: main, sub, MJPEG and snapshots.
  All streams of a camera show the same moment, and files loop seamlessly without restarting the
  encoder. The previous ffmpeg engine stays available (`"engine": "ffmpeg"` / `--engine ffmpeg`).
- **Screen as an ONVIF camera** (`screen://`): the desktop of the PC, with the mouse pointer.
  Choose the monitor and capture FPS in the panel. Windows uses Direct3D 11 desktop duplication,
  with GDI as a fallback.
- **USB webcam / capture card as an ONVIF camera** (`webcam://`): connected cameras are listed in
  the panel. Windows uses Media Foundation, with KS / DirectShow as fallbacks. The webcam is opened
  once, even with several streams, snapshots and clones.
- **Panel:** source picker (file / screen / webcam / network URL), device list, and the active
  engine shown in the header.
- **CLI:** `--engine`, `--list-devices`, and `screen://` / `webcam://` sources.

### Notes
- H.264 and MJPEG are encoded by GStreamer (x264 / jpegenc). For H.265, GStreamer prepares the
  frames and libx265 (bundled ffmpeg) encodes them, because GStreamer builds include no software
  H.265 encoder.
- Screen capture needs an interactive desktop. It does not run as a Windows service, and over RDP
  it updates only while the RDP window is open.
- Settings, cameras and ports from 1.1.x keep working. Existing file cameras switch to GStreamer
  automatically.

**Upgrading from 1.1.x:** run `STOP.bat`, extract the new zip, copy your `config.json` and `videos\`
into it, then run `START.bat`.

### نسخه 1.2.0 (فارسی)
- **موتور استریم GStreamer** به‌جای ffmpeg (ffmpeg همچنان قابل انتخاب است). هر منبع فقط یک بار
  دیکد یا ضبط می‌شود و همه استریم‌های دوربین از آن تغذیه می‌شوند. تکرار فایل بدون مکث است.
- **صفحه نمایش به‌عنوان دوربین ONVIF** (`screen://`)، با انتخاب مانیتور و FPS در پنل.
- **وب‌کم و دوربین USB به‌عنوان دوربین ONVIF** (`webcam://`)، با فهرست خودکار دستگاه‌ها در پنل.
- برای آپدیت: `STOP.bat`، فایل زیپ جدید را باز کنید، `config.json` و پوشه `videos` را داخلش کپی کنید
  و `START.bat` را اجرا کنید.

### Download
**ONVIF-VCam-1.2.0-Portable-win64.zip**: standalone and offline for Windows 64-bit
(Python + GStreamer + FFmpeg bundled). Extract it, then run `START.bat`.

---
Created by **Behrouz Kazemi**, VMS Systems Specialist · IPS-Co. · Telegram: [@B_8241](https://t.me/B_8241)
© 1405 IPS-Co. All rights reserved.
_Bundled third-party software: GStreamer (LGPL; x264 / gst-libav GPL), FFmpeg (GPL), PyGObject (LGPL).
Sources: https://gstreamer.freedesktop.org · https://ffmpeg.org · https://github.com/conda-forge_
