"""Windows: run processes on the logged-in user's desktop from a service.

A Windows service lives in Session 0, which has no access to the user's screen: desktop
duplication / GDI capture there sees nothing. The service (LocalSystem) therefore starts screen
helpers and the screen agent inside the active user session with that user's token, the way
remote-support tools do. Everything here is a no-op answer on other systems.
"""

import os
import subprocess

WIN = os.name == "nt"
CREATE_NO_WINDOW = 0x08000000
CREATE_UNICODE_ENVIRONMENT = 0x00000400
STILL_ACTIVE = 259


def in_service_session():
    """True when this process runs in Session 0 (a Windows service)."""
    if not WIN:
        return False
    try:
        import win32ts
        return win32ts.ProcessIdToSessionId(os.getpid()) == 0
    except Exception:  # noqa - pywin32 missing: behave like a normal desktop process
        return False


def user_session():
    """Id of the session with a logged-on user at the console (or an active RDP one), else None."""
    import win32ts
    cands = []
    console = win32ts.WTSGetActiveConsoleSessionId()
    if console not in (0, 0xFFFFFFFF):
        cands.append(console)
    for s in win32ts.WTSEnumerateSessions(win32ts.WTS_CURRENT_SERVER_HANDLE):
        sid = s["SessionId"]
        if s["State"] == win32ts.WTSActive and sid not in cands and sid != 0:
            cands.append(sid)
    for sid in cands:
        try:
            user = win32ts.WTSQuerySessionInformation(win32ts.WTS_CURRENT_SERVER_HANDLE, sid,
                                                      win32ts.WTSUserName)
        except Exception:  # noqa
            continue
        if user:
            return sid
    return None


class SessionProcess:
    """Popen-like handle of a process started with launch()."""

    def __init__(self, handle, pid):
        self.handle = handle
        self.pid = pid
        self.returncode = None

    def poll(self):
        if self.returncode is None:
            import win32process
            rc = win32process.GetExitCodeProcess(self.handle)
            if rc != STILL_ACTIVE:
                self.returncode = rc
        return self.returncode

    def wait(self, timeout=None):
        import win32event
        win32event.WaitForSingleObject(self.handle, win32event.INFINITE if timeout is None
                                       else int(timeout * 1000))
        return self.poll()

    def kill(self):
        if self.poll() is None:
            import win32process
            try:
                win32process.TerminateProcess(self.handle, 1)
            except Exception:  # noqa - already gone
                pass

    terminate = kill


def launch(cmd, session, env=None, cwd=None):
    """Start cmd (argv list) hidden on the desktop of `session` as its logged-on user.
    env: variables to set on top of the user's own environment (PATH entries are prepended)."""
    import win32con
    import win32process
    import win32profile
    import win32security
    import win32ts
    token = win32ts.WTSQueryUserToken(session)
    primary = win32security.DuplicateTokenEx(token, win32security.SecurityImpersonation,
                                             win32con.MAXIMUM_ALLOWED, win32security.TokenPrimary)
    block = win32profile.CreateEnvironmentBlock(primary, False)
    for k, v in (env or {}).items():
        if k.upper() == "PATH":
            cur = next((block[x] for x in list(block) if x.upper() == "PATH"), "")
            for x in [x for x in list(block) if x.upper() == "PATH"]:
                del block[x]
            block["Path"] = v + (os.pathsep + cur if cur else "")
        else:
            block[k] = v
    si = win32process.STARTUPINFO()
    si.lpDesktop = "winsta0\\default"
    hproc, hthread, pid, _ = win32process.CreateProcessAsUser(
        primary, None, subprocess.list2cmdline(cmd), None, None, False,
        CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT, block, cwd, si)
    hthread.Close()
    return SessionProcess(hproc, pid)
