## ONVIF VCam 1.3.1: setup program for the screen agent

### New
- **`ONVIF-VCam-Agent-Setup-1.3.1.exe` (45 MB):** copy this one file to a client PC and run it.
  1. It installs the agent into `C:\ProgramData\ONVIF-VCam-Agent`, with only what the agent needs
     (Python, GStreamer capture / encode, pywin32).
  2. It asks for the **VCam server IP** (empty = find it automatically on the network), the
     **port** (8600), the **agent key** and the name shown in the panel.
  3. It registers and **starts the `ONVIF-VCam-Agent` service**, which starts with Windows.
  4. It adds Start-menu shortcuts (agent log, uninstall) and an entry under Windows **Installed apps**.

  The PC then appears in the VCam panel → Agents, ready to turn its monitors into cameras.
- **Silent install for many PCs** (GPO / PDQ / SCCM):
  `ONVIF-VCam-Agent-Setup-1.3.1.exe /S /SERVER=192.168.1.10 /KEY=secret [/PORT=8600] [/NAME=PC-01]`.
  Silent uninstall: `"C:\ProgramData\ONVIF-VCam-Agent\uninstall.exe" /S`.
- Running the setup again upgrades in place; the agent's identity, settings and logs are kept.
- The setup can be shown in English or Persian (فارسی).

### Fixed
- The portable bundle was missing two GStreamer plugins: AVI files and HTTP sources failed. Both
  plugins are included again.
- Agent logs from the installed agent go to a `logs` folder that the logged-in user can write to.
  The program files stay admin-only.

### نسخه 1.3.1 (فارسی)
- **برنامه نصب ایجنت (`ONVIF-VCam-Agent-Setup-1.3.1.exe`):**
  1. فقط همین یک فایل را روی کامپیوتر مقصد اجرا کنید. ایجنت در `C:\ProgramData\ONVIF-VCam-Agent`
     نصب می‌شود.
  2. برنامه IP سرور (خالی = پیدا کردن خودکار)، پورت و کلید ایجنت را می‌پرسد.
  3. سرویس `ONVIF-VCam-Agent` ثبت و اجرا می‌شود، و برنامه در «برنامه‌های نصب‌شده» ویندوز قابل حذف است.
- نصب بی‌صدا برای تعداد زیادی کامپیوتر:
  `ONVIF-VCam-Agent-Setup-1.3.1.exe /S /SERVER=192.168.1.10 /KEY=secret`

### Downloads
- **ONVIF-VCam-1.3.1-Portable-win64.zip**: the VCam server, standalone and offline.
- **ONVIF-VCam-Agent-Setup-1.3.1.exe**: the screen agent for other PCs (64-bit Windows 8.1+).

---
Created by **Behrouz Kazemi**, VMS Systems Specialist · IPS-Co. · Telegram: [@B_8241](https://t.me/B_8241)
© 1405 IPS-Co. All rights reserved.
_Bundled third-party software: GStreamer (LGPL; x264 / gst-libav GPL), FFmpeg (GPL), PyGObject (LGPL),
pywin32 (PSF), NSIS installer (zlib licence). Sources: https://gstreamer.freedesktop.org · https://ffmpeg.org ·
https://github.com/conda-forge_
