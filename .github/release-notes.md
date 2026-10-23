## ONVIF VCam 1.1.1 — port fix

### Fixed
- **`PermissionError: [WinError 10013]` / `[WinError 10048]` on start** — happened when the default
  ports were taken by another program (VMS / NVR, IIS, MediaMTX, go2rtc) or reserved by Windows
  (Hyper-V / WSL / Docker).
  - RTSP now falls back from 8554 to 18554, 28554, 38554, 554. The VMS gets the real port from the
    camera, so nothing changes on its side.
  - A camera's ONVIF HTTP port (8080+) moves to the next free port and is saved in `config.json`,
    so the address stays the same on later starts. Adding cameras from the panel works the same way.
  - On Windows, ports are now opened exclusively, so two programs can no longer silently share one.

**Upgrading from 1.1.0:** run `STOP.bat`, extract the new zip, copy your `config.json` and `videos\`
into it, then run `START.bat`. If a camera moved to a new port, `vcam.log` and the panel show it.
More details: [Troubleshooting](https://github.com/ithouse98/onvif-vcam#troubleshooting-winerror-10013--winerror-10048-port-in-use)

### رفع خطای پورت (فارسی)
خطای `WinError 10013` / `10048` وقتی پیش می‌آمد که پورت 8554 یا 8080 توسط برنامه دیگری (VMS، IIS و ...)
گرفته شده یا ویندوز آن را رزرو کرده بود. حالا برنامه خودش پورت آزاد انتخاب می‌کند و پورت جدید را
در پنل و `vcam.log` نشان می‌دهد. برای آپدیت: `STOP.bat`، فایل زیپ جدید را باز کنید، `config.json`
و پوشه `videos` قبلی را داخلش کپی کنید و `START.bat` را اجرا کنید.

### Download
**ONVIF-VCam-1.1.1-Portable-win64.zip** — standalone & offline for Windows 64-bit. Extract, run `START.bat`.

---
Created by **Behrouz Kazemi** — VMS Systems Specialist · IPS-Co. · Telegram: [@B_8241](https://t.me/B_8241)
© 1405 IPS-Co. — All rights reserved.
_Includes FFmpeg (GPL build, conda-forge); source: https://ffmpeg.org/download.html_
