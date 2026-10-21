"""Device management service (tds) - ONVIF Core Specification section 8.

Covers every Profile S mandatory device function: capabilities (7.2), discovery (7.3),
network configuration (7.4), system (7.5) and user handling (7.6), plus NTP/relay stubs.
"""

import datetime as dt
import logging
import threading

from .model import User, USER_LEVELS
from .soap import SoapFault, invalid_arg, not_supported, NS
from .util import (esc, child, children, text, to_bool, to_int, utcnow, localname)

log = logging.getLogger("vcam.device")

SPEC_MAJOR, SPEC_MINOR = 21, 12   # interface version advertised in GetServices
CAP_MAJOR, CAP_MINOR = 2, 60      # legacy SupportedVersions in GetCapabilities


def _ver(major, minor):
    return f"<tds:Version><tt:Major>{major}</tt:Major><tt:Minor>{minor}</tt:Minor></tds:Version>"


class DeviceService:
    def __init__(self, app):
        self.app = app

    # ================================================================== capabilities
    def device_caps_xml(self, cam):
        return (
            '<tds:Capabilities>'
            '<tds:Network IPFilter="false" ZeroConfiguration="false" IPVersion6="false" '
            'DynDNS="false" Dot11Configuration="false" HostnameFromDHCP="false" NTP="3" '
            'DHCPv6="false"/>'
            '<tds:Security TLS1.0="false" TLS1.1="false" TLS1.2="false" OnboardKeyGeneration="false" '
            'AccessPolicyConfig="false" DefaultAccessPolicy="true" Dot1X="false" '
            'RemoteUserHandling="false" X.509Token="false" SAMLToken="false" KerberosToken="false" '
            'UsernameToken="true" HttpDigest="true" RELToken="false" MaxUsers="16" '
            'MaxUserNameLength="32" MaxPasswordLength="64" '
            f'HashingAlgorithms="{esc(" ".join(cam.authenticator.algorithms))}"/>'
            '<tds:System DiscoveryResolve="true" DiscoveryBye="true" RemoteDiscovery="false" '
            'SystemBackup="false" SystemLogging="false" FirmwareUpgrade="false" '
            'HttpFirmwareUpgrade="false" HttpSystemBackup="false" HttpSystemLogging="false" '
            'HttpSupportInformation="false" StorageConfiguration="false"/>'
            '<tds:Misc AuxiliaryCommands=""/>'
            '</tds:Capabilities>')

    def GetServiceCapabilities(self, ctx, req):
        return f"<tds:GetServiceCapabilitiesResponse>{self.device_caps_xml(ctx.camera)}" \
               "</tds:GetServiceCapabilitiesResponse>"

    def GetServices(self, ctx, req):
        inc = to_bool(text(req, "IncludeCapability"))
        cam = ctx.camera
        app = self.app

        def svc(ns, path, caps):
            c = f"<tds:Capabilities>{caps}</tds:Capabilities>" if inc else ""
            return (f"<tds:Service><tds:Namespace>{ns}</tds:Namespace>"
                    f"<tds:XAddr>{ctx.xaddr(path)}</tds:XAddr>{c}"
                    f"{_ver(SPEC_MAJOR, SPEC_MINOR)}</tds:Service>")

        dev_caps = self.device_caps_xml(cam)
        out = [svc(NS["tds"], "device_service", dev_caps),
               svc(NS["trt"], "media_service", app.services["media"].caps_xml()),
               svc(NS["tr2"], "media2_service", app.services["media2"].caps_xml()),
               svc(NS["tev"], "events_service", app.services["events"].caps_xml())]
        return "<tds:GetServicesResponse>" + "".join(out) + "</tds:GetServicesResponse>"

    def GetCapabilities(self, ctx, req):
        cats = [c.text.strip() for c in children(req, "Category") if c.text] or ["All"]
        want = set(cats)
        for c in want:
            if c in ("Analytics", "PTZ", "Imaging"):
                raise not_supported("ter:NoSuchService", f"{c} service not supported")
        allc = "All" in want
        parts = []
        if allc or "Device" in want:
            parts.append(
                "<tt:Device>"
                f"<tt:XAddr>{ctx.xaddr('device_service')}</tt:XAddr>"
                "<tt:Network><tt:IPFilter>false</tt:IPFilter><tt:ZeroConfiguration>false"
                "</tt:ZeroConfiguration><tt:IPVersion6>false</tt:IPVersion6><tt:DynDNS>false"
                "</tt:DynDNS><tt:Extension><tt:Dot11Configuration>false</tt:Dot11Configuration>"
                "</tt:Extension></tt:Network>"
                "<tt:System><tt:DiscoveryResolve>true</tt:DiscoveryResolve><tt:DiscoveryBye>true"
                "</tt:DiscoveryBye><tt:RemoteDiscovery>false</tt:RemoteDiscovery><tt:SystemBackup>"
                "false</tt:SystemBackup><tt:SystemLogging>false</tt:SystemLogging>"
                "<tt:FirmwareUpgrade>false</tt:FirmwareUpgrade>"
                + "".join(f"<tt:SupportedVersions><tt:Major>{ma}</tt:Major><tt:Minor>{mi}</tt:Minor>"
                          f"</tt:SupportedVersions>" for ma, mi in
                          ((CAP_MAJOR, CAP_MINOR), (2, 40), (2, 20), (2, 0)))
                + "<tt:Extension><tt:HttpFirmwareUpgrade>false</tt:HttpFirmwareUpgrade>"
                "<tt:HttpSystemBackup>false</tt:HttpSystemBackup><tt:HttpSystemLogging>false"
                "</tt:HttpSystemLogging><tt:HttpSupportInformation>false</tt:HttpSupportInformation>"
                "</tt:Extension></tt:System>"
                "<tt:IO><tt:InputConnectors>0</tt:InputConnectors><tt:RelayOutputs>0"
                "</tt:RelayOutputs></tt:IO>"
                "<tt:Security><tt:TLS1.1>false</tt:TLS1.1><tt:TLS1.2>false</tt:TLS1.2>"
                "<tt:OnboardKeyGeneration>false</tt:OnboardKeyGeneration><tt:AccessPolicyConfig>"
                "false</tt:AccessPolicyConfig><tt:X.509Token>false</tt:X.509Token><tt:SAMLToken>"
                "false</tt:SAMLToken><tt:KerberosToken>false</tt:KerberosToken><tt:RELToken>false"
                "</tt:RELToken><tt:Extension><tt:TLS1.0>false</tt:TLS1.0><tt:Extension>"
                "<tt:Dot1X>false</tt:Dot1X><tt:SupportedEAPMethod>0</tt:SupportedEAPMethod>"
                "<tt:RemoteUserHandling>false</tt:RemoteUserHandling></tt:Extension></tt:Extension>"
                "</tt:Security></tt:Device>")
        if allc or "Events" in want:
            parts.append(
                f"<tt:Events><tt:XAddr>{ctx.xaddr('events_service')}</tt:XAddr>"
                "<tt:WSSubscriptionPolicySupport>true</tt:WSSubscriptionPolicySupport>"
                "<tt:WSPullPointSupport>true</tt:WSPullPointSupport>"
                "<tt:WSPausableSubscriptionManagerInterfaceSupport>false"
                "</tt:WSPausableSubscriptionManagerInterfaceSupport></tt:Events>")
        if allc or "Media" in want:
            parts.append(
                f"<tt:Media><tt:XAddr>{ctx.xaddr('media_service')}</tt:XAddr>"
                "<tt:StreamingCapabilities><tt:RTPMulticast>true</tt:RTPMulticast>"
                "<tt:RTP_TCP>true</tt:RTP_TCP><tt:RTP_RTSP_TCP>true</tt:RTP_RTSP_TCP>"
                "</tt:StreamingCapabilities><tt:Extension><tt:ProfileCapabilities>"
                f"<tt:MaximumNumberOfProfiles>{self.app.max_profiles}</tt:MaximumNumberOfProfiles>"
                "</tt:ProfileCapabilities></tt:Extension></tt:Media>")
        if allc:
            parts.append(
                "<tt:Extension><tt:DeviceIO><tt:XAddr>"
                f"{ctx.xaddr('device_service')}</tt:XAddr><tt:VideoSources>1</tt:VideoSources>"
                "<tt:VideoOutputs>0</tt:VideoOutputs><tt:AudioSources>0</tt:AudioSources>"
                "<tt:AudioOutputs>0</tt:AudioOutputs><tt:RelayOutputs>0</tt:RelayOutputs>"
                "</tt:DeviceIO></tt:Extension>")
        return ("<tds:GetCapabilitiesResponse><tds:Capabilities>" + "".join(parts) +
                "</tds:Capabilities></tds:GetCapabilitiesResponse>")

    def GetWsdlUrl(self, ctx, req):
        return ("<tds:GetWsdlUrlResponse><tds:WsdlUrl>http://www.onvif.org/onvif/ver10/device/wsdl/"
                "devicemgmt.wsdl</tds:WsdlUrl></tds:GetWsdlUrlResponse>")

    def GetEndpointReference(self, ctx, req):
        return (f"<tds:GetEndpointReferenceResponse><tds:GUID>urn:uuid:{ctx.camera.uuid}</tds:GUID>"
                "</tds:GetEndpointReferenceResponse>")

    # ================================================================== system
    def GetDeviceInformation(self, ctx, req):
        c = ctx.camera
        return ("<tds:GetDeviceInformationResponse>"
                f"<tds:Manufacturer>{esc(c.manufacturer)}</tds:Manufacturer>"
                f"<tds:Model>{esc(c.model)}</tds:Model>"
                f"<tds:FirmwareVersion>{esc(c.firmware)}</tds:FirmwareVersion>"
                f"<tds:SerialNumber>{esc(c.serial)}</tds:SerialNumber>"
                f"<tds:HardwareId>{esc(c.hardware_id)}</tds:HardwareId>"
                "</tds:GetDeviceInformationResponse>")

    @staticmethod
    def _dt_xml(t):
        return (f"<tt:Time><tt:Hour>{t.hour}</tt:Hour><tt:Minute>{t.minute}</tt:Minute>"
                f"<tt:Second>{t.second}</tt:Second></tt:Time><tt:Date><tt:Year>{t.year}</tt:Year>"
                f"<tt:Month>{t.month}</tt:Month><tt:Day>{t.day}</tt:Day></tt:Date>")

    def GetSystemDateAndTime(self, ctx, req):
        c = ctx.camera
        now = utcnow() + dt.timedelta(seconds=c.time_offset)
        local = now  # TZ string is reported; local clock == UTC unless TZ set to offset form
        return ("<tds:GetSystemDateAndTimeResponse><tds:SystemDateAndTime>"
                f"<tt:DateTimeType>{'NTP' if c.ntp_mode else 'Manual'}</tt:DateTimeType>"
                "<tt:DaylightSavings>false</tt:DaylightSavings>"
                f"<tt:TimeZone><tt:TZ>{esc(c.tz)}</tt:TZ></tt:TimeZone>"
                f"<tt:UTCDateTime>{self._dt_xml(now)}</tt:UTCDateTime>"
                f"<tt:LocalDateTime>{self._dt_xml(local)}</tt:LocalDateTime>"
                "</tds:SystemDateAndTime></tds:GetSystemDateAndTimeResponse>")

    def SetSystemDateAndTime(self, ctx, req):
        c = ctx.camera
        mode = text(req, "DateTimeType", default="Manual")
        tz = text(req, "TimeZone", "TZ")
        utc = child(req, "UTCDateTime")
        with c.lock:
            c.ntp_mode = mode == "NTP"
            if tz:
                c.tz = tz
            if mode == "Manual" and utc is not None:
                try:
                    t = dt.datetime(to_int(text(utc, "Date", "Year")), to_int(text(utc, "Date", "Month")),
                                    to_int(text(utc, "Date", "Day")), to_int(text(utc, "Time", "Hour")),
                                    to_int(text(utc, "Time", "Minute")),
                                    to_int(text(utc, "Time", "Second")), tzinfo=dt.timezone.utc)
                except ValueError:
                    raise invalid_arg("ter:InvalidDateTime", "invalid date/time")
                c.time_offset = (t - utcnow()).total_seconds()
                if abs(c.time_offset) < 2:
                    c.time_offset = 0.0
            elif mode == "NTP":
                c.time_offset = 0.0
            c.save()
        return "<tds:SetSystemDateAndTimeResponse/>"

    def SetSystemFactoryDefault(self, ctx, req):
        hard = text(req, "FactoryDefault") == "Hard"
        ctx.camera.factory_reset(hard)
        return "<tds:SetSystemFactoryDefaultResponse/>"

    def SystemReboot(self, ctx, req):
        cam = ctx.camera

        def reboot():
            self.app.discovery.bye(cam)
            self.app.rtsp.drop_camera(cam)
            threading.Event().wait(3)
            self.app.discovery.hello(cam)
        threading.Thread(target=reboot, daemon=True).start()
        return ("<tds:SystemRebootResponse><tds:Message>Rebooting in 3 seconds</tds:Message>"
                "</tds:SystemRebootResponse>")

    def GetSystemUris(self, ctx, req):
        return "<tds:GetSystemUrisResponse/>"

    # ================================================================== scopes / discovery
    def GetScopes(self, ctx, req):
        c = ctx.camera
        with c.lock:
            items = [("Fixed", s) for s in c.fixed_scopes] + [("Configurable", s) for s in c.scopes]
        return "<tds:GetScopesResponse>" + "".join(
            f"<tds:Scopes><tt:ScopeDef>{d}</tt:ScopeDef><tt:ScopeItem>{esc(s)}</tt:ScopeItem>"
            "</tds:Scopes>" for d, s in items) + "</tds:GetScopesResponse>"

    def _scope_args(self, req):
        return [s.text.strip() for s in children(req, "Scopes") + children(req, "ScopeItem")
                if s.text and s.text.strip()]

    def SetScopes(self, ctx, req):
        c = ctx.camera
        new = self._scope_args(req)
        for s in new:
            if s in c.fixed_scopes:
                raise SoapFault("Sender", "ter:OperationProhibited", "ter:ScopeOverwrite",
                                "Scope overwrite")
        with c.lock:
            c.scopes = new
            c.save()
        self.app.discovery.hello(c)
        return "<tds:SetScopesResponse/>"

    def AddScopes(self, ctx, req):
        c = ctx.camera
        add = self._scope_args(req)
        with c.lock:
            for s in add:
                if s not in c.scopes and s not in c.fixed_scopes:
                    c.scopes.append(s)
            c.save()
        self.app.discovery.hello(c)
        return "<tds:AddScopesResponse/>"

    def RemoveScopes(self, ctx, req):
        c = ctx.camera
        rem = self._scope_args(req)
        with c.lock:
            for s in rem:
                if s in c.fixed_scopes:
                    raise SoapFault("Sender", "ter:OperationProhibited", "ter:FixedScope",
                                    "Trying to remove fixed scope parameter")
                if s not in c.scopes:
                    raise invalid_arg("ter:NoScope", "Trying to remove scope which does not exist")
            c.scopes = [s for s in c.scopes if s not in rem]
            c.save()
        self.app.discovery.hello(c)
        return ("<tds:RemoveScopesResponse>" +
                "".join(f"<tds:ScopeItem>{esc(s)}</tds:ScopeItem>" for s in rem) +
                "</tds:RemoveScopesResponse>")

    def GetDiscoveryMode(self, ctx, req):
        mode = "Discoverable" if ctx.camera.discoverable else "NonDiscoverable"
        return (f"<tds:GetDiscoveryModeResponse><tds:DiscoveryMode>{mode}</tds:DiscoveryMode>"
                "</tds:GetDiscoveryModeResponse>")

    def SetDiscoveryMode(self, ctx, req):
        mode = text(req, "DiscoveryMode")
        if mode not in ("Discoverable", "NonDiscoverable"):
            raise invalid_arg(None, "invalid discovery mode")
        c = ctx.camera
        with c.lock:
            was = c.discoverable
            c.discoverable = mode == "Discoverable"
            c.save()
        if c.discoverable and not was:
            self.app.discovery.hello(c)
        elif was and not c.discoverable:
            self.app.discovery.bye(c)
        return "<tds:SetDiscoveryModeResponse/>"

    def GetRemoteDiscoveryMode(self, ctx, req):
        raise not_supported()

    def GetDPAddresses(self, ctx, req):
        raise not_supported()

    # ================================================================== network
    def GetHostname(self, ctx, req):
        return ("<tds:GetHostnameResponse><tds:HostnameInformation><tt:FromDHCP>false</tt:FromDHCP>"
                f"<tt:Name>{esc(ctx.camera.hostname)}</tt:Name></tds:HostnameInformation>"
                "</tds:GetHostnameResponse>")

    def SetHostname(self, ctx, req):
        name = text(req, "Name", default="")
        import re
        if not name or not re.match(r"^[A-Za-z0-9]([A-Za-z0-9\-]{0,61}[A-Za-z0-9])?"
                                    r"(\.[A-Za-z0-9]([A-Za-z0-9\-]{0,61}[A-Za-z0-9])?)*$", name):
            raise invalid_arg("ter:InvalidHostname", "Invalid hostname")
        with ctx.camera.lock:
            ctx.camera.hostname = name
            ctx.camera.save()
        return "<tds:SetHostnameResponse/>"

    def SetHostnameFromDHCP(self, ctx, req):
        return "<tds:SetHostnameFromDHCPResponse><tds:RebootNeeded>false</tds:RebootNeeded>" \
               "</tds:SetHostnameFromDHCPResponse>"

    def GetDNS(self, ctx, req):
        dns = "".join(f"<tt:DNSManual><tt:Type>IPv4</tt:Type><tt:IPv4Address>{esc(d)}"
                      "</tt:IPv4Address></tt:DNSManual>" for d in ctx.camera.dns)
        return ("<tds:GetDNSResponse><tds:DNSInformation><tt:FromDHCP>false</tt:FromDHCP>"
                f"{dns}</tds:DNSInformation></tds:GetDNSResponse>")

    @staticmethod
    def _ips(req, name):
        out = []
        for e in children(req, name):
            v = text(e, "IPv4Address") or text(e, "IPv6Address") or text(e, "DNSname")
            if v:
                out.append(v)
        return out

    def SetDNS(self, ctx, req):
        with ctx.camera.lock:
            ctx.camera.dns = self._ips(req, "DNSManual")
            ctx.camera.save()
        return "<tds:SetDNSResponse/>"

    def GetNTP(self, ctx, req):
        ntp = []
        for n in ctx.camera.ntp:
            if all(p.isdigit() for p in n.split(".")) and n.count(".") == 3:
                ntp.append(f"<tt:NTPManual><tt:Type>IPv4</tt:Type><tt:IPv4Address>{esc(n)}"
                           "</tt:IPv4Address></tt:NTPManual>")
            else:
                ntp.append(f"<tt:NTPManual><tt:Type>DNS</tt:Type><tt:DNSname>{esc(n)}</tt:DNSname>"
                           "</tt:NTPManual>")
        return ("<tds:GetNTPResponse><tds:NTPInformation><tt:FromDHCP>false</tt:FromDHCP>"
                f"{''.join(ntp)}</tds:NTPInformation></tds:GetNTPResponse>")

    def SetNTP(self, ctx, req):
        with ctx.camera.lock:
            ctx.camera.ntp = self._ips(req, "NTPManual")
            ctx.camera.save()
        return "<tds:SetNTPResponse/>"

    def GetNetworkInterfaces(self, ctx, req):
        c = ctx.camera
        return ("<tds:GetNetworkInterfacesResponse><tds:NetworkInterfaces token=\"eth0\">"
                "<tt:Enabled>true</tt:Enabled><tt:Info><tt:Name>eth0</tt:Name>"
                f"<tt:HwAddress>{c.mac}</tt:HwAddress><tt:MTU>1500</tt:MTU></tt:Info>"
                "<tt:IPv4><tt:Enabled>true</tt:Enabled><tt:Config><tt:Manual>"
                f"<tt:Address>{ctx.host_ip}</tt:Address><tt:PrefixLength>24</tt:PrefixLength>"
                "</tt:Manual><tt:DHCP>false</tt:DHCP></tt:Config></tt:IPv4>"
                "</tds:NetworkInterfaces></tds:GetNetworkInterfacesResponse>")

    def SetNetworkInterfaces(self, ctx, req):
        tok = text(req, "InterfaceToken")
        if tok and tok != "eth0":
            raise invalid_arg("ter:InvalidNetworkInterface", "The supplied network interface "
                                                             "token does not exist")
        log.info("%s: SetNetworkInterfaces accepted (virtual device keeps host address)",
                 ctx.camera.id)
        return ("<tds:SetNetworkInterfacesResponse><tds:RebootNeeded>false</tds:RebootNeeded>"
                "</tds:SetNetworkInterfacesResponse>")

    def GetNetworkProtocols(self, ctx, req):
        c = ctx.camera
        return ("<tds:GetNetworkProtocolsResponse>"
                f"<tds:NetworkProtocols><tt:Name>HTTP</tt:Name><tt:Enabled>true</tt:Enabled>"
                f"<tt:Port>{c.http_port}</tt:Port></tds:NetworkProtocols>"
                f"<tds:NetworkProtocols><tt:Name>RTSP</tt:Name><tt:Enabled>true</tt:Enabled>"
                f"<tt:Port>{ctx.rtsp_port}</tt:Port></tds:NetworkProtocols>"
                "</tds:GetNetworkProtocolsResponse>")

    def SetNetworkProtocols(self, ctx, req):
        for p in children(req, "NetworkProtocols"):
            name = text(p, "Name")
            if name not in ("HTTP", "HTTPS", "RTSP"):
                raise invalid_arg("ter:ServiceNotSupported", "The supplied network service is "
                                                             "not supported")
            if name == "HTTPS" and to_bool(text(p, "Enabled")):
                raise not_supported("ter:EnablingTlsFailed", "TLS not supported")
        log.info("%s: SetNetworkProtocols accepted (ports are fixed by the simulator)",
                 ctx.camera.id)
        return "<tds:SetNetworkProtocolsResponse/>"

    def GetNetworkDefaultGateway(self, ctx, req):
        gw = "".join(f"<tt:IPv4Address>{esc(g)}</tt:IPv4Address>" for g in ctx.camera.gateway)
        return ("<tds:GetNetworkDefaultGatewayResponse><tds:NetworkGateway>"
                f"{gw}</tds:NetworkGateway></tds:GetNetworkDefaultGatewayResponse>")

    def SetNetworkDefaultGateway(self, ctx, req):
        with ctx.camera.lock:
            ctx.camera.gateway = [e.text.strip() for e in children(req, "IPv4Address") if e.text]
            ctx.camera.save()
        return "<tds:SetNetworkDefaultGatewayResponse/>"

    def GetZeroConfiguration(self, ctx, req):
        return ("<tds:GetZeroConfigurationResponse><tds:ZeroConfiguration>"
                "<tt:InterfaceToken>eth0</tt:InterfaceToken><tt:Enabled>false</tt:Enabled>"
                "</tds:ZeroConfiguration></tds:GetZeroConfigurationResponse>")

    def GetIPAddressFilter(self, ctx, req):
        raise not_supported()

    def GetDynamicDNS(self, ctx, req):
        raise not_supported()

    # ================================================================== users
    def GetUsers(self, ctx, req):
        with ctx.camera.lock:
            users = list(ctx.camera.users.values())
        return "<tds:GetUsersResponse>" + "".join(
            f"<tds:User><tt:Username>{esc(u.username)}</tt:Username>"
            f"<tt:UserLevel>{u.level}</tt:UserLevel></tds:User>" for u in users) + \
            "</tds:GetUsersResponse>"

    def CreateUsers(self, ctx, req):
        c = ctx.camera
        new = []
        for u in children(req, "User"):
            name, pwd, lvl = text(u, "Username", default=""), text(u, "Password", default=""), \
                text(u, "UserLevel", default="User")
            if not name:
                raise invalid_arg("ter:InvalidArgVal", "empty username")
            if lvl not in USER_LEVELS:
                raise invalid_arg("ter:AnonymousNotAllowed", "User level anonymous is not allowed")
            if len(name) > 32:
                raise SoapFault("Sender", "ter:OperationProhibited", "ter:UsernameTooLong",
                                "The username is too long")
            if len(pwd) > 64:
                raise SoapFault("Sender", "ter:OperationProhibited", "ter:PasswordTooLong",
                                "The password is too long")
            new.append(User(name, pwd, lvl))
        with c.lock:
            for u in new:
                if u.username in c.users:
                    raise SoapFault("Sender", "ter:OperationProhibited", "ter:UsernameClash",
                                    "Username already exists")
            if len(c.users) + len(new) > 16:
                raise SoapFault("Receiver", "ter:Action", "ter:TooManyUsers", "Too many users")
            for u in new:
                c.users[u.username] = u
            c.save()
        return "<tds:CreateUsersResponse/>"

    def DeleteUsers(self, ctx, req):
        c = ctx.camera
        names = [e.text.strip() for e in children(req, "Username") if e.text]
        with c.lock:
            for n in names:
                if n not in c.users:
                    raise invalid_arg("ter:UsernameMissing", "Username not recognized")
                if n == ctx.user and len(c.users) == 1:
                    raise SoapFault("Sender", "ter:InvalidArgVal", "ter:FixedUser",
                                    "Cannot delete the last user")
            for n in names:
                del c.users[n]
            c.save()
        return "<tds:DeleteUsersResponse/>"

    def SetUser(self, ctx, req):
        c = ctx.camera
        with c.lock:
            for u in children(req, "User"):
                name = text(u, "Username", default="")
                if name not in c.users:
                    raise invalid_arg("ter:UsernameMissing", "Username not recognized")
                pwd = text(u, "Password")
                lvl = text(u, "UserLevel")
                if lvl and lvl not in USER_LEVELS:
                    raise invalid_arg("ter:AnonymousNotAllowed", "Anonymous not allowed")
                if pwd is not None:
                    c.users[name].password = pwd
                if lvl:
                    c.users[name].level = lvl
            c.save()
        return "<tds:SetUserResponse/>"

    # ================================================================== IO / misc
    def GetRelayOutputs(self, ctx, req):
        return "<tds:GetRelayOutputsResponse/>"

    def SetRelayOutputSettings(self, ctx, req):
        raise invalid_arg("ter:RelayToken", "Unknown relay token")

    def SetRelayOutputState(self, ctx, req):
        raise invalid_arg("ter:RelayToken", "Unknown relay token")

    def __getattr__(self, name):
        # Every remaining operation of the device WSDL that a test client may call:
        # answer per Core 5.1.2 with env:Receiver / ter:ActionNotSupported.
        if name and name[0].isupper():
            def _unsupported(ctx, req, _n=name):
                raise not_supported(None, f"{_n} is not supported by this device")
            return _unsupported
        raise AttributeError(name)


__all__ = ["DeviceService", "localname"]
