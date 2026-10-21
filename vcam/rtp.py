"""RTP payload formats and RTCP.

ONVIF Streaming Spec 5.1.2: media shall conform to RFC 3550/3551, RFC 6184 (H.264, formerly 3984),
RFC 7798 (H.265) and RFC 2435 (JPEG). Devices shall support RTCP Sender Reports (5.1.2.2.1).
"""

import struct
import time

NTP_EPOCH_OFFSET = 2208988800  # 1900-01-01 -> 1970-01-01

PT_JPEG = 26
PT_DYNAMIC = 96


# --------------------------------------------------------------------------- H.264 (RFC 6184)
def h264_type(nal: bytes) -> int:
    return nal[0] & 0x1F


def packetize_h264(nals, mtu):
    """Single NAL unit packets + FU-A fragmentation (packetization-mode=1)."""
    out = []
    for nal in nals:
        if not nal:
            continue
        t = nal[0] & 0x1F
        if t == 9:  # access unit delimiter - not needed on the wire
            continue
        if len(nal) <= mtu:
            out.append(nal)
            continue
        indicator = (nal[0] & 0xE0) | 28
        hdr = nal[0] & 0x1F
        data = memoryview(nal)[1:]
        step = mtu - 2
        n = len(data)
        for off in range(0, n, step):
            fu = hdr
            if off == 0:
                fu |= 0x80
            if off + step >= n:
                fu |= 0x40
            out.append(bytes((indicator, fu)) + bytes(data[off:off + step]))
    return out


# --------------------------------------------------------------------------- H.265 (RFC 7798)
def h265_type(nal: bytes) -> int:
    return (nal[0] >> 1) & 0x3F


def packetize_h265(nals, mtu):
    out = []
    for nal in nals:
        if len(nal) < 2:
            continue
        t = (nal[0] >> 1) & 0x3F
        if t == 35:  # AUD
            continue
        if len(nal) <= mtu:
            out.append(nal)
            continue
        ph0 = (nal[0] & 0x81) | (49 << 1)
        ph1 = nal[1]
        data = memoryview(nal)[2:]
        step = mtu - 3
        n = len(data)
        for off in range(0, n, step):
            fu = t
            if off == 0:
                fu |= 0x80
            if off + step >= n:
                fu |= 0x40
            out.append(bytes((ph0, ph1, fu)) + bytes(data[off:off + step]))
    return out


# --------------------------------------------------------------------------- JPEG (RFC 2435)
class JpegInfo:
    __slots__ = ("width", "height", "type", "qtables", "precision", "dri", "scan")


def parse_jpeg(buf: bytes) -> JpegInfo:
    """Extract what RFC 2435 needs from a baseline JFIF image."""
    if buf[:2] != b"\xff\xd8":
        raise ValueError("not a JPEG")
    info = JpegInfo()
    info.qtables = {}
    info.precision = 0
    info.dri = 0
    info.type = 1
    info.width = info.height = 0
    pos = 2
    n = len(buf)
    while pos + 4 <= n:
        if buf[pos] != 0xFF:
            pos += 1
            continue
        marker = buf[pos + 1]
        if marker == 0xFF:
            pos += 1
            continue
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            pos += 2
            continue
        seglen = struct.unpack_from(">H", buf, pos + 2)[0]
        seg = buf[pos + 4:pos + 2 + seglen]
        if marker == 0xDB:  # DQT
            i = 0
            while i < len(seg):
                pq, tq = seg[i] >> 4, seg[i] & 0x0F
                size = 128 if pq else 64
                info.qtables[tq] = seg[i + 1:i + 1 + size]
                if pq:
                    info.precision |= 1 << tq
                i += 1 + size
        elif marker in (0xC0, 0xC1):  # SOF0 / SOF1 baseline
            info.height, info.width = struct.unpack_from(">HH", seg, 1)
            ncomp = seg[5]
            if ncomp >= 1:
                samp = seg[7]
                info.type = 1 if samp == 0x22 else 0  # 4:2:0 -> 1, 4:2:2 -> 0
        elif marker in (0xC2, 0xC3):
            raise ValueError("progressive/lossless JPEG not supported by RFC 2435")
        elif marker == 0xDD:  # DRI
            info.dri = struct.unpack_from(">H", seg, 0)[0]
        elif marker == 0xDA:  # SOS -> entropy coded data until EOI
            start = pos + 2 + seglen
            end = buf.rfind(b"\xff\xd9")
            if end < start:
                end = n
            info.scan = buf[start:end]
            return info
        pos += 2 + seglen
    raise ValueError("JPEG without SOS")


def packetize_jpeg(info: JpegInfo, mtu):
    out = []
    jtype = info.type + (64 if info.dri else 0)
    w8 = min(255, (info.width + 7) // 8)
    h8 = min(255, (info.height + 7) // 8)
    restart_hdr = struct.pack(">HH", info.dri, 0xFFFF) if info.dri else b""  # F=1 L=1 count=0x3FFF
    tables = b"".join(info.qtables[k] for k in sorted(info.qtables))[:256]
    q_hdr = struct.pack(">BBH", 0, info.precision, len(tables)) + tables
    scan = info.scan
    off = 0
    total = len(scan)
    while off < total or (off == 0 and total == 0):
        main_hdr = struct.pack(">BBBB", 0, (off >> 16) & 0xFF, (off >> 8) & 0xFF, off & 0xFF) + \
            struct.pack(">BBBB", jtype, 255, w8, h8)
        hdr = main_hdr + restart_hdr + (q_hdr if off == 0 else b"")
        room = max(64, mtu - len(hdr))
        chunk = scan[off:off + room]
        out.append(hdr + chunk)
        off += len(chunk)
        if total == 0:
            break
    return out


# --------------------------------------------------------------------------- RTP / RTCP
def rtp_header(pt, marker, seq, ts, ssrc) -> bytes:
    return struct.pack(">BBHII", 0x80, (0x80 if marker else 0) | (pt & 0x7F),
                       seq & 0xFFFF, ts & 0xFFFFFFFF, ssrc & 0xFFFFFFFF)


def ntp_now(now=None):
    now = time.time() if now is None else now
    sec = int(now) + NTP_EPOCH_OFFSET
    frac = int((now % 1.0) * (1 << 32)) & 0xFFFFFFFF
    return sec & 0xFFFFFFFF, frac


def rtcp_sr(ssrc, rtp_ts, packets, octets, cname=b"onvif-vcam", now=None) -> bytes:
    """Sender Report + SDES CNAME compound packet (RFC 3550 6.4.1 / 6.5)."""
    msw, lsw = ntp_now(now)
    sr = struct.pack(">BBHIIIIII", 0x80, 200, 6, ssrc & 0xFFFFFFFF, msw, lsw,
                     rtp_ts & 0xFFFFFFFF, packets & 0xFFFFFFFF, octets & 0xFFFFFFFF)
    item = bytes((1, len(cname))) + cname
    chunk = struct.pack(">I", ssrc & 0xFFFFFFFF) + item + b"\x00"
    while len(chunk) % 4:
        chunk += b"\x00"
    sdes = struct.pack(">BBH", 0x81, 202, len(chunk) // 4) + chunk
    return sr + sdes


def rtcp_bye(ssrc) -> bytes:
    return struct.pack(">BBHI", 0x81, 203, 1, ssrc & 0xFFFFFFFF)
