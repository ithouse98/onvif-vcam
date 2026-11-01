"""Windows services for ONVIF VCam and the screen agent (pywin32).

    python -m vcam.winservice install vcam [--user U --password P]   (run as administrator)
    python -m vcam.winservice config vcam --user U --password P     (first config.json only)
    python -m vcam.winservice install agent [--server HOST[:PORT]] [--key KEY] [--name NAME]
    python -m vcam.winservice remove|start|stop|restart|status vcam|agent

The service control manager starts:  runtime\\python.exe app\\vcam\\winservice.py --run vcam|agent

* vcam  - the whole VCam server (cameras, RTSP, panel) from boot, without anyone logged on.
          Screen / webcam sources are captured by helpers started in the logged-in user's
          session (vcam.winsession), because Session 0 cannot see the desktop.
* agent - keeps `python -m vcam.agent` running inside the active user session (restarted on
          logon / logoff / crash), so this PC's screens can be streamed to a VCam server.
Both are set to restart on failure and may be started / stopped by interactive users (tray).
"""

import os
import subprocess
import sys
import threading
import time

if __package__ in (None, ""):  # started by the service control manager as a plain script
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if "--run" in sys.argv:  # no console under the SCM: keep early errors (imports, DLLs) in a file
    try:
        _err_log = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                                f"service-{sys.argv[-1]}.log")
        sys.stderr = sys.stdout = open(_err_log, "a", encoding="utf-8", buffering=1)
        print(time.strftime("%Y-%m-%d %H:%M:%S"), "service process start:", sys.executable, sys.argv)
    except OSError:
        pass

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))     # ...\app
ROOT = os.path.dirname(APP)                                           # bundle folder
# the bundle's own python (ROOT\runtime\python.exe) is the reliable anchor: a vcam folder in the
# current directory must never end up registered as the service code
_rt_root = os.path.dirname(os.path.dirname(os.path.abspath(sys.executable)))
if os.path.isdir(os.path.join(_rt_root, "app", "vcam")):
    ROOT, APP = _rt_root, os.path.join(_rt_root, "app")
RUNTIME = os.path.join(ROOT, "runtime")

SERVICES = {
    "vcam": ("ONVIF-VCam", "ONVIF VCam",
             "Virtual ONVIF cameras for VMS testing (files, screens, webcams, remote screens)."),
    "agent": ("ONVIF-VCam-Agent", "ONVIF VCam Screen Agent",
              "Streams this PC's monitors to an ONVIF VCam server on the network."),
}
TRAY_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
TRAY_VALUE = "ONVIF-VCam Tray"
# interactive users (IU) may start / stop / query: the tray runs without elevation
SDDL = ("D:(A;;CCLCSWRPWPDTLOCRRC;;;SY)(A;;CCDCLCSWRPWPDTLOCRSDRCWDWO;;;BA)"
        "(A;;CCLCSWRPWPLOCRRC;;;IU)(A;;CCLCSWLOCRRC;;;SU)")


def prepare_env():
    """Services start with a bare environment: same settings as _env.cmd."""
    paths = [RUNTIME, os.path.join(RUNTIME, "Library", "bin"), os.path.join(RUNTIME, "Scripts")]
    os.environ["PATH"] = os.pathsep.join(paths + [os.environ.get("PATH", "")])
    os.environ["PYTHONPATH"] = APP
    os.environ["PYTHONUTF8"] = "1"
    os.environ["PYTHONNOUSERSITE"] = "1"
    os.chdir(ROOT)


def python_exe(windowless=False):
    exe = os.path.join(RUNTIME, "pythonw.exe" if windowless else "python.exe")
    return exe if os.path.exists(exe) else sys.executable


# --------------------------------------------------------------------------- service classes
try:
    import servicemanager
    import win32event
    import win32service
    import win32serviceutil
    _Base = win32serviceutil.ServiceFramework
except ImportError:  # not Windows / pywin32 missing: the CLI reports it
    servicemanager = None
    _Base = object


def _svc_log(msg, error=False):
    try:
        (servicemanager.LogErrorMsg if error else servicemanager.LogInfoMsg)(msg)
    except Exception:  # noqa
        pass


class VcamService(_Base):
    _svc_name_, _svc_display_name_, _svc_description_ = SERVICES["vcam"]

    def __init__(self, args):
        super().__init__(args)
        self.stop_evt = threading.Event()

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        self.stop_evt.set()
        from vcam import __main__ as m
        if m.CURRENT_APP is not None:
            m.CURRENT_APP.quit.set()

    def SvcDoRun(self):
        prepare_env()
        cfg = os.path.join(ROOT, "config.json")
        args = ["-c", cfg, "--log-file", os.path.join(ROOT, "vcam.log")]
        if not os.path.exists(cfg):  # first run, like START.bat: one camera per video
            args = [os.path.join(ROOT, "videos"), "--mjpeg"] + args
        _svc_log("ONVIF VCam service starting")
        print(time.strftime("%H:%M:%S"), "vcam service: running main", args)
        try:
            from vcam import __main__ as m
            m.main(args)
        except SystemExit as exc:
            if exc.code not in (0, None):
                _svc_log(f"ONVIF VCam stopped: {exc.code}", error=True)
                _append_log(f"service: {exc.code}")
        except Exception as exc:  # noqa
            import traceback
            _svc_log(traceback.format_exc(), error=True)
            _append_log("service crashed:\n" + traceback.format_exc())
            raise
        _svc_log("ONVIF VCam service stopped")


class AgentService(_Base):
    _svc_name_, _svc_display_name_, _svc_description_ = SERVICES["agent"]

    def __init__(self, args):
        super().__init__(args)
        self.stop_evt = threading.Event()

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        self.stop_evt.set()

    def SvcDoRun(self):
        prepare_env()
        from vcam import winsession
        # installer layout: a logs folder writable by users (the agent runs as the logged-in user,
        # the program files stay admin-only because SYSTEM runs them)
        logs = os.path.join(ROOT, "logs")
        log = os.path.join(logs if os.path.isdir(logs) else ROOT, "vcam-agent.log")
        cmd = [python_exe(), "-m", "vcam.agent", "--config", os.path.join(ROOT, "agent.json"),
               "--log-file", log]
        env = {"PYTHONPATH": APP, "PYTHONUTF8": "1", "PYTHONNOUSERSITE": "1",
               "PATH": os.pathsep.join([RUNTIME, os.path.join(RUNTIME, "Library", "bin")])}
        proc, sess, started, backoff = None, None, 0.0, 3
        _svc_log("ONVIF VCam agent service started")
        while not self.stop_evt.wait(3):
            try:
                sid = winsession.user_session()
            except Exception:  # noqa
                sid = None
            if proc is not None and (proc.poll() is not None or sid != sess):
                proc.kill()
                proc = None
                if time.time() - started < 30:  # crashing right away: slow down
                    self.stop_evt.wait(backoff)
                    backoff = min(backoff * 2, 60)
                else:
                    backoff = 3
            if proc is None and sid is not None:
                try:
                    proc = winsession.launch(cmd, sid, env=env, cwd=ROOT)
                    sess, started = sid, time.time()
                except Exception as exc:  # noqa
                    _append_log(f"agent service: cannot start the agent in session {sid}: {exc}", log)
                    self.stop_evt.wait(10)
        if proc is not None:
            proc.kill()
        _svc_log("ONVIF VCam agent service stopped")


def _append_log(msg, path=None):
    try:
        with open(path or os.path.join(ROOT, "vcam.log"), "a", encoding="utf-8") as fh:
            fh.write(time.strftime("%H:%M:%S ") + msg + "\n")
    except OSError:
        pass


# --------------------------------------------------------------------------- management
def _sc(*args):
    return subprocess.run(["sc.exe", *args], capture_output=True, text=True,
                          creationflags=0x08000000).stdout


def status(which):
    """'running' | 'stopped' | 'starting' | 'stopping' | 'not installed'"""
    try:
        st = win32serviceutil.QueryServiceStatus(SERVICES[which][0])[1]
    except Exception:  # noqa
        return "not installed"
    return {1: "stopped", 2: "starting", 3: "stopping", 4: "running"}.get(st, "unknown")


def install(which, opts):
    name, display, desc = SERVICES[which]
    if which == "agent":
        _write_agent_config(opts)
    else:
        _write_vcam_config(opts)
    binpath = f'"{python_exe()}" "{os.path.join(APP, "vcam", "winservice.py")}" --run {which}'
    scm = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_ALL_ACCESS)
    try:
        try:
            h = win32service.OpenService(scm, name, win32service.SERVICE_ALL_ACCESS)
            win32service.ChangeServiceConfig(
                h, win32service.SERVICE_NO_CHANGE, win32service.SERVICE_AUTO_START,
                win32service.SERVICE_NO_CHANGE, binpath, None, 0, None, None, None, display)
        except win32service.error:
            h = win32service.CreateService(
                scm, name, display, win32service.SERVICE_ALL_ACCESS,
                win32service.SERVICE_WIN32_OWN_PROCESS, win32service.SERVICE_AUTO_START,
                win32service.SERVICE_ERROR_NORMAL, binpath, None, 0, None, None, None)
        win32service.ChangeServiceConfig2(h, win32service.SERVICE_CONFIG_DESCRIPTION, desc)
        win32service.CloseServiceHandle(h)
    finally:
        win32service.CloseServiceHandle(scm)
    _sc("failure", name, "reset=", "86400", "actions=", "restart/5000/restart/10000/restart/30000")
    _sc("sdset", name, SDDL)
    if which == "vcam":
        set_tray_autostart(True)
        # a logon autostart (AUTOSTART-ON.bat) would now only open a second panel window
        su = os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs\Startup",
                          "ONVIF-VCam.cmd")
        if os.path.exists(su):
            os.remove(su)
    start(which)
    print(f"{display}: installed and {status(which)}")


def _write_vcam_config(opts):
    """Setup program, first install: config.json with the chosen admin login and one camera per
    bundled video (what the service would build on its first start). An existing one is kept."""
    import json
    path = os.path.join(ROOT, "config.json")
    if os.path.exists(path) or not opts.get("user"):
        return
    env = dict(os.environ, PYTHONPATH=APP, PYTHONUTF8="1", PYTHONNOUSERSITE="1",
               PATH=os.pathsep.join([RUNTIME, os.path.join(RUNTIME, "Library", "bin"), os.environ.get("PATH", "")]))
    out = subprocess.run([python_exe(), "-m", "vcam", os.path.join(ROOT, "videos"), "--mjpeg", "-c", path,
                          "--user", opts["user"], "--password", opts.get("password") or "", "--print-config"],
                         capture_output=True, text=True, env=env, cwd=ROOT, timeout=120)
    if out.returncode:
        raise RuntimeError("cannot create config.json: " + (out.stderr or out.stdout).strip()[-300:])
    cfg = json.loads(out.stdout)
    cfg.pop("config_path", None)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2, ensure_ascii=False)


def _write_agent_config(opts):
    import json
    import uuid
    path = os.path.join(ROOT, "agent.json")
    cfg = {}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                cfg = json.load(fh)
        except ValueError:
            cfg = {}
    cfg.setdefault("id", str(uuid.uuid4()))
    for k in ("server", "key", "name"):
        if opts.get(k) is not None:
            cfg[k] = opts[k]
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2)


def remove(which):
    name = SERVICES[which][0]
    if status(which) == "not installed":
        print(f"{name}: not installed")
        return
    stop(which)
    win32serviceutil.RemoveService(name)
    if which == "vcam":
        set_tray_autostart(False)
    print(f"{name}: removed")


def start(which):
    if status(which) != "running":
        win32serviceutil.StartService(SERVICES[which][0])
        for _ in range(30):
            if status(which) == "running":
                break
            time.sleep(1)


def stop(which):
    if status(which) in ("running", "starting"):
        try:
            win32serviceutil.StopService(SERVICES[which][0])
        except Exception:  # noqa - already stopping
            pass
        for _ in range(30):
            if status(which) == "stopped":
                break
            time.sleep(1)


def set_tray_autostart(on):
    import winreg
    with winreg.CreateKey(winreg.HKEY_LOCAL_MACHINE, TRAY_RUN_KEY) as k:
        if on:
            winreg.SetValueEx(k, TRAY_VALUE, 0, winreg.REG_SZ,
                              f'"{python_exe(True)}" "{os.path.join(APP, "vcam", "tray.py")}"')
        else:
            try:
                winreg.DeleteValue(k, TRAY_VALUE)
            except OSError:
                pass


def cli(argv):
    if servicemanager is None:
        print("Windows services need pywin32 (included in the portable bundle).")
        return 1
    if len(argv) >= 2 and argv[0] == "config" and argv[1] == "vcam":  # setup: first config.json only
        opts = {k: argv[argv.index(f"--{k}") + 1] for k in ("user", "password") if f"--{k}" in argv}
        try:
            _write_vcam_config(opts)
        except Exception as exc:  # noqa
            print(f"error: {exc}")
            return 1
        return 0
    if len(argv) < 2 or argv[0] not in ("install", "remove", "start", "stop", "restart", "status") \
            or argv[1] not in SERVICES:
        print(__doc__)
        return 2
    action, which, rest = argv[0], argv[1], argv[2:]
    opts = {}
    for k in ("server", "key", "name", "user", "password"):
        if f"--{k}" in rest:
            opts[k] = rest[rest.index(f"--{k}") + 1]
    try:
        if action == "install":
            install(which, opts)
        elif action == "remove":
            remove(which)
        elif action == "start":
            start(which)
        elif action == "stop":
            stop(which)
        elif action == "restart":
            stop(which)
            start(which)
        print(f"{SERVICES[which][1]}: {status(which)}")
    except Exception as exc:  # noqa
        msg = str(exc)
        if "Access is denied" in msg or "(5," in msg:
            msg += "  -> run as administrator"
        print(f"error: {msg}")
        return 1
    return 0


def main():
    if len(sys.argv) >= 3 and sys.argv[1] == "--run":  # started by the service control manager
        try:
            cls = {"vcam": VcamService, "agent": AgentService}[sys.argv[2]]
            servicemanager.Initialize()
            servicemanager.PrepareToHostSingle(cls)
            servicemanager.StartServiceCtrlDispatcher()
        except BaseException:
            import traceback
            traceback.print_exc()
            raise
        print(time.strftime("%Y-%m-%d %H:%M:%S"), "service process exit")
        return 0
    return cli(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
