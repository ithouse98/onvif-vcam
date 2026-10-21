"""ONVIF Virtual Camera - turns video files into ONVIF Profile S/T compliant network cameras.

Implements (per ONVIF Core 26.06, Streaming 26.06, Profile S 1.2):
  * WS-Discovery target service (Hello / Bye / Probe / Resolve)
  * Device management service  (tds, ver10)
  * Media service              (trt, ver10)
  * Media2 service             (tr2, ver20)  - H.265 / Profile T style streaming
  * Event service              (tev, pull-point + WS-BaseNotification push)
  * WS-UsernameToken (PasswordDigest) and HTTP Digest (MD5 / SHA-256) authentication
  * RTSP/RTP server: RTP/UDP unicast, RTP/RTSP/TCP interleaved, RTP/RTSP/HTTP/TCP tunnel,
    RTP/UDP multicast, RTCP sender reports, H.264 (RFC 6184), H.265 (RFC 7798), JPEG (RFC 2435)
"""

__version__ = "1.1.0"
__author__ = "Behrouz Kazemi (IPS-Co.)"
