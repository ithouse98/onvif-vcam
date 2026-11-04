## ONVIF VCam 1.4.2: batch camera actions, JPEG down to 0.01 fps, new Android agent design

### New
- **Batch actions for cameras.** Press **Select cameras** on the control panel, tick the cameras
  (or click their pictures), then **Start**, **Stop** or **Delete** all of them at once. **All** and
  **None** select every camera or none. Works in English and Persian. Stop keeps the camera (it can be
  started again); Delete asks once for the whole selection and keeps the video files.
- **MJPEG down to 0.01 fps:** one picture every 100 s (0.05 = every 20 s). Also available over ONVIF
  Media2. Streams this slow no longer get restarted by the watchdog.
- **Android agent redesigned (1.4.2):** one Start / Stop button that changes colour with the state,
  the image source as a 2 × 2 grid, the resolution as equal segments in Settings.

### Fixed
- Very slow MJPEG streams (0.05 and 0.01 fps) were treated as frozen and restarted: the watchdog now
  allows for the time between pictures.

### نسخه 1.4.2 (فارسی)
- **عملیات گروهی روی دوربین‌ها:** دکمه «انتخاب دوربین‌ها» را بزنید، دوربین‌ها را علامت بزنید (یا روی تصویرشان کلیک کنید)، و سپس همه را با «شروع»، «توقف» یا «حذف» تغییر دهید. «همه» و «هیچ‌کدام» برای انتخاب سریع است. «توقف» دوربین را نگه می‌دارد؛ «حذف» یک بار تأیید می‌گیرد و فایل‌های ویدیو را نگه می‌دارد.
- **MJPEG تا 0.01 فریم در ثانیه:** یک تصویر هر ۱۰۰ ثانیه (۰.۰۵ یعنی هر ۲۰ ثانیه). این نرخ در ONVIF Media2 هم قابل تنظیم است.
- **طراحی جدید ایجنت اندروید (1.4.2):** یک دکمه شروع / توقف که با وضعیت رنگ عوض می‌کند، منبع تصویر به صورت شبکه ۲×۲ و وضوح تصویر در تنظیمات.
- **اصلاح:** استریم‌های بسیار کند MJPEG (0.05 و 0.01 فریم در ثانیه) به اشتباه منجمد تشخیص داده و ریستارت می‌شدند؛ حالا فاصله بین تصاویر در نظر گرفته می‌شود.

### Downloads
- **ONVIF-VCam-Setup-1.4.2.exe**: setup for the server and / or the agent (64-bit Windows 8.1+).
- **ONVIF-VCam-1.4.2-Portable-win64.zip**: the VCam server, standalone and offline (no installation).
- **ONVIF-VCam-Agent-Setup-1.4.2.exe**: the small agent-only setup for rolling out to many PCs.
- **ONVIF-VCam-Agent-Android-1.4.2.apk**: the Android agent app.

---
Created by **Behrouz Kazemi**, VMS Systems Specialist · IPS-Co. · Telegram: [@B_8241](https://t.me/B_8241)
© 1405 IPS-Co. All rights reserved.
_Bundled third-party software: GStreamer (LGPL; x264 / gst-libav GPL), FFmpeg (GPL), PyGObject (LGPL),
pywin32 (PSF), NSIS installers (zlib licence), Vazirmatn and IBM Plex Sans Arabic fonts (OFL)._
