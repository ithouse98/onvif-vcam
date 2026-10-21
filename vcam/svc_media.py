"""Media service (trt, ver10) and Media2 service (tr2, ver20).

Profile S 7.8 - 7.13 mandatory device functions: profiles, video source / encoder / metadata
configuration handling, stream URI, guaranteed encoder instances, MJPEG option declaration.
Media2 adds H.265 encoder configuration (Profile T) and the new profile/configuration model.
"""

import logging
import re

from .model import Profile, Multicast
from .soap import SoapFault, invalid_arg, not_supported
from .util import esc, xbool, child, children, text, to_bool, to_int, localname

log = logging.getLogger("vcam.media")

MAX_BITRATE = 32768
H264_PROFILES = ("Baseline", "Main", "High")
H265_PROFILES = ("Main",)


def no_profile():
    return invalid_arg("ter:NoProfile", "The requested profile token does not exist")


def no_config():
    return invalid_arg("ter:NoConfig", "The requested configuration does not exist")


def config_modify(reason="The configuration parameters are not possible to set"):
    return invalid_arg("ter:ConfigModify", reason)


# ======================================================================= shared XML
def multicast_xml(m: Multicast):
    return (f"<tt:Multicast><tt:Address><tt:Type>IPv4</tt:Type>"
            f"<tt:IPv4Address>{m.address}</tt:IPv4Address></tt:Address>"
            f"<tt:Port>{m.port}</tt:Port><tt:TTL>{m.ttl}</tt:TTL>"
            f"<tt:AutoStart>{xbool(m.auto_start)}</tt:AutoStart></tt:Multicast>")


def vsc_xml(cam, tag):
    i = cam.info
    return (f'<{tag} token="{cam.vsc_token}"><tt:Name>VideoSourceConfig</tt:Name>'
            f"<tt:UseCount>{cam.use_count('vsc', cam.vsc_token)}</tt:UseCount>"
            f"<tt:SourceToken>{cam.video_source_token}</tt:SourceToken>"
            f'<tt:Bounds x="0" y="0" width="{i.width}" height="{i.height}"/></{tag}>')


def media1_encoding(vec, app):
    if vec.encoding == "H265":
        return app.media1_h265_as
    return vec.encoding


def vec10_xml(cam, vec, tag, app):
    enc = media1_encoding(vec, app)
    h264 = ""
    if enc == "H264":
        prof = vec.profile if vec.profile in H264_PROFILES else "Main"
        h264 = (f"<tt:H264><tt:GovLength>{vec.gop}</tt:GovLength>"
                f"<tt:H264Profile>{prof}</tt:H264Profile></tt:H264>")
    return (f'<{tag} token="{vec.token}"><tt:Name>{esc(vec.name)}</tt:Name>'
            f"<tt:UseCount>{cam.use_count('vec', vec.token)}</tt:UseCount>"
            f"<tt:Encoding>{enc}</tt:Encoding>"
            f"<tt:Resolution><tt:Width>{vec.width}</tt:Width><tt:Height>{vec.height}</tt:Height>"
            f"</tt:Resolution><tt:Quality>{vec.quality:g}</tt:Quality>"
            f"<tt:RateControl><tt:FrameRateLimit>{int(round(vec.fps))}</tt:FrameRateLimit>"
            f"<tt:EncodingInterval>1</tt:EncodingInterval>"
            f"<tt:BitrateLimit>{vec.bitrate_kbps}</tt:BitrateLimit></tt:RateControl>"
            f"{h264}{multicast_xml(vec.multicast)}"
            f"<tt:SessionTimeout>PT{vec.session_timeout}S</tt:SessionTimeout></{tag}>")


def vec20_xml(cam, vec, tag):
    attrs = ""
    if vec.encoding in ("H264", "H265"):
        prof = vec.profile if vec.profile in (H264_PROFILES if vec.encoding == "H264"
                                              else H265_PROFILES) else "Main"
        attrs = f' GovLength="{vec.gop}" Profile="{prof}"'
    return (f'<{tag} token="{vec.token}"{attrs}><tt:Name>{esc(vec.name)}</tt:Name>'
            f"<tt:UseCount>{cam.use_count('vec', vec.token)}</tt:UseCount>"
            f"<tt:Encoding>{vec.encoding}</tt:Encoding>"
            f"<tt:Resolution><tt:Width>{vec.width}</tt:Width><tt:Height>{vec.height}</tt:Height>"
            f'</tt:Resolution><tt:RateControl ConstantBitRate="{xbool(vec.cbr)}">'
            f"<tt:FrameRateLimit>{vec.fps:g}</tt:FrameRateLimit>"
            f"<tt:BitrateLimit>{vec.bitrate_kbps}</tt:BitrateLimit></tt:RateControl>"
            f"{multicast_xml(vec.multicast)}<tt:Quality>{vec.quality:g}</tt:Quality></{tag}>")


def mdc_xml(cam, mdc, tag):
    return (f'<{tag} token="{mdc.token}"><tt:Name>{esc(mdc.name)}</tt:Name>'
            f"<tt:UseCount>{cam.use_count('mdc', mdc.token)}</tt:UseCount>"
            f"<tt:Analytics>{xbool(mdc.analytics)}</tt:Analytics>"
            f"{multicast_xml(mdc.multicast)}"
            f"<tt:SessionTimeout>PT{mdc.session_timeout}S</tt:SessionTimeout></{tag}>")


def parse_multicast(node, m: Multicast):
    if node is None:
        return
    addr = text(node, "Address", "IPv4Address")
    if addr:
        if not re.match(r"^2(2[4-9]|3\d)\.\d+\.\d+\.\d+$", addr):
            raise config_modify("multicast address must be in 224.0.0.0/4")
        m.address = addr
    port = text(node, "Port")
    if port is not None:
        p = to_int(port, -1)
        if not (0 <= p <= 65535) or p % 2:
            raise config_modify("multicast port must be an even number")
        m.port = p
    ttl = text(node, "TTL")
    if ttl is not None:
        m.ttl = max(1, min(255, to_int(ttl, m.ttl)))
    auto = text(node, "AutoStart")
    if auto is not None:
        m.auto_start = to_bool(auto)


class _MediaBase:
    def __init__(self, app):
        self.app = app

    # ------------------------------------------------------------------ lookup helpers
    @staticmethod
    def profile(cam, token):
        p = cam.profiles.get(token or "")
        if p is None:
            raise no_profile()
        return p

    @staticmethod
    def vec(cam, token):
        v = cam.vecs.get(token or "")
        if v is None:
            raise no_config()
        return v

    def stream_uri(self, ctx, profile, multicast=False):
        cam = ctx.camera
        if not profile.vec or not profile.vsc:
            raise SoapFault("Receiver", "ter:Action", "ter:IncompleteConfiguration",
                            "The specified media profile does not contain either a reference to "
                            "a video encoder or to an audio encoder")
        suffix = "/multicast" if multicast else ""
        return f"rtsp://{ctx.host_ip}:{ctx.rtsp_port}/{cam.id}/{profile.token}{suffix}"

    def snapshot_uri(self, ctx, profile):
        return f"http://{ctx.host_ip}:{ctx.camera.http_port}/onvif/snapshot/{profile.token}.jpg"

    # ------------------------------------------------------------------ encoder update
    def apply_vec(self, ctx, vec, enc, w, h, fps, br, gop, prof, quality, cbr, mc_node,
                  session_timeout=None):
        cam = ctx.camera
        allowed = ("H264", "H265", "JPEG")
        if enc not in allowed:
            raise config_modify(f"encoding {enc} not supported")
        res = cam.resolutions(enc)
        if (w, h) not in res and not (vec.mode == "copy" and (w, h) == (vec.width, vec.height)):
            raise config_modify(f"resolution {w}x{h} not supported for {enc}")
        if not (1 <= fps <= max(cam.max_fps(), cam.info.fps)):  # e.g. 15.011 source
            raise config_modify(f"frame rate {fps} out of range 1..{cam.max_fps()}")
        if not (32 <= br <= MAX_BITRATE):
            raise config_modify("bitrate out of range")
        if not (1 <= gop <= 300):
            raise config_modify("GovLength out of range")
        if not (0 <= quality <= 10):
            raise config_modify("quality out of range")
        if enc == "H264" and prof not in H264_PROFILES:
            prof = "Main"
        if enc == "H265":
            prof = "Main"
        old_key = vec.stream_key()
        with cam.lock:
            if vec.mode == "copy" and (enc, w, h, round(fps, 2)) != \
                    (vec.encoding, vec.width, vec.height, round(vec.fps, 2)):
                vec.mode = "transcode"
            vec.encoding, vec.width, vec.height = enc, w, h
            vec.fps, vec.bitrate_kbps, vec.gop, vec.profile = float(fps), int(br), int(gop), prof
            vec.quality, vec.cbr = float(quality), bool(cbr)
            parse_multicast(mc_node, vec.multicast)
            if session_timeout:
                vec.session_timeout = max(5, int(session_timeout))
            cam.save()
        if vec.stream_key() != old_key:
            log.info("%s: %s changed %s -> %s (encoder restarts, viewers reconnect)", cam.id,
                     vec.token, old_key, vec.stream_key())
            self.app.rtsp.drop_vec(cam, vec.token)
        self.app.events.config_changed(cam, "VideoEncoderConfiguration", vec.token)

    # ------------------------------------------------------------------ profiles
    def create_profile(self, cam, name, token=None):
        with cam.lock:
            if len(cam.profiles) >= self.app.max_profiles:
                raise SoapFault("Receiver", "ter:Action", "ter:MaxNVTProfiles",
                                "The maximum number of supported profiles has been reached")
            if token and token in cam.profiles:
                raise invalid_arg("ter:ProfileExists", "A profile with the token already exists")
            if not token:
                n = 1
                while f"Profile_U{n}" in cam.profiles:
                    n += 1
                token = f"Profile_U{n}"
            p = Profile(token=token, name=name or token, fixed=False)
            cam.profiles[token] = p
            cam.save()
            return p

    def delete_profile(self, cam, token):
        with cam.lock:
            p = self.profile(cam, token)
            if p.fixed:
                raise SoapFault("Receiver", "ter:Action", "ter:DeletionOfFixedProfile",
                                "The fixed Profile cannot be deleted")
            del cam.profiles[token]
            cam.save()
        self.app.rtsp.drop_profile(cam, token)

    # ------------------------------------------------------------------ options XML
    def resolutions_xml(self, cam, enc):
        return "".join(f"<tt:ResolutionsAvailable><tt:Width>{w}</tt:Width><tt:Height>{h}"
                       "</tt:Height></tt:ResolutionsAvailable>" for w, h in cam.resolutions(enc))


# ======================================================================= Media (ver10)
class MediaService(_MediaBase):

    def caps_xml(self):
        return ('<trt:Capabilities SnapshotUri="true" Rotation="false" VideoSourceMode="false" '
                'OSD="false" TemporaryOSDText="false" EXICompression="false">'
                f'<trt:ProfileCapabilities MaximumNumberOfProfiles="{self.app.max_profiles}"/>'
                '<trt:StreamingCapabilities RTPMulticast="true" RTP_TCP="true" RTP_RTSP_TCP="true" '
                'NonAggregateControl="false" NoRTSPStreaming="false"/></trt:Capabilities>')

    def GetServiceCapabilities(self, ctx, req):
        return f"<trt:GetServiceCapabilitiesResponse>{self.caps_xml()}" \
               "</trt:GetServiceCapabilitiesResponse>"

    def _profile_xml(self, cam, p, tag="trt:Profiles"):
        inner = f"<tt:Name>{esc(p.name)}</tt:Name>"
        if p.vsc:
            inner += vsc_xml(cam, "tt:VideoSourceConfiguration")
        if p.vec and p.vec in cam.vecs:
            inner += vec10_xml(cam, cam.vecs[p.vec], "tt:VideoEncoderConfiguration", self.app)
        if p.mdc and p.mdc in cam.mdcs:
            inner += mdc_xml(cam, cam.mdcs[p.mdc], "tt:MetadataConfiguration")
        return f'<{tag} token="{p.token}" fixed="{xbool(p.fixed)}">{inner}</{tag}>'

    # profiles -------------------------------------------------------------
    def GetProfiles(self, ctx, req):
        cam = ctx.camera
        with cam.lock:
            body = "".join(self._profile_xml(cam, p) for p in cam.profiles.values())
        return f"<trt:GetProfilesResponse>{body}</trt:GetProfilesResponse>"

    def GetProfile(self, ctx, req):
        cam = ctx.camera
        with cam.lock:
            p = self.profile(cam, text(req, "ProfileToken"))
            return f"<trt:GetProfileResponse>{self._profile_xml(cam, p, 'trt:Profile')}" \
                   "</trt:GetProfileResponse>"

    def CreateProfile(self, ctx, req):
        cam = ctx.camera
        p = self.create_profile(cam, text(req, "Name"), text(req, "Token"))
        return f"<trt:CreateProfileResponse>{self._profile_xml(cam, p, 'trt:Profile')}" \
               "</trt:CreateProfileResponse>"

    def DeleteProfile(self, ctx, req):
        self.delete_profile(ctx.camera, text(req, "ProfileToken"))
        return "<trt:DeleteProfileResponse/>"

    # video sources ----------------------------------------------------------
    def GetVideoSources(self, ctx, req):
        c = ctx.camera
        return (f'<trt:GetVideoSourcesResponse><trt:VideoSources token="{c.video_source_token}">'
                f"<tt:Framerate>{c.info.fps:g}</tt:Framerate><tt:Resolution><tt:Width>"
                f"{c.info.width}</tt:Width><tt:Height>{c.info.height}</tt:Height></tt:Resolution>"
                "</trt:VideoSources></trt:GetVideoSourcesResponse>")

    def GetVideoSourceConfigurations(self, ctx, req):
        return ("<trt:GetVideoSourceConfigurationsResponse>" +
                vsc_xml(ctx.camera, "trt:Configurations") +
                "</trt:GetVideoSourceConfigurationsResponse>")

    def GetVideoSourceConfiguration(self, ctx, req):
        if text(req, "ConfigurationToken") != ctx.camera.vsc_token:
            raise no_config()
        return ("<trt:GetVideoSourceConfigurationResponse>" +
                vsc_xml(ctx.camera, "trt:Configuration") +
                "</trt:GetVideoSourceConfigurationResponse>")

    def GetCompatibleVideoSourceConfigurations(self, ctx, req):
        self.profile(ctx.camera, text(req, "ProfileToken"))
        return ("<trt:GetCompatibleVideoSourceConfigurationsResponse>" +
                vsc_xml(ctx.camera, "trt:Configurations") +
                "</trt:GetCompatibleVideoSourceConfigurationsResponse>")

    def GetVideoSourceConfigurationOptions(self, ctx, req):
        c = ctx.camera
        tok = text(req, "ConfigurationToken")
        if tok and tok != c.vsc_token:
            raise no_config()
        if text(req, "ProfileToken"):
            self.profile(c, text(req, "ProfileToken"))
        i = c.info
        return ("<trt:GetVideoSourceConfigurationOptionsResponse><trt:Options>"
                "<tt:BoundsRange><tt:XRange><tt:Min>0</tt:Min><tt:Max>0</tt:Max></tt:XRange>"
                "<tt:YRange><tt:Min>0</tt:Min><tt:Max>0</tt:Max></tt:YRange>"
                f"<tt:WidthRange><tt:Min>{i.width}</tt:Min><tt:Max>{i.width}</tt:Max></tt:WidthRange>"
                f"<tt:HeightRange><tt:Min>{i.height}</tt:Min><tt:Max>{i.height}</tt:Max>"
                "</tt:HeightRange></tt:BoundsRange>"
                f"<tt:VideoSourceTokensAvailable>{c.video_source_token}"
                "</tt:VideoSourceTokensAvailable></trt:Options>"
                "</trt:GetVideoSourceConfigurationOptionsResponse>")

    def AddVideoSourceConfiguration(self, ctx, req):
        c = ctx.camera
        with c.lock:
            p = self.profile(c, text(req, "ProfileToken"))
            if text(req, "ConfigurationToken") != c.vsc_token:
                raise no_config()
            p.vsc = c.vsc_token
            c.save()
        return "<trt:AddVideoSourceConfigurationResponse/>"

    def RemoveVideoSourceConfiguration(self, ctx, req):
        c = ctx.camera
        with c.lock:
            p = self.profile(c, text(req, "ProfileToken"))
            if p.fixed:
                raise SoapFault("Sender", "ter:InvalidArgVal", "ter:ConfigurationConflict",
                                "Cannot remove configuration from a fixed profile")
            p.vsc = None
            c.save()
        self.app.rtsp.drop_profile(c, p.token)
        return "<trt:RemoveVideoSourceConfigurationResponse/>"

    def SetVideoSourceConfiguration(self, ctx, req):
        conf = child(req, "Configuration")
        if conf is None or conf.get("token") != ctx.camera.vsc_token:
            raise no_config()
        b = child(conf, "Bounds")
        i = ctx.camera.info
        if b is not None and (to_int(b.get("width"), i.width) != i.width or
                              to_int(b.get("height"), i.height) != i.height or
                              to_int(b.get("x"), 0) or to_int(b.get("y"), 0)):
            raise config_modify("bounds are fixed to the sensor size")
        return "<trt:SetVideoSourceConfigurationResponse/>"

    # video encoders ----------------------------------------------------------
    def GetVideoEncoderConfigurations(self, ctx, req):
        c = ctx.camera
        with c.lock:
            body = "".join(vec10_xml(c, v, "trt:Configurations", self.app) for v in c.vecs.values())
        return f"<trt:GetVideoEncoderConfigurationsResponse>{body}" \
               "</trt:GetVideoEncoderConfigurationsResponse>"

    def GetVideoEncoderConfiguration(self, ctx, req):
        c = ctx.camera
        v = self.vec(c, text(req, "ConfigurationToken"))
        return ("<trt:GetVideoEncoderConfigurationResponse>" +
                vec10_xml(c, v, "trt:Configuration", self.app) +
                "</trt:GetVideoEncoderConfigurationResponse>")

    def GetCompatibleVideoEncoderConfigurations(self, ctx, req):
        c = ctx.camera
        self.profile(c, text(req, "ProfileToken"))
        body = "".join(vec10_xml(c, v, "trt:Configurations", self.app) for v in c.vecs.values())
        return f"<trt:GetCompatibleVideoEncoderConfigurationsResponse>{body}" \
               "</trt:GetCompatibleVideoEncoderConfigurationsResponse>"

    def GetVideoEncoderConfigurationOptions(self, ctx, req):
        c = ctx.camera
        tok = text(req, "ConfigurationToken")
        if tok:
            self.vec(c, tok)
        if text(req, "ProfileToken"):
            self.profile(c, text(req, "ProfileToken"))
        mf = c.max_fps()
        rng = (f"<tt:FrameRateRange><tt:Min>1</tt:Min><tt:Max>{mf}</tt:Max></tt:FrameRateRange>"
               "<tt:EncodingIntervalRange><tt:Min>1</tt:Min><tt:Max>1</tt:Max>"
               "</tt:EncodingIntervalRange>")
        br = f"<tt:BitrateRange><tt:Min>32</tt:Min><tt:Max>{MAX_BITRATE}</tt:Max></tt:BitrateRange>"
        jpeg = f"{self.resolutions_xml(c, 'JPEG')}{rng}"
        h264 = (f"{self.resolutions_xml(c, 'H264')}<tt:GovLengthRange><tt:Min>1</tt:Min>"
                f"<tt:Max>300</tt:Max></tt:GovLengthRange>{rng}" +
                "".join(f"<tt:H264ProfilesSupported>{p}</tt:H264ProfilesSupported>"
                        for p in H264_PROFILES))
        return ("<trt:GetVideoEncoderConfigurationOptionsResponse><trt:Options>"
                "<tt:QualityRange><tt:Min>1</tt:Min><tt:Max>10</tt:Max></tt:QualityRange>"
                f"<tt:JPEG>{jpeg}</tt:JPEG><tt:H264>{h264}</tt:H264>"
                f"<tt:Extension><tt:JPEG>{jpeg}{br}</tt:JPEG><tt:H264>{h264}{br}</tt:H264>"
                "</tt:Extension></trt:Options></trt:GetVideoEncoderConfigurationOptionsResponse>")

    def SetVideoEncoderConfiguration(self, ctx, req):
        c = ctx.camera
        conf = child(req, "Configuration")
        if conf is None:
            raise invalid_arg(None, "missing Configuration")
        v = self.vec(c, conf.get("token"))
        enc = (text(conf, "Encoding") or v.encoding).upper()
        if enc == "MPEG4":
            raise config_modify("MPEG4 is not supported")
        if enc == "H264" and v.encoding == "H265" and self.app.media1_h265_as == "H264":
            enc = "H265"  # Media1 cannot express H.265; keep it
        w = to_int(text(conf, "Resolution", "Width"), v.width)
        h = to_int(text(conf, "Resolution", "Height"), v.height)
        q = float(text(conf, "Quality") or v.quality)
        rc = child(conf, "RateControl")
        fps = float(text(rc, "FrameRateLimit") or v.fps) if rc is not None else v.fps
        if fps == round(v.fps):
            fps = v.fps  # Media1 reports FrameRateLimit as an integer; 15 written back = 15.011 kept
        interval = to_int(text(rc, "EncodingInterval"), 1) if rc is not None else 1
        fps = max(1.0, fps / max(1, interval))
        br = to_int(text(rc, "BitrateLimit"), v.bitrate_kbps) if rc is not None else v.bitrate_kbps
        h264 = child(conf, "H264")
        gop = to_int(text(h264, "GovLength"), v.gop) if h264 is not None else v.gop
        prof = text(h264, "H264Profile") or v.profile if h264 is not None else v.profile
        st = text(conf, "SessionTimeout")
        from .util import parse_duration
        self.apply_vec(ctx, v, enc, w, h, fps, br, gop, prof, q, v.cbr, child(conf, "Multicast"),
                       parse_duration(st) if st else None)
        return "<trt:SetVideoEncoderConfigurationResponse/>"

    def AddVideoEncoderConfiguration(self, ctx, req):
        c = ctx.camera
        with c.lock:
            p = self.profile(c, text(req, "ProfileToken"))
            v = self.vec(c, text(req, "ConfigurationToken"))
            p.vec = v.token
            c.save()
        self.app.rtsp.drop_profile(c, p.token)
        return "<trt:AddVideoEncoderConfigurationResponse/>"

    def RemoveVideoEncoderConfiguration(self, ctx, req):
        c = ctx.camera
        with c.lock:
            p = self.profile(c, text(req, "ProfileToken"))
            if p.fixed:
                raise SoapFault("Sender", "ter:InvalidArgVal", "ter:ConfigurationConflict",
                                "Cannot remove configuration from a fixed profile")
            p.vec = None
            c.save()
        self.app.rtsp.drop_profile(c, p.token)
        return "<trt:RemoveVideoEncoderConfigurationResponse/>"

    def GetGuaranteedNumberOfVideoEncoderInstances(self, ctx, req):
        c = ctx.camera
        tok = text(req, "ConfigurationToken")
        if tok and tok != c.vsc_token and tok not in c.vecs:
            raise no_config()
        n = self.app.max_profiles
        return ("<trt:GetGuaranteedNumberOfVideoEncoderInstancesResponse>"
                f"<trt:TotalNumber>{n}</trt:TotalNumber><trt:JPEG>{n}</trt:JPEG>"
                f"<trt:H264>{n}</trt:H264></trt:GetGuaranteedNumberOfVideoEncoderInstancesResponse>")

    # metadata ---------------------------------------------------------------
    def GetMetadataConfigurations(self, ctx, req):
        c = ctx.camera
        return ("<trt:GetMetadataConfigurationsResponse>" +
                "".join(mdc_xml(c, m, "trt:Configurations") for m in c.mdcs.values()) +
                "</trt:GetMetadataConfigurationsResponse>")

    def GetMetadataConfiguration(self, ctx, req):
        c = ctx.camera
        m = c.mdcs.get(text(req, "ConfigurationToken") or "")
        if m is None:
            raise no_config()
        return f"<trt:GetMetadataConfigurationResponse>{mdc_xml(c, m, 'trt:Configuration')}" \
               "</trt:GetMetadataConfigurationResponse>"

    def GetCompatibleMetadataConfigurations(self, ctx, req):
        c = ctx.camera
        self.profile(c, text(req, "ProfileToken"))
        return ("<trt:GetCompatibleMetadataConfigurationsResponse>" +
                "".join(mdc_xml(c, m, "trt:Configurations") for m in c.mdcs.values()) +
                "</trt:GetCompatibleMetadataConfigurationsResponse>")

    def GetMetadataConfigurationOptions(self, ctx, req):
        c = ctx.camera
        tok = text(req, "ConfigurationToken")
        if tok and tok not in c.mdcs:
            raise no_config()
        return ("<trt:GetMetadataConfigurationOptionsResponse><trt:Options>"
                "<tt:PTZStatusFilterOptions><tt:PanTiltStatusSupported>false"
                "</tt:PanTiltStatusSupported><tt:ZoomStatusSupported>false</tt:ZoomStatusSupported>"
                "</tt:PTZStatusFilterOptions></trt:Options>"
                "</trt:GetMetadataConfigurationOptionsResponse>")

    def AddMetadataConfiguration(self, ctx, req):
        c = ctx.camera
        with c.lock:
            p = self.profile(c, text(req, "ProfileToken"))
            tok = text(req, "ConfigurationToken")
            if tok not in c.mdcs:
                raise no_config()
            p.mdc = tok
            c.save()
        return "<trt:AddMetadataConfigurationResponse/>"

    def RemoveMetadataConfiguration(self, ctx, req):
        c = ctx.camera
        with c.lock:
            p = self.profile(c, text(req, "ProfileToken"))
            p.mdc = None
            c.save()
        return "<trt:RemoveMetadataConfigurationResponse/>"

    def SetMetadataConfiguration(self, ctx, req):
        c = ctx.camera
        conf = child(req, "Configuration")
        m = c.mdcs.get(conf.get("token") if conf is not None else "")
        if m is None:
            raise no_config()
        with c.lock:
            if text(conf, "Name"):
                m.name = text(conf, "Name")
            if text(conf, "Analytics") is not None:
                m.analytics = to_bool(text(conf, "Analytics"))
            parse_multicast(child(conf, "Multicast"), m.multicast)
            c.save()
        return "<trt:SetMetadataConfigurationResponse/>"

    # streaming ----------------------------------------------------------------
    def GetStreamUri(self, ctx, req):
        c = ctx.camera
        p = self.profile(c, text(req, "ProfileToken"))
        setup = child(req, "StreamSetup")
        stream = text(setup, "Stream", default="RTP-Unicast")
        proto = text(setup, "Transport", "Protocol", default="RTSP")
        if stream not in ("RTP-Unicast", "RTP-Multicast"):
            raise invalid_arg("ter:InvalidStreamSetup", "Specification of StreamType or Transport "
                                                        "part in StreamSetup is not supported")
        if proto not in ("UDP", "TCP", "RTSP", "HTTP"):
            raise invalid_arg("ter:InvalidStreamSetup", "Transport not supported")
        if stream == "RTP-Multicast" and proto in ("TCP", "HTTP"):
            raise invalid_arg("ter:InvalidStreamSetup", "Multicast over TCP is not possible")
        uri = self.stream_uri(ctx, p, stream == "RTP-Multicast")
        return ("<trt:GetStreamUriResponse><trt:MediaUri>"
                f"<tt:Uri>{esc(uri)}</tt:Uri><tt:InvalidAfterConnect>false</tt:InvalidAfterConnect>"
                "<tt:InvalidAfterReboot>false</tt:InvalidAfterReboot><tt:Timeout>PT0S</tt:Timeout>"
                "</trt:MediaUri></trt:GetStreamUriResponse>")

    def GetSnapshotUri(self, ctx, req):
        p = self.profile(ctx.camera, text(req, "ProfileToken"))
        return ("<trt:GetSnapshotUriResponse><trt:MediaUri>"
                f"<tt:Uri>{esc(self.snapshot_uri(ctx, p))}</tt:Uri>"
                "<tt:InvalidAfterConnect>false</tt:InvalidAfterConnect>"
                "<tt:InvalidAfterReboot>false</tt:InvalidAfterReboot><tt:Timeout>PT0S</tt:Timeout>"
                "</trt:MediaUri></trt:GetSnapshotUriResponse>")

    def SetSynchronizationPoint(self, ctx, req):
        self.profile(ctx.camera, text(req, "ProfileToken"))
        return "<trt:SetSynchronizationPointResponse/>"

    def StartMulticastStreaming(self, ctx, req):
        p = self.profile(ctx.camera, text(req, "ProfileToken"))
        self.stream_uri(ctx, p, True)
        self.app.rtsp.multicast_start(ctx.camera, p.token, persistent=True)
        return "<trt:StartMulticastStreamingResponse/>"

    def StopMulticastStreaming(self, ctx, req):
        p = self.profile(ctx.camera, text(req, "ProfileToken"))
        self.app.rtsp.multicast_stop(ctx.camera, p.token)
        return "<trt:StopMulticastStreamingResponse/>"

    # no audio / analytics / PTZ on this device: empty lists ------------------
    def _empty(name):
        def fn(self, ctx, req, _n=name):
            return f"<trt:{_n}Response/>"
        return fn

    GetAudioSources = _empty("GetAudioSources")
    GetAudioSourceConfigurations = _empty("GetAudioSourceConfigurations")
    GetAudioEncoderConfigurations = _empty("GetAudioEncoderConfigurations")
    GetAudioOutputs = _empty("GetAudioOutputs")
    GetAudioOutputConfigurations = _empty("GetAudioOutputConfigurations")
    GetAudioDecoderConfigurations = _empty("GetAudioDecoderConfigurations")
    GetCompatibleAudioSourceConfigurations = _empty("GetCompatibleAudioSourceConfigurations")
    GetCompatibleAudioEncoderConfigurations = _empty("GetCompatibleAudioEncoderConfigurations")
    GetCompatibleAudioOutputConfigurations = _empty("GetCompatibleAudioOutputConfigurations")
    GetCompatibleAudioDecoderConfigurations = _empty("GetCompatibleAudioDecoderConfigurations")
    GetVideoAnalyticsConfigurations = _empty("GetVideoAnalyticsConfigurations")
    GetCompatibleVideoAnalyticsConfigurations = _empty("GetCompatibleVideoAnalyticsConfigurations")
    GetOSDs = _empty("GetOSDs")
    del _empty


# ======================================================================= Media2 (ver20)
CONF_TYPES = ("VideoSource", "VideoEncoder", "Metadata")


class Media2Service(_MediaBase):

    def caps_xml(self):
        return ('<tr2:Capabilities SnapshotUri="true" Rotation="false" VideoSourceMode="false" '
                'OSD="false" TemporaryOSDText="false" Mask="false" SourceMask="false">'
                f'<tr2:ProfileCapabilities MaximumNumberOfProfiles="{self.app.max_profiles}" '
                'ConfigurationsSupported="VideoSource VideoEncoder Metadata"/>'
                '<tr2:StreamingCapabilities RTSPStreaming="true" RTPMulticast="true" '
                'RTP_RTSP_TCP="true" NonAggregateControl="false" AutoStartMulticast="true"/>'
                '</tr2:Capabilities>')

    def GetServiceCapabilities(self, ctx, req):
        return f"<tr2:GetServiceCapabilitiesResponse>{self.caps_xml()}" \
               "</tr2:GetServiceCapabilitiesResponse>"

    def _profile_xml(self, cam, p, types):
        conf = ""
        if types:
            if ("All" in types or "VideoSource" in types) and p.vsc:
                conf += vsc_xml(cam, "tr2:VideoSource")
            if ("All" in types or "VideoEncoder" in types) and p.vec in cam.vecs:
                conf += vec20_xml(cam, cam.vecs[p.vec], "tr2:VideoEncoder")
            if ("All" in types or "Metadata" in types) and p.mdc in cam.mdcs:
                conf += mdc_xml(cam, cam.mdcs[p.mdc], "tr2:Metadata")
        return (f'<tr2:Profiles token="{p.token}" fixed="{xbool(p.fixed)}">'
                f"<tr2:Name>{esc(p.name)}</tr2:Name>"
                f"{'<tr2:Configurations>' + conf + '</tr2:Configurations>' if types else ''}"
                "</tr2:Profiles>")

    def GetProfiles(self, ctx, req):
        c = ctx.camera
        tok = text(req, "Token")
        types = [t.text.strip() for t in children(req, "Type") if t.text]
        with c.lock:
            if tok:
                profs = [self.profile(c, tok)]
            else:
                profs = list(c.profiles.values())
            body = "".join(self._profile_xml(c, p, types) for p in profs)
        return f"<tr2:GetProfilesResponse>{body}</tr2:GetProfilesResponse>"

    def _apply_confs(self, c, p, confs):
        for cf in confs:
            typ, tok = text(cf, "Type"), text(cf, "Token")
            if typ == "VideoSource":
                if tok and tok != c.vsc_token:
                    raise no_config()
                p.vsc = c.vsc_token
            elif typ == "VideoEncoder":
                if not tok:
                    tok = next(iter(c.vecs))
                self.vec(c, tok)
                p.vec = tok
            elif typ == "Metadata":
                tok = tok or "MDC_1"
                if tok not in c.mdcs:
                    raise no_config()
                p.mdc = tok
            elif typ == "All":
                p.vsc = c.vsc_token
                p.vec = p.vec or next(iter(c.vecs))
            else:
                raise SoapFault("Sender", "ter:InvalidArgVal", "ter:ConfigurationConflict",
                                f"Configuration type {typ} not supported")

    def CreateProfile(self, ctx, req):
        c = ctx.camera
        p = self.create_profile(c, text(req, "Name"))
        with c.lock:
            self._apply_confs(c, p, children(req, "Configuration"))
            c.save()
        return f"<tr2:CreateProfileResponse><tr2:Token>{p.token}</tr2:Token>" \
               "</tr2:CreateProfileResponse>"

    def DeleteProfile(self, ctx, req):
        self.delete_profile(ctx.camera, text(req, "Token"))
        return "<tr2:DeleteProfileResponse/>"

    def AddConfiguration(self, ctx, req):
        c = ctx.camera
        with c.lock:
            p = self.profile(c, text(req, "ProfileToken"))
            if text(req, "Name"):
                p.name = text(req, "Name")
            self._apply_confs(c, p, children(req, "Configuration"))
            c.save()
        self.app.rtsp.drop_profile(c, p.token)
        return "<tr2:AddConfigurationResponse/>"

    def RemoveConfiguration(self, ctx, req):
        c = ctx.camera
        with c.lock:
            p = self.profile(c, text(req, "ProfileToken"))
            for cf in children(req, "Configuration"):
                typ = text(cf, "Type")
                if p.fixed and typ in ("VideoSource", "VideoEncoder", "All"):
                    raise SoapFault("Sender", "ter:InvalidArgVal", "ter:ConfigurationConflict",
                                    "Cannot remove configuration from a fixed profile")
                if typ in ("VideoSource", "All"):
                    p.vsc = None
                if typ in ("VideoEncoder", "All"):
                    p.vec = None
                if typ in ("Metadata", "All"):
                    p.mdc = None
            c.save()
        self.app.rtsp.drop_profile(c, p.token)
        return "<tr2:RemoveConfigurationResponse/>"

    def _filter(self, c, req, kind):
        tok, ptok = text(req, "ConfigurationToken"), text(req, "ProfileToken")
        p = self.profile(c, ptok) if ptok else None
        return tok, p

    def GetVideoSourceConfigurations(self, ctx, req):
        c = ctx.camera
        tok, p = self._filter(c, req, "vsc")
        if tok and tok != c.vsc_token:
            raise no_config()
        body = vsc_xml(c, "tr2:Configurations")
        return f"<tr2:GetVideoSourceConfigurationsResponse>{body}" \
               "</tr2:GetVideoSourceConfigurationsResponse>"

    def GetVideoEncoderConfigurations(self, ctx, req):
        c = ctx.camera
        tok, p = self._filter(c, req, "vec")
        with c.lock:
            if tok:
                vecs = [self.vec(c, tok)]
            elif p is not None:
                vecs = list(c.vecs.values())  # all encoders are compatible with every profile
            else:
                vecs = list(c.vecs.values())
            body = "".join(vec20_xml(c, v, "tr2:Configurations") for v in vecs)
        return f"<tr2:GetVideoEncoderConfigurationsResponse>{body}" \
               "</tr2:GetVideoEncoderConfigurationsResponse>"

    def GetMetadataConfigurations(self, ctx, req):
        c = ctx.camera
        tok, p = self._filter(c, req, "mdc")
        mdcs = [c.mdcs[tok]] if tok in c.mdcs else list(c.mdcs.values())
        if tok and tok not in c.mdcs:
            raise no_config()
        return ("<tr2:GetMetadataConfigurationsResponse>" +
                "".join(mdc_xml(c, m, "tr2:Configurations") for m in mdcs) +
                "</tr2:GetMetadataConfigurationsResponse>")

    def GetVideoSourceConfigurationOptions(self, ctx, req):
        out = self.app.services["media"].GetVideoSourceConfigurationOptions(ctx, req)
        return out.replace("trt:GetVideoSourceConfigurationOptionsResponse",
                           "tr2:GetVideoSourceConfigurationOptionsResponse").replace(
            "trt:Options", "tr2:Options")

    def GetVideoEncoderConfigurationOptions(self, ctx, req):
        c = ctx.camera
        tok = text(req, "ConfigurationToken")
        if tok:
            self.vec(c, tok)
        if text(req, "ProfileToken"):
            self.profile(c, text(req, "ProfileToken"))
        mf = c.max_fps()
        rates = " ".join(f"{r:g}" for r in sorted({mf, 30, 25, 20, 15, 12.5, 10, 8, 5, 3, 2, 1},
                                                  reverse=True) if r <= mf)
        br = f"<tt:BitrateRange><tt:Min>32</tt:Min><tt:Max>{MAX_BITRATE}</tt:Max></tt:BitrateRange>"
        q = "<tt:QualityRange><tt:Min>1</tt:Min><tt:Max>10</tt:Max></tt:QualityRange>"
        out = []
        for enc, profs in (("H264", H264_PROFILES), ("H265", H265_PROFILES)):
            out.append(f'<tr2:Options GovLengthRange="1 300" FrameRatesSupported="{rates}" '
                       f'ProfilesSupported="{" ".join(profs)}" ConstantBitRateSupported="true">'
                       f"<tt:Encoding>{enc}</tt:Encoding>{q}{self.resolutions_xml(c, enc)}{br}"
                       "</tr2:Options>")
        out.append(f'<tr2:Options FrameRatesSupported="{rates}" ConstantBitRateSupported="false">'
                   f"<tt:Encoding>JPEG</tt:Encoding>{q}{self.resolutions_xml(c, 'JPEG')}{br}"
                   "</tr2:Options>")
        return "<tr2:GetVideoEncoderConfigurationOptionsResponse>" + "".join(out) + \
            "</tr2:GetVideoEncoderConfigurationOptionsResponse>"

    def GetMetadataConfigurationOptions(self, ctx, req):
        out = self.app.services["media"].GetMetadataConfigurationOptions(ctx, req)
        return out.replace("trt:GetMetadataConfigurationOptionsResponse",
                           "tr2:GetMetadataConfigurationOptionsResponse").replace(
            "trt:Options", "tr2:Options")

    def SetVideoEncoderConfiguration(self, ctx, req):
        c = ctx.camera
        conf = child(req, "Configuration")
        if conf is None:
            raise invalid_arg(None, "missing Configuration")
        v = self.vec(c, conf.get("token"))
        enc = (text(conf, "Encoding") or v.encoding).upper().replace("HEVC", "H265")
        if enc in ("MPV4-ES", "MPEG4"):
            raise config_modify("MPEG4 is not supported")
        w = to_int(text(conf, "Resolution", "Width"), v.width)
        h = to_int(text(conf, "Resolution", "Height"), v.height)
        rc = child(conf, "RateControl")
        fps = float(text(rc, "FrameRateLimit") or v.fps) if rc is not None else v.fps
        br = to_int(text(rc, "BitrateLimit"), v.bitrate_kbps) if rc is not None else v.bitrate_kbps
        cbr = to_bool(rc.get("ConstantBitRate"), v.cbr) if rc is not None else v.cbr
        gop = to_int(conf.get("GovLength"), v.gop)
        prof = conf.get("Profile") or v.profile
        q = float(text(conf, "Quality") or v.quality)
        self.apply_vec(ctx, v, enc, w, h, fps, br, gop, prof, q, cbr, child(conf, "Multicast"))
        return "<tr2:SetVideoEncoderConfigurationResponse/>"

    def SetVideoSourceConfiguration(self, ctx, req):
        self.app.services["media"].SetVideoSourceConfiguration(ctx, req)
        return "<tr2:SetVideoSourceConfigurationResponse/>"

    def SetMetadataConfiguration(self, ctx, req):
        self.app.services["media"].SetMetadataConfiguration(ctx, req)
        return "<tr2:SetMetadataConfigurationResponse/>"

    def GetVideoEncoderInstances(self, ctx, req):
        c = ctx.camera
        tok = text(req, "ConfigurationToken")
        if tok and tok != c.vsc_token:
            raise no_config()
        n = self.app.max_profiles
        return ("<tr2:GetVideoEncoderInstancesResponse><tr2:Info>"
                f"<tr2:Codec><tr2:Encoding>H264</tr2:Encoding><tr2:Number>{n}</tr2:Number>"
                f"</tr2:Codec><tr2:Codec><tr2:Encoding>H265</tr2:Encoding><tr2:Number>{n}"
                f"</tr2:Number></tr2:Codec><tr2:Codec><tr2:Encoding>JPEG</tr2:Encoding>"
                f"<tr2:Number>{n}</tr2:Number></tr2:Codec><tr2:Total>{n}</tr2:Total>"
                "</tr2:Info></tr2:GetVideoEncoderInstancesResponse>")

    def GetStreamUri(self, ctx, req):
        c = ctx.camera
        p = self.profile(c, text(req, "ProfileToken"))
        proto = text(req, "Protocol", default="RtspUnicast")
        if proto in ("RtspsUnicast", "RtspsMulticast", "RtspOverWebSocket", "RTSPS"):
            raise invalid_arg("ter:InvalidStreamSetup", f"{proto} is not supported")
        multicast = proto == "RtspMulticast"
        uri = self.stream_uri(ctx, p, multicast)
        return f"<tr2:GetStreamUriResponse><tr2:Uri>{esc(uri)}</tr2:Uri></tr2:GetStreamUriResponse>"

    def GetSnapshotUri(self, ctx, req):
        p = self.profile(ctx.camera, text(req, "ProfileToken"))
        return (f"<tr2:GetSnapshotUriResponse><tr2:Uri>{esc(self.snapshot_uri(ctx, p))}</tr2:Uri>"
                "</tr2:GetSnapshotUriResponse>")

    def SetSynchronizationPoint(self, ctx, req):
        self.profile(ctx.camera, text(req, "ProfileToken"))
        return "<tr2:SetSynchronizationPointResponse/>"

    def StartMulticastStreaming(self, ctx, req):
        p = self.profile(ctx.camera, text(req, "ProfileToken"))
        self.stream_uri(ctx, p, True)
        self.app.rtsp.multicast_start(ctx.camera, p.token, persistent=True)
        return "<tr2:StartMulticastStreamingResponse/>"

    def StopMulticastStreaming(self, ctx, req):
        p = self.profile(ctx.camera, text(req, "ProfileToken"))
        self.app.rtsp.multicast_stop(ctx.camera, p.token)
        return "<tr2:StopMulticastStreamingResponse/>"

    def GetVideoSourceModes(self, ctx, req):
        c = ctx.camera
        i = c.info
        return ("<tr2:GetVideoSourceModesResponse>"
                f'<tr2:VideoSourceModes token="VSM_1" Enabled="true">'
                f"<tr2:MaxFramerate>{i.fps:g}</tr2:MaxFramerate><tr2:MaxResolution>"
                f"<tt:Width>{i.width}</tt:Width><tt:Height>{i.height}</tt:Height>"
                "</tr2:MaxResolution><tr2:Encodings>H264 H265 JPEG</tr2:Encodings>"
                "<tr2:Reboot>false</tr2:Reboot></tr2:VideoSourceModes>"
                "</tr2:GetVideoSourceModesResponse>")

    def _empty(name):
        def fn(self, ctx, req, _n=name):
            return f"<tr2:{_n}Response/>"
        return fn

    GetAudioSourceConfigurations = _empty("GetAudioSourceConfigurations")
    GetAudioEncoderConfigurations = _empty("GetAudioEncoderConfigurations")
    GetAudioOutputConfigurations = _empty("GetAudioOutputConfigurations")
    GetAudioDecoderConfigurations = _empty("GetAudioDecoderConfigurations")
    GetAnalyticsConfigurations = _empty("GetAnalyticsConfigurations")
    GetOSDs = _empty("GetOSDs")
    GetMasks = _empty("GetMasks")
    del _empty


__all__ = ["MediaService", "Media2Service", "localname", "not_supported", "CONF_TYPES"]
