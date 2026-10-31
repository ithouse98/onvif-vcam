## ONVIF VCam 1.3.0: multi-monitor capture, screen agent, Windows service and tray icon

### New
- **Several monitors in one stream.** `screen://all` composes every monitor as arranged in Windows
  display settings, e.g. two 1080p monitors side by side become one 3840×1080 camera.
  `screen://0+1` puts chosen monitors side by side in any order. Both are in the panel's monitor list.
- **Screens of other PCs (screen agent).** Copy the folder to another PC and run
  `AGENT-INSTALL.bat`. The agent installs as a Windows service, finds the VCam server on the
  network (or uses the IP you enter) and appears in the panel's new **Agents** tab with its
  monitors. One click turns a monitor, or all of them, into an ONVIF camera. The agent connects
  out to the server, so the other PC needs no inbound firewall rule. An optional agent key is
  checked by challenge/response.
- **Windows service + tray icon.** `SERVICE-INSTALL.bat` runs VCam as the `ONVIF-VCam` service:
  it starts with Windows even before anyone logs in, and restarts on failure. A tray icon next to
  the clock (coloured = running, grey = stopped) opens the panel, starts / stops / restarts the
  service and the agent, and opens the logs. Screen and webcam cameras are captured in the
  logged-in user's session, because a service in Session 0 cannot see the screen. They start by
  themselves once a user logs in.
- Screen, webcam and agent cameras that cannot start yet are retried automatically.

### Notes
- Server ports: TCP 8600 for agents, UDP 8600 for auto-discovery (FIREWALL.bat already allows Python).
- `START.bat` / `STOP.bat` start and stop the service once it is installed. `SERVICE-UNINSTALL.bat`
  and `AGENT-UNINSTALL.bat` remove the services; settings and videos stay.
- Settings and cameras from 1.2.x keep working.

**Upgrading from 1.2.x:** run `STOP.bat`, extract the new zip, copy your `config.json` and `videos\`
into it, then run `START.bat` or `SERVICE-INSTALL.bat`.

### نسخه 1.3.0 (فارسی)
- **چند مانیتور در یک استریم:** `screen://all` همه مانیتورها را طبق چیدمان ویندوز در یک تصویر
  عریض می‌گذارد (دو مانیتور 1080p = یک دوربین 3840×1080)؛ `screen://0+1` مانیتورهای انتخابی را
  کنار هم می‌گذارد.
- **صفحه نمایش کامپیوترهای دیگر (ایجنت):** پوشه را روی کامپیوتر دیگر کپی و `AGENT-INSTALL.bat`
  را اجرا کنید. ایجنت به‌صورت سرویس نصب می‌شود، سرور را در شبکه پیدا می‌کند و در تب «ایجنت‌ها»
  ظاهر می‌شود. با یک کلیک هر مانیتورش دوربین ONVIF می‌شود.
- **سرویس ویندوز + آیکون کنار ساعت:** `SERVICE-INSTALL.bat` برنامه را به‌صورت سرویس نصب می‌کند
  (اجرا با روشن شدن ویندوز، حتی قبل از ورود کاربر). مدیریت از آیکون کنار ساعت انجام می‌شود:
  پنل، شروع / توقف / راه‌اندازی مجدد و لاگ.
- برای آپدیت: `STOP.bat`، فایل زیپ جدید را باز کنید، `config.json` و پوشه `videos` را داخلش کپی
  کنید و `START.bat` یا `SERVICE-INSTALL.bat` را اجرا کنید.

### Download
**ONVIF-VCam-1.3.0-Portable-win64.zip**: standalone and offline for Windows 64-bit
(Python + GStreamer + FFmpeg + pywin32 bundled). The same folder is also the agent for other PCs.

---
Created by **Behrouz Kazemi**, VMS Systems Specialist · IPS-Co. · Telegram: [@B_8241](https://t.me/B_8241)
© 1405 IPS-Co. All rights reserved.
_Bundled third-party software: GStreamer (LGPL; x264 / gst-libav GPL), FFmpeg (GPL), PyGObject (LGPL),
pywin32 (PSF). Sources: https://gstreamer.freedesktop.org · https://ffmpeg.org · https://github.com/conda-forge_
