"""End-to-end check of a running vcam with an independent ONVIF client (onvif-zeep-async).

    pip install onvif-zeep-async
    python tests/test_with_onvif_client.py 127.0.0.1 8080 admin admin

Every call goes through zeep, i.e. requests are built from and responses parsed against the
official ONVIF WSDL/XSD files, so schema mistakes in the device surface as failures here.
"""

import asyncio
import datetime as dt
import sys

from onvif import ONVIFCamera

PASS, FAIL = [], []


async def check(name, coro):
    try:
        res = await coro
        PASS.append(name)
        print(f"  PASS  {name}")
        return res
    except Exception as exc:
        FAIL.append((name, exc))
        print(f"  FAIL  {name}: {type(exc).__name__}: {exc}")
        return None


async def main(host, port, user, pwd):
    cam = ONVIFCamera(host, port, user, pwd)
    await cam.update_xaddrs()
    print("xaddrs:", {k.split('/')[-2]: v for k, v in cam.xaddrs.items()})
    dev = await cam.create_devicemgmt_service()

    print("\n[Device service - Profile S 7.1-7.6]")
    await check("GetSystemDateAndTime", dev.GetSystemDateAndTime())
    await check("GetCapabilities", dev.GetCapabilities({"Category": "All"}))
    svcs = await check("GetServices(IncludeCapability)", dev.GetServices({"IncludeCapability": True}))
    if svcs:
        print("        services:", [s.Namespace.split("/")[-2] for s in svcs])
    await check("GetServiceCapabilities", dev.GetServiceCapabilities())
    await check("GetWsdlUrl", dev.GetWsdlUrl())
    info = await check("GetDeviceInformation", dev.GetDeviceInformation())
    if info:
        print(f"        {info.Manufacturer} / {info.Model} / fw {info.FirmwareVersion} / sn {info.SerialNumber}")
    await check("GetScopes", dev.GetScopes())
    await check("AddScopes", dev.AddScopes({"ScopeItem": ["onvif://www.onvif.org/location/test-bench"]}))
    await check("RemoveScopes", dev.RemoveScopes({"ScopeItem": ["onvif://www.onvif.org/location/test-bench"]}))
    await check("GetDiscoveryMode", dev.GetDiscoveryMode())
    await check("SetDiscoveryMode", dev.SetDiscoveryMode({"DiscoveryMode": "Discoverable"}))
    await check("GetHostname", dev.GetHostname())
    await check("SetHostname", dev.SetHostname({"Name": "vcam-test"}))
    await check("GetDNS", dev.GetDNS())
    await check("GetNTP", dev.GetNTP())
    await check("GetNetworkInterfaces", dev.GetNetworkInterfaces())
    await check("GetNetworkProtocols", dev.GetNetworkProtocols())
    await check("GetNetworkDefaultGateway", dev.GetNetworkDefaultGateway())
    await check("GetUsers", dev.GetUsers())
    await check("CreateUsers", dev.CreateUsers({"User": [{"Username": "viewer", "Password": "viewer1",
                                                          "UserLevel": "User"}]}))
    await check("SetUser", dev.SetUser({"User": [{"Username": "viewer", "Password": "viewer2",
                                                  "UserLevel": "User"}]}))
    await check("DeleteUsers", dev.DeleteUsers({"Username": ["viewer"]}))
    await check("GetRelayOutputs", dev.GetRelayOutputs())

    print("\n[Media service ver10 - Profile S 7.8-7.13]")
    media = await cam.create_media_service()
    profiles = await check("GetProfiles", media.GetProfiles())
    for p in profiles or []:
        vec = p.VideoEncoderConfiguration
        print(f"        {p.token:<10} {p.Name:<11} {vec.Encoding} {vec.Resolution.Width}x"
              f"{vec.Resolution.Height} @{vec.RateControl.FrameRateLimit} {vec.RateControl.BitrateLimit}kbps")
    tok = profiles[0].token
    await check("GetProfile", media.GetProfile({"ProfileToken": tok}))
    for proto in ("UDP", "TCP", "RTSP", "HTTP"):
        r = await check(f"GetStreamUri RTP-Unicast/{proto}", media.GetStreamUri(
            {"StreamSetup": {"Stream": "RTP-Unicast", "Transport": {"Protocol": proto}},
             "ProfileToken": tok}))
    print("        ", r.Uri if r else "")
    await check("GetStreamUri RTP-Multicast/UDP", media.GetStreamUri(
        {"StreamSetup": {"Stream": "RTP-Multicast", "Transport": {"Protocol": "UDP"}},
         "ProfileToken": tok}))
    await check("GetSnapshotUri", media.GetSnapshotUri({"ProfileToken": tok}))
    await check("GetVideoSources", media.GetVideoSources())
    await check("GetVideoSourceConfigurations", media.GetVideoSourceConfigurations())
    await check("GetVideoSourceConfiguration", media.GetVideoSourceConfiguration({"ConfigurationToken": "VSC_1"}))
    await check("GetVideoSourceConfigurationOptions", media.GetVideoSourceConfigurationOptions({}))
    await check("GetCompatibleVideoSourceConfigurations",
                media.GetCompatibleVideoSourceConfigurations({"ProfileToken": tok}))
    vecs = await check("GetVideoEncoderConfigurations", media.GetVideoEncoderConfigurations())
    await check("GetVideoEncoderConfiguration",
                media.GetVideoEncoderConfiguration({"ConfigurationToken": "VEC_2"}))
    opts = await check("GetVideoEncoderConfigurationOptions",
                       media.GetVideoEncoderConfigurationOptions({"ConfigurationToken": "VEC_2"}))
    if opts:
        print("        JPEG option declared:", opts.JPEG is not None, "| H264 profiles:",
              opts.H264.H264ProfilesSupported)
    await check("GetCompatibleVideoEncoderConfigurations",
                media.GetCompatibleVideoEncoderConfigurations({"ProfileToken": tok}))
    await check("GetGuaranteedNumberOfVideoEncoderInstances",
                media.GetGuaranteedNumberOfVideoEncoderInstances({"ConfigurationToken": "VSC_1"}))
    if vecs:
        sub = [v for v in vecs if v.token == "VEC_2"][0]
        sub.RateControl.FrameRateLimit = 10
        sub.RateControl.BitrateLimit = 400
        await check("SetVideoEncoderConfiguration (sub -> 10fps 400kbps)",
                    media.SetVideoEncoderConfiguration({"Configuration": sub, "ForcePersistence": True}))
        r = await media.GetVideoEncoderConfiguration({"ConfigurationToken": "VEC_2"})
        print(f"        read back: {r.RateControl.FrameRateLimit} fps, {r.RateControl.BitrateLimit} kbps")
    await check("CreateProfile", media.CreateProfile({"Name": "VmsProfile", "Token": "VmsProfile"}))
    await check("AddVideoSourceConfiguration", media.AddVideoSourceConfiguration(
        {"ProfileToken": "VmsProfile", "ConfigurationToken": "VSC_1"}))
    await check("AddVideoEncoderConfiguration", media.AddVideoEncoderConfiguration(
        {"ProfileToken": "VmsProfile", "ConfigurationToken": "VEC_2"}))
    await check("AddMetadataConfiguration", media.AddMetadataConfiguration(
        {"ProfileToken": "VmsProfile", "ConfigurationToken": "MDC_1"}))
    await check("GetMetadataConfigurations", media.GetMetadataConfigurations())
    await check("GetMetadataConfigurationOptions", media.GetMetadataConfigurationOptions({}))
    await check("RemoveMetadataConfiguration", media.RemoveMetadataConfiguration({"ProfileToken": "VmsProfile"}))
    await check("DeleteProfile", media.DeleteProfile({"ProfileToken": "VmsProfile"}))
    await check("SetSynchronizationPoint", media.SetSynchronizationPoint({"ProfileToken": tok}))
    await check("GetAudioSources (empty)", media.GetAudioSources())

    print("\n[Event service - Profile S 7.7]")
    events = await cam.create_events_service()
    await check("GetEventProperties", events.GetEventProperties())
    await check("Events GetServiceCapabilities", events.GetServiceCapabilities())
    def lost():
        print("        subscription lost!")
    mgr = await check("CreatePullPointSubscription (PullPointManager)",
                      cam.create_pullpoint_manager(dt.timedelta(seconds=60), lost))
    if mgr:
        pp = mgr.get_service()
        msgs = await check("PullMessages", pp.PullMessages({"MessageLimit": 10, "Timeout": "PT2S"}))
        if msgs:
            for m in msgs.NotificationMessage:
                msg = m.Message._value_1
                data = {i.Name: i.Value for i in msg.Data.SimpleItem}
                print(f"        {m.Topic._value_1:<44} {msg.PropertyOperation:<12} {data}")
        await check("SetSynchronizationPoint (events)", mgr.set_synchronization_point())
        await check("Renew", mgr._call_subscription_renew())
        await check("Unsubscribe", mgr.shutdown())
    await cam.close()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    a = sys.argv[1:] + [None] * 4
    sys.exit(asyncio.run(main(a[0] or "127.0.0.1", int(a[1] or 8080), a[2] or "admin", a[3] or "admin")))
