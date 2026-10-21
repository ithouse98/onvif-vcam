"""Protocol-level self test for a running vcam (stdlib only + ffprobe).

    python tests/test_raw_soap.py [host] [http_port] [rtsp_port] [user] [password]

Covers what a high-level client hides: authentication semantics (Core 5.9.1), fault codes
(Core 5.8.2), Media2 / H.265, WS-Discovery probe matching, push notifications, multicast,
RTSP Require / keep-alive / bad credentials.
"""

import base64
import datetime as dt
import hashlib
import http.server
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid

HOST = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 8080
RTSP = int(sys.argv[3]) if len(sys.argv) > 3 else 8554
USER = sys.argv[4] if len(sys.argv) > 4 else "admin"
PWD = sys.argv[5] if len(sys.argv) > 5 else "admin"
BASE = f"http://{HOST}:{PORT}"
RESULTS = []

NS = ('xmlns:s="http://www.w3.org/2003/05/soap-envelope" '
      'xmlns:tds="http://www.onvif.org/ver10/device/wsdl" '
      'xmlns:trt="http://www.onvif.org/ver10/media/wsdl" '
      'xmlns:tr2="http://www.onvif.org/ver20/media/wsdl" '
      'xmlns:tev="http://www.onvif.org/ver10/events/wsdl" '
      'xmlns:wsnt="http://docs.oasis-open.org/wsn/b-2" '
      'xmlns:wsa="http://www.w3.org/2005/08/addressing" '
      'xmlns:tt="http://www.onvif.org/ver10/schema"')


def wsse(user=USER, pwd=PWD, mangle=False):
    nonce = os.urandom(16)
    created = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    digest = base64.b64encode(hashlib.sha1(nonce + created.encode() + pwd.encode()).digest()).decode()
    if mangle:
        digest = digest[::-1]
    return ('<wsse:Security s:mustUnderstand="1" xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/'
            'oasis-200401-wss-wssecurity-secext-1.0.xsd" xmlns:wsu="http://docs.oasis-open.org/wss/2004/'
            '01/oasis-200401-wss-wssecurity-utility-1.0.xsd"><wsse:UsernameToken>'
            f"<wsse:Username>{user}</wsse:Username><wsse:Password Type=\"http://docs.oasis-open.org/"
            f"wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest\">{digest}"
            "</wsse:Password><wsse:Nonce EncodingType=\"http://docs.oasis-open.org/wss/2004/01/"
            f"oasis-200401-soap-message-security-1.0#Base64Binary\">{base64.b64encode(nonce).decode()}"
            f"</wsse:Nonce><wsu:Created>{created}</wsu:Created></wsse:UsernameToken></wsse:Security>")


def soap(body, path="/onvif/device_service", auth=True, mangle=False, extra_header="", headers=None):
    hdr = (wsse(mangle=mangle) if auth else "") + extra_header
    env = f'<?xml version="1.0"?><s:Envelope {NS}><s:Header>{hdr}</s:Header><s:Body>{body}</s:Body></s:Envelope>'
    req = urllib.request.Request(BASE + path, data=env.encode(), method="POST", headers=dict(
        {"Content-Type": "application/soap+xml; charset=utf-8"}, **(headers or {})))
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read().decode(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(), dict(e.headers)


def ok(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  [{detail}]" if detail and not cond else ""))


def has(xml, tag):
    return re.search(r"<[\w]+:" + tag + r"[\s>/]", xml) is not None


def ffprobe(url, transport="tcp", extra=()):
    cmd = ["ffprobe", "-v", "error", *extra, "-show_entries", "stream=codec_name,width,height",
           "-of", "csv=p=0", url]
    if transport:
        cmd[1:1] = ["-rtsp_transport", transport]
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=40).stdout.strip()
    except subprocess.TimeoutExpired:
        return ""


def rtsp_raw(lines):
    s = socket.create_connection((HOST, RTSP), timeout=10)
    s.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
    data = s.recv(8192).decode("latin-1")
    s.close()
    return data


def main():
    print("[Authentication - Core 5.9]")
    st, x, _ = soap("<tds:GetSystemDateAndTime/>", auth=False)
    ok("PRE_AUTH GetSystemDateAndTime without credentials -> 200", st == 200)
    st, x, _ = soap("<tds:GetCapabilities><tds:Category>All</tds:Category></tds:GetCapabilities>", auth=False)
    ok("PRE_AUTH GetCapabilities without credentials -> 200", st == 200)
    st, x, h = soap("<tds:GetDeviceInformation/>", auth=False)
    ok("protected op without credentials -> 401 + Digest challenge",
       st == 401 and "Digest" in h.get("WWW-Authenticate", ""), f"{st} {h.get('WWW-Authenticate')}")
    st, x, _ = soap("<tds:GetDeviceInformation/>", mangle=True)
    ok("bad WS-UsernameToken -> 400 env:Sender ter:NotAuthorized",
       st == 400 and "ter:NotAuthorized" in x, f"{st}")
    st, x, _ = soap("<tds:GetDeviceInformation/>")
    ok("valid WS-UsernameToken digest -> 200", st == 200 and has(x, "Manufacturer"))
    # HTTP digest
    pm = urllib.request.HTTPPasswordMgrWithDefaultRealm()
    pm.add_password(None, BASE, USER, PWD)
    opener = urllib.request.build_opener(urllib.request.HTTPDigestAuthHandler(pm))
    env = f'<?xml version="1.0"?><s:Envelope {NS}><s:Body><tds:GetUsers/></s:Body></s:Envelope>'
    try:
        r = opener.open(urllib.request.Request(BASE + "/onvif/device_service", data=env.encode(),
                                               headers={"Content-Type": "application/soap+xml"}), timeout=10)
        ok("HTTP Digest (RFC 2617) -> 200 GetUsers", r.status == 200 and "Username" in r.read().decode())
    except Exception as exc:
        ok("HTTP Digest (RFC 2617) -> 200 GetUsers", False, str(exc))
    # user level policy: a 'User' cannot create users
    soap("<tds:CreateUsers><tds:User><tt:Username>lowuser</tt:Username><tt:Password>low12345</tt:Password>"
         "<tt:UserLevel>User</tt:UserLevel></tds:User></tds:CreateUsers>")
    env = (f'<?xml version="1.0"?><s:Envelope {NS}><s:Header>{wsse("lowuser", "low12345")}</s:Header>'
           "<s:Body><tds:CreateUsers><tds:User><tt:Username>x</tt:Username><tt:Password>y</tt:Password>"
           "<tt:UserLevel>User</tt:UserLevel></tds:User></tds:CreateUsers></s:Body></s:Envelope>")
    try:
        urllib.request.urlopen(urllib.request.Request(BASE + "/onvif/device_service", data=env.encode(),
                                                      headers={"Content-Type": "application/soap+xml"}))
        ok("access policy: User level cannot WRITE_SYSTEM", False)
    except urllib.error.HTTPError as e:
        ok("access policy: User level cannot WRITE_SYSTEM", e.code == 400 and "NotAuthorized" in e.read().decode())
    soap("<tds:DeleteUsers><tds:Username>lowuser</tds:Username></tds:DeleteUsers>")

    print("\n[Faults - Core 5.8.2]")
    st, x, _ = soap("<tds:GetSystemLog><tds:LogType>System</tds:LogType></tds:GetSystemLog>")
    ok("unsupported op -> env:Receiver ter:ActionNotSupported", st == 500 and "ActionNotSupported" in x)
    st, x, _ = soap("<trt:GetProfile><trt:ProfileToken>nope</trt:ProfileToken></trt:GetProfile>",
                    path="/onvif/media_service")
    ok("unknown profile -> ter:InvalidArgVal/ter:NoProfile", st == 400 and "ter:NoProfile" in x)
    st, x, _ = soap("<tds:RemoveScopes><tds:ScopeItem>onvif://www.onvif.org/Profile/Streaming</tds:ScopeItem>"
                    "</tds:RemoveScopes>")
    ok("remove fixed scope -> ter:OperationProhibited/ter:FixedScope", st == 400 and "FixedScope" in x)
    st, x, _ = soap("<trt:DeleteProfile><trt:ProfileToken>Profile_1</trt:ProfileToken></trt:DeleteProfile>",
                    path="/onvif/media_service")
    ok("delete fixed profile -> ter:DeletionOfFixedProfile", "DeletionOfFixedProfile" in x)
    st, x, _ = soap('<trt:SetVideoEncoderConfiguration><trt:Configuration token="VEC_1"><tt:Name>x</tt:Name>'
                    "<tt:UseCount>1</tt:UseCount><tt:Encoding>H264</tt:Encoding><tt:Resolution><tt:Width>1234"
                    "</tt:Width><tt:Height>777</tt:Height></tt:Resolution><tt:Quality>5</tt:Quality>"
                    "</trt:Configuration><trt:ForcePersistence>true</trt:ForcePersistence>"
                    "</trt:SetVideoEncoderConfiguration>", path="/onvif/media_service")
    ok("invalid resolution -> ter:ConfigModify", st == 400 and "ConfigModify" in x)
    req = urllib.request.Request(BASE + "/onvif/device_service", data=b"<not-xml", method="POST",
                                 headers={"Content-Type": "application/soap+xml"})
    try:
        urllib.request.urlopen(req)
        ok("malformed XML -> 400 ter:WellFormed", False)
    except urllib.error.HTTPError as e:
        ok("malformed XML -> 400 ter:WellFormed", e.code == 400 and "WellFormed" in e.read().decode())

    print("\n[Media2 - ver20 / H.265]")
    st, x, _ = soap("<tr2:GetServiceCapabilities/>", path="/onvif/media2_service")
    ok("Media2 GetServiceCapabilities", st == 200 and "RTSPStreaming" in x)
    st, x, _ = soap("<tr2:GetProfiles><tr2:Type>All</tr2:Type></tr2:GetProfiles>", path="/onvif/media2_service")
    ok("Media2 GetProfiles Type=All has configurations", st == 200 and has(x, "VideoEncoder"))
    st, x2, _ = soap("<tr2:GetProfiles/>", path="/onvif/media2_service")
    ok("Media2 GetProfiles without Type omits configurations", st == 200 and not has(x2, "VideoEncoder"))
    st, x, _ = soap("<tr2:GetVideoEncoderConfigurationOptions/>", path="/onvif/media2_service")
    ok("Media2 encoder options list H264/H265/JPEG", all(f">{e}<" in x for e in ("H264", "H265", "JPEG")))
    st, x, _ = soap("<tr2:GetStreamUri><tr2:Protocol>RtspUnicast</tr2:Protocol><tr2:ProfileToken>Profile_1"
                    "</tr2:ProfileToken></tr2:GetStreamUri>", path="/onvif/media2_service")
    ok("Media2 GetStreamUri RtspUnicast", st == 200 and "rtsp://" in x)
    st, x, _ = soap("<tr2:GetVideoEncoderInstances><tr2:ConfigurationToken>VSC_1</tr2:ConfigurationToken>"
                    "</tr2:GetVideoEncoderInstances>", path="/onvif/media2_service")
    ok("Media2 GetVideoEncoderInstances", st == 200 and has(x, "Total"))
    # switch the MJPEG profile's encoder to H.265 via Media2, then stream it
    st, x, _ = soap('<tr2:SetVideoEncoderConfiguration><tr2:Configuration token="VEC_3" GovLength="20" '
                    'Profile="Main"><tt:Name>enc3</tt:Name><tt:UseCount>1</tt:UseCount><tt:Encoding>H265'
                    "</tt:Encoding><tt:Resolution><tt:Width>1280</tt:Width><tt:Height>720</tt:Height>"
                    '</tt:Resolution><tt:RateControl ConstantBitRate="false"><tt:FrameRateLimit>20'
                    "</tt:FrameRateLimit><tt:BitrateLimit>1500</tt:BitrateLimit></tt:RateControl>"
                    "<tt:Quality>6</tt:Quality></tr2:Configuration></tr2:SetVideoEncoderConfiguration>",
                    path="/onvif/media2_service")
    ok("Media2 SetVideoEncoderConfiguration -> H265 1280x720", st == 200, x[:300])
    out = ffprobe(f"rtsp://{USER}:{PWD}@{HOST}:{RTSP}/cam1/Profile_3")
    ok("RTSP H.265 stream decodes (RFC 7798)", out.startswith("hevc,1280,720"), out)
    soap('<tr2:SetVideoEncoderConfiguration><tr2:Configuration token="VEC_3"><tt:Name>enc3</tt:Name>'
         "<tt:UseCount>1</tt:UseCount><tt:Encoding>JPEG</tt:Encoding><tt:Resolution><tt:Width>640"
         "</tt:Width><tt:Height>352</tt:Height></tt:Resolution><tt:RateControl><tt:FrameRateLimit>10"
         "</tt:FrameRateLimit><tt:BitrateLimit>768</tt:BitrateLimit></tt:RateControl><tt:Quality>5"
         "</tt:Quality></tr2:Configuration></tr2:SetVideoEncoderConfiguration>", path="/onvif/media2_service")

    print("\n[WS-Discovery - Core 7]")
    def probe(types="dn:NetworkVideoTransmitter", scopes=None):
        mid = f"urn:uuid:{uuid.uuid4()}"
        sc = f"<d:Scopes>{scopes}</d:Scopes>" if scopes else ""
        msg = ('<?xml version="1.0" encoding="UTF-8"?><e:Envelope xmlns:e="http://www.w3.org/2003/05/soap-envelope" '
               'xmlns:w="http://schemas.xmlsoap.org/ws/2004/08/addressing" xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery" '
               'xmlns:dn="http://www.onvif.org/ver10/network/wsdl"><e:Header>'
               f"<w:MessageID>{mid}</w:MessageID><w:To>urn:schemas-xmlsoap-org:ws:2005:04:discovery</w:To>"
               "<w:Action>http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</w:Action></e:Header><e:Body>"
               f"<d:Probe><d:Types>{types}</d:Types>{sc}</d:Probe></e:Body></e:Envelope>")
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(1.5)
        s.sendto(msg.encode(), ("239.255.255.250", 3702))
        found = []
        end = time.time() + 2
        while time.time() < end:
            try:
                d, _ = s.recvfrom(65535)
            except socket.timeout:
                break
            t = d.decode()
            if mid in t and "ProbeMatches" in t:
                found.append(t)
        s.close()
        return found
    m = probe()
    ok("Probe(dn:NetworkVideoTransmitter) -> ProbeMatch", len(m) >= 1)
    if m:
        ok("ProbeMatch has XAddrs, urn:uuid, Profile/Streaming scope",
           "/onvif/device_service" in m[0] and "urn:uuid:" in m[0] and "Profile/Streaming" in m[0])
    ok("Probe(tds:Device) -> ProbeMatch", len(probe("tds:Device")) >= 1)
    ok("Probe scope onvif://www.onvif.org/Profile matches (rfc3986 prefix)",
       len(probe("", "onvif://www.onvif.org/Profile")) >= 1)
    ok("Probe scope onvif://www.onvif.org/hardware/Virt does NOT match", len(probe("", "onvif://www.onvif.org/hardware/Virt")) == 0)
    ok("Probe for NetworkVideoDisplay does NOT match", len(probe("dn:NetworkVideoDisplay")) == 0)

    print("\n[Events - push (WS-BaseNotification)]")
    got = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            got.append(self.rfile.read(int(self.headers["Content-Length"])).decode())
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *a):
            pass
    srv = http.server.HTTPServer(("0.0.0.0", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    me = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    me.connect((HOST, 9))
    myip = me.getsockname()[0] if HOST not in ("127.0.0.1", "localhost") else "127.0.0.1"
    st, x, _ = soap("<wsnt:Subscribe><wsnt:ConsumerReference><wsa:Address>"
                    f"http://{myip}:{srv.server_address[1]}/notify</wsa:Address></wsnt:ConsumerReference>"
                    "<wsnt:InitialTerminationTime>PT60S</wsnt:InitialTerminationTime></wsnt:Subscribe>",
                    path="/onvif/events_service")
    ok("Subscribe -> SubscriptionReference", st == 200 and "SubscriptionReference" in x)
    sub_addr = re.search(r"<wsa5:Address>([^<]+)</wsa5:Address>", x)
    time.sleep(3)
    ok("Notify received (Initialized property events)", any("Initialized" in g for g in got))
    # fire a manual motion event through the test API
    pm = urllib.request.HTTPPasswordMgrWithDefaultRealm()
    pm.add_password(None, BASE, USER, PWD)
    op = urllib.request.build_opener(urllib.request.HTTPDigestAuthHandler(pm))
    # property events only fire on change: force 'false' first (the motion simulator may be on)
    op.open(urllib.request.Request(BASE + "/api/motion?state=0", data=b"", method="POST"), timeout=5)
    time.sleep(2.5)
    got.clear()
    op.open(urllib.request.Request(BASE + "/api/motion?state=1", data=b"", method="POST"), timeout=5)
    time.sleep(3)
    ok("motion trigger -> Notify with State=true Changed",
       any('Value="true"' in g and "Changed" in g for g in got))
    op.open(urllib.request.Request(BASE + "/api/motion?state=0", data=b"", method="POST"), timeout=5)
    if sub_addr:
        path = "/" + sub_addr.group(1).split("/", 3)[3]
        st, x, _ = soap("<wsnt:Unsubscribe/>", path=path)
        ok("Unsubscribe push subscription", st == 200)
    srv.shutdown()

    print("\n[RTSP - Streaming spec 5.2]")
    r = rtsp_raw([f"OPTIONS rtsp://{HOST}:{RTSP}/cam1/Profile_1 RTSP/1.0", "CSeq: 1"])
    ok("OPTIONS lists required methods", all(mth in r for mth in ("DESCRIBE", "SETUP", "PLAY", "TEARDOWN")))
    r = rtsp_raw([f"DESCRIBE rtsp://{HOST}:{RTSP}/cam1/Profile_1 RTSP/1.0", "CSeq: 2"])
    ok("DESCRIBE without credentials -> 401 Digest", r.startswith("RTSP/1.0 401") and "Digest" in r)
    r = rtsp_raw([f"DESCRIBE rtsp://{HOST}:{RTSP}/cam1/Profile_1 RTSP/1.0", "CSeq: 3",
                  "Require: www.onvif.org/ver20/backchannel",
                  "Authorization: Basic " + base64.b64encode(f"{USER}:{PWD}".encode()).decode()])
    ok("Require backchannel -> 551 Option not supported", r.startswith("RTSP/1.0 551") and "Unsupported" in r)
    out = ffprobe(f"rtsp://{USER}:wrong@{HOST}:{RTSP}/cam1/Profile_1")
    ok("wrong RTSP password is rejected", out == "")
    r = rtsp_raw([f"DESCRIBE rtsp://{HOST}:{RTSP}/cam1/Profile_1 RTSP/1.0", "CSeq: 4",
                  "Authorization: Basic " + base64.b64encode(f"{USER}:{PWD}".encode()).decode()])
    ok("SDP: H264 packetization-mode=1 + sprop-parameter-sets",
       "packetization-mode=1" in r and "sprop-parameter-sets=" in r)
    out = ffprobe(f"rtsp://{USER}:{PWD}@{HOST}:{RTSP}/cam1/Profile_2/multicast", transport="udp_multicast")
    ok("RTP multicast stream decodes", out.startswith("h264,640,360"), out)
    out = ffprobe(f"rtsp://{USER}:{PWD}@{HOST}:{RTSP}/cam1/Profile_2", transport="http")
    ok("RTSP over HTTP tunnel on RTSP port", out.startswith("h264"), out)
    out = ffprobe(f"rtsp://{USER}:{PWD}@{HOST}:{PORT}/cam1/Profile_2", transport="http")
    ok("RTSP over HTTP tunnel on ONVIF HTTP port", out.startswith("h264"), out)

    print("\n[Snapshot]")
    try:
        with urllib.request.urlopen(BASE + "/onvif/snapshot/Profile_2.jpg", timeout=20) as r:
            data = r.read()
        ok("snapshot is a JPEG", data[:2] == b"\xff\xd8" and r.headers["Content-Type"] == "image/jpeg")
    except Exception as exc:
        ok("snapshot is a JPEG", False, str(exc))

    n_ok = sum(1 for _, v in RESULTS if v)
    print(f"\n{n_ok}/{len(RESULTS)} checks passed")
    return 0 if n_ok == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
