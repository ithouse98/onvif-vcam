"""Authentication & authorization.

ONVIF Core 5.9:
  * HTTP Digest (RFC 2617, optional SHA-256 per RFC 7616) and WS-UsernameToken.
  * No credentials on a protected request  -> HTTP 401 with a Digest challenge.
  * Bad WS-UsernameToken                   -> HTTP 400 + env:Sender/ter:NotAuthorized.
  * Both supplied                          -> validate HTTP first, then WS.
  * UsernameToken must use nonce + Created (5.9.5).
  * Access classes / default access policy (5.9.4.3, Table 7).
"""

import base64
import hashlib
import hmac
import os
import re
import threading
import time

from .util import child, localname, text, parse_datetime, utcnow

PRE_AUTH = "PRE_AUTH"
READ_SYSTEM = "READ_SYSTEM"
READ_SYSTEM_SENSITIVE = "READ_SYSTEM_SENSITIVE"
READ_SYSTEM_SECRET = "READ_SYSTEM_SECRET"
WRITE_SYSTEM = "WRITE_SYSTEM"
UNRECOVERABLE = "UNRECOVERABLE"
READ_MEDIA = "READ_MEDIA"
ACTUATE = "ACTUATE"

POLICY = {  # Table 7: Default Access Policy Definition
    PRE_AUTH: {"Administrator", "Operator", "User", "Anonymous"},
    READ_SYSTEM: {"Administrator", "Operator", "User"},
    READ_SYSTEM_SENSITIVE: {"Administrator", "Operator"},
    READ_SYSTEM_SECRET: {"Administrator"},
    WRITE_SYSTEM: {"Administrator"},
    UNRECOVERABLE: {"Administrator"},
    READ_MEDIA: {"Administrator", "Operator", "User"},
    ACTUATE: {"Administrator", "Operator"},
}

PRE_AUTH_OPS = {
    "GetSystemDateAndTime", "GetWsdlUrl", "GetServices", "GetServiceCapabilities",
    "GetCapabilities", "GetHostname", "GetEndpointReference",
}

SPECIAL = {
    "GetUsers": READ_SYSTEM_SECRET, "CreateUsers": WRITE_SYSTEM, "DeleteUsers": WRITE_SYSTEM,
    "SetUser": WRITE_SYSTEM, "SetSystemFactoryDefault": UNRECOVERABLE,
    "SystemReboot": UNRECOVERABLE, "GetSystemLog": READ_SYSTEM_SECRET,
    "GetSystemSupportInformation": READ_SYSTEM_SECRET, "GetAccessPolicy": READ_SYSTEM_SECRET,
    "GetStreamUri": READ_MEDIA, "GetSnapshotUri": READ_MEDIA,
    "StartMulticastStreaming": ACTUATE, "StopMulticastStreaming": ACTUATE,
    "SetSynchronizationPoint": ACTUATE, "GetNetworkInterfaces": READ_SYSTEM,
    "CreatePullPointSubscription": READ_MEDIA, "PullMessages": READ_MEDIA,
    "Subscribe": READ_MEDIA, "Renew": READ_MEDIA, "Unsubscribe": READ_MEDIA,
    "GetEventProperties": READ_MEDIA, "Seek": READ_MEDIA, "SetRelayOutputState": ACTUATE,
}


def access_class(service: str, op: str) -> str:
    if op in PRE_AUTH_OPS:
        return PRE_AUTH
    if op in SPECIAL:
        return SPECIAL[op]
    if op.startswith("Get"):
        return READ_MEDIA if service in ("media", "media2") else READ_SYSTEM
    return ACTUATE if service in ("media", "media2", "events") and op.startswith(
        ("Start", "Stop")) else WRITE_SYSTEM


class AuthError(Exception):
    def __init__(self, kind, msg=""):
        super().__init__(msg)
        self.kind = kind  # "challenge" | "not_authorized" | "forbidden"


class Authenticator:
    def __init__(self, camera, cfg):
        self.camera = camera
        self.enabled = bool(cfg.get("auth", True))
        self.realm = cfg.get("realm", "ONVIF-VCam")
        self.algorithms = [a.upper() for a in cfg.get("digest_algorithms", ["MD5"])]
        self.allow_basic = bool(cfg.get("allow_basic", True))
        self.clock_skew = float(cfg.get("wsse_clock_tolerance", 0))  # 0 = don't check Created
        self.strict_wsse = bool(cfg.get("wsse_strict", True))
        self.allow_text = bool(cfg.get("wsse_allow_password_text", True))
        self.secret = os.urandom(16)
        self._seen = {}
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- users
    def password(self, username):
        with self.camera.lock:
            u = self.camera.users.get(username)
            return (u.password, u.level) if u else (None, None)

    # ---------------------------------------------------------------- WS-Security
    def check_wsse(self, header):
        """Returns (username, level) or None when no token present; raises AuthError on bad token."""
        if header is None:
            return None
        sec = child(header, "Security")
        tok = child(sec, "UsernameToken") if sec is not None else None
        if tok is None:
            return None
        username = text(tok, "Username", default="")
        pwd_el = child(tok, "Password")
        supplied = (pwd_el.text or "").strip() if pwd_el is not None else ""
        ptype = (pwd_el.get("Type") or "") if pwd_el is not None else ""
        nonce_el = child(tok, "Nonce")
        nonce_txt = (nonce_el.text or "").strip() if nonce_el is not None else ""
        created = text(tok, "Created", default="")
        password, level = self.password(username)
        if password is None:
            raise AuthError("not_authorized", "unknown user")
        if ptype.endswith("#PasswordText") or (ptype == "" and not nonce_txt):
            if not self.allow_text:
                raise AuthError("not_authorized", "PasswordText disabled")
            ok = hmac.compare_digest(supplied, password)
        else:
            if self.strict_wsse and (not nonce_txt or not created):
                # Core 5.9.5: reject any UsernameToken not using both nonce and Created
                raise AuthError("not_authorized", "UsernameToken without Nonce/Created")
            enc = (nonce_el.get("EncodingType") or "") if nonce_el is not None else ""
            try:
                nonce = base64.b64decode(nonce_txt) if "Base64" in enc or not enc else \
                    nonce_txt.encode()
            except Exception:
                nonce = nonce_txt.encode()
            expect = base64.b64encode(hashlib.sha1(nonce + created.encode() + password.encode())
                                      .digest()).decode()
            ok = hmac.compare_digest(supplied, expect)
            if ok and self.clock_skew > 0:
                ts = parse_datetime(created)
                device_now = utcnow().timestamp() + self.camera.time_offset
                if ts is None or abs(ts.timestamp() - device_now) > self.clock_skew:
                    raise AuthError("not_authorized", "Created outside tolerance")
            if ok:
                key = (nonce_txt, created)
                now = time.time()
                with self._lock:
                    if key in self._seen and self.clock_skew > 0:
                        raise AuthError("not_authorized", "replayed nonce")
                    self._seen[key] = now
                    if len(self._seen) > 5000:
                        cutoff = now - max(self.clock_skew, 300)
                        self._seen = {k: v for k, v in self._seen.items() if v > cutoff}
        if not ok:
            raise AuthError("not_authorized", "bad password")
        return username, level

    # ---------------------------------------------------------------- HTTP / RTSP digest
    def new_nonce(self):
        ts = f"{time.time():.0f}"
        mac = hmac.new(self.secret, ts.encode(), hashlib.sha256).hexdigest()[:24]
        return base64.b64encode(f"{ts}:{mac}".encode()).decode()

    def nonce_valid(self, nonce, max_age=300):
        try:
            ts, mac = base64.b64decode(nonce).decode().split(":")
            good = hmac.new(self.secret, ts.encode(), hashlib.sha256).hexdigest()[:24]
            return hmac.compare_digest(mac, good) and time.time() - float(ts) < max_age
        except Exception:
            return False

    def challenges(self, stale=False):
        nonce = self.new_nonce()
        out = []
        for alg in self.algorithms:
            s = (f'Digest realm="{self.realm}", qop="auth", algorithm={alg}, nonce="{nonce}", '
                 f'opaque="{hashlib.md5(self.realm.encode()).hexdigest()}"')
            if stale:
                s += ", stale=true"
            out.append(s)
        return out

    def rtsp_challenges(self, stale=False):
        """RTSP clients are often RFC 2069 style; offer digest without qop as well."""
        nonce = self.new_nonce()
        out = [f'Digest realm="{self.realm}", nonce="{nonce}"' + (", stale=TRUE" if stale else "")]
        if self.allow_basic:
            out.append(f'Basic realm="{self.realm}"')
        return out

    _kv = re.compile(r'(\w+)=("([^"]*)"|[^,\s]+)')

    def check_authorization(self, header, method):
        """Validates an HTTP/RTSP Authorization header. Returns (user, level) or raises."""
        if not header:
            return None
        scheme, _, rest = header.strip().partition(" ")
        if scheme.lower() == "basic":
            if not self.allow_basic:
                raise AuthError("challenge", "basic disabled")
            try:
                user, _, pwd = base64.b64decode(rest.strip()).decode().partition(":")
            except Exception:
                raise AuthError("challenge", "malformed basic")
            password, level = self.password(user)
            if password is None or not hmac.compare_digest(pwd, password):
                raise AuthError("challenge", "bad basic credentials")
            return user, level
        if scheme.lower() != "digest":
            raise AuthError("challenge", "unsupported scheme")
        p = {m.group(1).lower(): (m.group(3) if m.group(3) is not None else m.group(2))
             for m in self._kv.finditer(rest)}
        user = p.get("username", "")
        password, level = self.password(user)
        if password is None:
            raise AuthError("challenge", "unknown user")
        alg = (p.get("algorithm") or "MD5").upper()
        hfn = {"MD5": hashlib.md5, "MD5-SESS": hashlib.md5, "SHA-256": hashlib.sha256,
               "SHA-256-SESS": hashlib.sha256}.get(alg)
        if hfn is None:
            raise AuthError("challenge", "unsupported algorithm")

        def H(s):
            return hfn(s.encode()).hexdigest()

        nonce = p.get("nonce", "")
        realm = p.get("realm", self.realm)
        ha1 = H(f"{user}:{realm}:{password}")
        if alg.endswith("-SESS"):
            ha1 = H(f"{ha1}:{nonce}:{p.get('cnonce', '')}")
        ha2 = H(f"{method}:{p.get('uri', '')}")
        qop = p.get("qop")
        if qop:
            expect = H(f"{ha1}:{nonce}:{p.get('nc', '')}:{p.get('cnonce', '')}:{qop}:{ha2}")
        else:
            expect = H(f"{ha1}:{nonce}:{ha2}")
        if not hmac.compare_digest(expect, p.get("response", "")):
            raise AuthError("challenge", "bad digest response")
        if not self.nonce_valid(nonce):
            raise AuthError("stale", "stale nonce")
        return user, level

    # ---------------------------------------------------------------- policy
    @staticmethod
    def allowed(level, klass):
        return (level or "Anonymous") in POLICY.get(klass, set()) or level == "Extended"


def strip_element(elem):
    return localname(elem.tag) if elem is not None else ""
