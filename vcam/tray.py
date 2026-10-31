"""ONVIF VCam tray icon (Windows): status at a glance and start / stop / restart / panel / log.

    pythonw.exe app\\vcam\\tray.py        (started at logon once the service is installed)

Works with both ways of running VCam: as the Windows service (SERVICE-INSTALL.bat) or in the
background with START.bat. Double-click opens the control panel.
"""

import json
import os
import socket
import subprocess
import sys
import threading
import time

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import win32api  # noqa: E402
import win32con  # noqa: E402
import win32event  # noqa: E402
import win32gui  # noqa: E402
import winerror  # noqa: E402

from vcam import winservice  # noqa: E402
from vcam.winservice import ROOT, APP  # noqa: E402

ASSETS = os.path.join(APP, "vcam", "web", "assets")
WM_TRAY = win32con.WM_USER + 20
(ID_PANEL, ID_START, ID_STOP, ID_RESTART, ID_AG_START, ID_AG_STOP, ID_LOG, ID_AGLOG, ID_FOLDER,
 ID_INSTALL, ID_EXIT) = range(1023, 1034)


def config():
    try:
        with open(os.path.join(ROOT, "config.json"), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def panel_port():
    return int(config().get("ui_port") or 8000)


def panel_up():
    try:
        with socket.create_connection(("127.0.0.1", panel_port()), timeout=0.5):
            return True
    except OSError:
        return False


class Tray:
    def __init__(self):
        self.svc = "not installed"
        self.agent = "not installed"
        self.up = False
        msg_restart = win32gui.RegisterWindowMessage("TaskbarCreated")
        wc = win32gui.WNDCLASS()
        self.hinst = wc.hInstance = win32api.GetModuleHandle(None)
        wc.lpszClassName = "ONVIFVCamTray"
        wc.lpfnWndProc = {msg_restart: self.on_restart, win32con.WM_DESTROY: self.on_destroy,
                          win32con.WM_COMMAND: self.on_command, WM_TRAY: self.on_notify}
        win32gui.RegisterClass(wc)
        self.hwnd = win32gui.CreateWindow(wc.lpszClassName, "ONVIF VCam", win32con.WS_OVERLAPPED, 0, 0,
                                          0, 0, 0, 0, self.hinst, None)
        flags = win32con.LR_LOADFROMFILE | win32con.LR_DEFAULTSIZE
        self.icon_on = win32gui.LoadImage(self.hinst, os.path.join(ASSETS, "vcam.ico"),
                                          win32con.IMAGE_ICON, 0, 0, flags)
        self.icon_off = win32gui.LoadImage(self.hinst, os.path.join(ASSETS, "vcam-off.ico"),
                                           win32con.IMAGE_ICON, 0, 0, flags)
        self.added = False
        self.refresh()
        threading.Thread(target=self._poll, daemon=True).start()

    # ---------------------------------------------------------------- icon
    def tip(self):
        mode = {"running": "سرویس: در حال اجرا", "stopped": "سرویس: متوقف",
                "starting": "سرویس: در حال شروع…", "stopping": "سرویس: در حال توقف…"}.get(self.svc)
        if mode is None:
            mode = "در حال اجرا (بدون سرویس)" if self.up else "متوقف"
        return f"ONVIF VCam — {mode}"[:127]

    def refresh(self):
        self.svc = winservice.status("vcam")
        self.agent = winservice.status("agent")
        self.up = panel_up()
        icon = self.icon_on if self.up else self.icon_off
        data = (self.hwnd, 0, win32gui.NIF_ICON | win32gui.NIF_MESSAGE | win32gui.NIF_TIP, WM_TRAY,
                icon, self.tip())
        try:
            win32gui.Shell_NotifyIcon(win32gui.NIM_MODIFY if self.added else win32gui.NIM_ADD, data)
            self.added = True
        except win32gui.error:  # explorer not ready yet (logon): retried on the next poll
            self.added = False

    def _poll(self):
        while True:
            time.sleep(3)
            try:
                self.refresh()
            except Exception:  # noqa
                pass

    def balloon(self, text):
        try:
            win32gui.Shell_NotifyIcon(win32gui.NIM_MODIFY, (
                self.hwnd, 0, win32gui.NIF_INFO, WM_TRAY, self.icon_on, self.tip(), text[:255], 5000,
                "ONVIF VCam", win32gui.NIIF_INFO))
        except win32gui.error:
            pass

    # ---------------------------------------------------------------- window messages
    def on_restart(self, *_):
        self.added = False
        self.refresh()
        return 0

    def on_destroy(self, *_):
        try:
            win32gui.Shell_NotifyIcon(win32gui.NIM_DELETE, (self.hwnd, 0))
        except win32gui.error:
            pass
        win32gui.PostQuitMessage(0)
        return 0

    def on_notify(self, hwnd, msg, wparam, lparam):
        if lparam == win32con.WM_LBUTTONDBLCLK:
            self.open_panel()
        elif lparam in (win32con.WM_RBUTTONUP, win32con.WM_LBUTTONUP):
            self.menu()
        return 1

    def menu(self):
        self.refresh()
        m = win32gui.CreatePopupMenu()
        add = lambda i, t, on=True: win32gui.AppendMenu(m, win32con.MF_STRING | (0 if on else win32con.MF_GRAYED), i, t)  # noqa
        sep = lambda: win32gui.AppendMenu(m, win32con.MF_SEPARATOR, 0, "")  # noqa
        add(ID_PANEL, "باز کردن پنل مدیریت", self.up)
        sep()
        installed = self.svc != "not installed"
        if installed:
            add(0, self.tip().split("— ")[-1], False)
            add(ID_START, "شروع سرویس", self.svc == "stopped")
            add(ID_STOP, "توقف سرویس", self.svc == "running")
            add(ID_RESTART, "راه‌اندازی مجدد سرویس", self.svc == "running")
        else:
            add(ID_START, "شروع (START.bat)", not self.up)
            add(ID_STOP, "توقف", self.up)
            add(ID_INSTALL, "نصب به‌عنوان سرویس ویندوز…")
        if self.agent != "not installed":
            sep()
            add(0, "ایجنت صفحه: " + {"running": "در حال اجرا", "stopped": "متوقف"}.get(self.agent, self.agent), False)
            add(ID_AG_START, "شروع ایجنت", self.agent == "stopped")
            add(ID_AG_STOP, "توقف ایجنت", self.agent == "running")
            add(ID_AGLOG, "لاگ ایجنت")
        sep()
        add(ID_LOG, "باز کردن لاگ")
        add(ID_FOLDER, "باز کردن پوشه برنامه")
        sep()
        add(ID_EXIT, "بستن آیکون (برنامه اجرا می‌ماند)")
        win32gui.SetMenuDefaultItem(m, ID_PANEL, 0)
        pos = win32gui.GetCursorPos()
        win32gui.SetForegroundWindow(self.hwnd)
        win32gui.TrackPopupMenu(m, win32con.TPM_LEFTALIGN, pos[0], pos[1], 0, self.hwnd, None)
        win32gui.PostMessage(self.hwnd, win32con.WM_NULL, 0, 0)

    def on_command(self, hwnd, msg, wparam, lparam):
        cmd = win32api.LOWORD(wparam)
        threading.Thread(target=self.run_command, args=(cmd,), daemon=True).start()
        return 0

    def run_command(self, cmd):
        try:
            if cmd == ID_PANEL:
                self.open_panel()
            elif cmd == ID_START:
                if self.svc != "not installed":
                    winservice.start("vcam")
                    self.balloon("سرویس شروع شد")
                else:
                    subprocess.Popen(["cmd", "/c", os.path.join(ROOT, "START.bat"), "silent"],
                                     creationflags=0x08000000)
            elif cmd == ID_STOP:
                if self.svc != "not installed":
                    winservice.stop("vcam")
                    self.balloon("سرویس متوقف شد")
                else:
                    from vcam.__main__ import panel_call
                    panel_call(config(), "/api/shutdown")
            elif cmd == ID_RESTART:
                winservice.stop("vcam")
                winservice.start("vcam")
                self.balloon("سرویس دوباره راه‌اندازی شد")
            elif cmd == ID_AG_START:
                winservice.start("agent")
            elif cmd == ID_AG_STOP:
                winservice.stop("agent")
            elif cmd == ID_LOG:
                os.startfile(os.path.join(ROOT, "vcam.log"))
            elif cmd == ID_AGLOG:
                os.startfile(os.path.join(ROOT, "vcam-agent.log"))
            elif cmd == ID_FOLDER:
                os.startfile(ROOT)
            elif cmd == ID_INSTALL:
                win32api.ShellExecute(0, "runas", os.path.join(ROOT, "SERVICE-INSTALL.bat"), None, ROOT, 1)
            elif cmd == ID_EXIT:
                win32gui.DestroyWindow(self.hwnd)
                return
        except Exception as exc:  # noqa - e.g. access denied
            self.balloon(f"خطا: {exc}")
        self.refresh()

    def open_panel(self):
        os.startfile(f"http://127.0.0.1:{panel_port()}/")


def main():
    mutex = win32event.CreateMutex(None, False, "Local\\ONVIF-VCam-Tray")
    if win32api.GetLastError() == winerror.ERROR_ALREADY_EXISTS:
        return 0
    Tray()
    win32gui.PumpMessages()
    del mutex
    return 0


if __name__ == "__main__":
    sys.exit(main())
