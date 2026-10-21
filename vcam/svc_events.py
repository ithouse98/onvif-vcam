"""Event service (tev) - ONVIF Core section 9.

* Real-time pull-point interface: CreatePullPointSubscription / PullMessages / Renew /
  Unsubscribe / SetSynchronizationPoint (9.1).
* WS-BaseNotification push: Subscribe / Renew / Unsubscribe + Notify to the consumer (9.3).
* GetEventProperties with a fixed topic set (9.7), ConcreteSet topic filters (9.6.3).
* Property events: 'Initialized' on subscribe / sync point, then 'Changed' (9.4.2).
* Simulated motion: tns1:VideoSource/MotionAlarm and tns1:RuleEngine/CellMotionDetector/Motion.
"""

import datetime as dt
import logging
import threading
import time
import urllib.request

from .soap import SoapFault, envelope, NS
from .util import esc, text, child, utcnow, iso, parse_duration, parse_time_or_duration, new_uuid

log = logging.getLogger("vcam.events")

DIALECT_CONCRETE_SET = "http://www.onvif.org/ver10/tev/topicExpression/ConcreteSet"
A_EVENTS = "http://www.onvif.org/ver10/events/wsdl/"
A_WSN = "http://docs.oasis-open.org/wsn/bw-2/"

TOPICS = {
    "tns1:VideoSource/MotionAlarm": {
        "property": True,
        "source": [("Source", "tt:ReferenceToken")],
        "data": [("State", "xs:boolean")],
    },
    "tns1:RuleEngine/CellMotionDetector/Motion": {
        "property": True,
        "source": [("VideoSourceConfigurationToken", "tt:ReferenceToken"),
                   ("VideoAnalyticsConfigurationToken", "tt:ReferenceToken"),
                   ("Rule", "xs:string")],
        "data": [("IsMotion", "xs:boolean")],
    },
    "tns1:Media/ConfigurationChanged": {
        "property": False,
        "source": [("Token", "tt:ReferenceToken")],
        "data": [("Type", "xs:string")],
    },
}


class Message:
    __slots__ = ("topic", "time", "op", "source", "data")

    def __init__(self, topic, op, source, data, when=None):
        self.topic, self.op, self.source, self.data = topic, op, source, data
        self.time = when or utcnow()

    def xml(self, sub_address=None):
        src = "".join(f'<tt:SimpleItem Name="{esc(n)}" Value="{esc(v)}"/>' for n, v in self.source)
        dat = "".join(f'<tt:SimpleItem Name="{esc(n)}" Value="{esc(v)}"/>' for n, v in self.data)
        op = f' PropertyOperation="{self.op}"' if self.op else ""
        ref = (f"<wsnt:SubscriptionReference><wsa5:Address>{esc(sub_address)}</wsa5:Address>"
               "</wsnt:SubscriptionReference>") if sub_address else ""
        return (f"<wsnt:NotificationMessage>{ref}"
                f'<wsnt:Topic Dialect="{DIALECT_CONCRETE_SET}">{self.topic}</wsnt:Topic>'
                f'<wsnt:Message><tt:Message UtcTime="{iso(self.time)}"{op}>'
                f"<tt:Source>{src}</tt:Source><tt:Data>{dat}</tt:Data></tt:Message></wsnt:Message>"
                "</wsnt:NotificationMessage>")


def _norm(path):
    """'tns1:RuleEngine/x:CellMotionDetector' -> ['RuleEngine','CellMotionDetector']"""
    return [seg.split(":", 1)[-1] for seg in path.strip().split("/") if seg]


def topic_matches(expr, topic):
    if not expr:
        return True
    t = _norm(topic)
    for alt in expr.split("|"):
        alt = alt.strip()
        if not alt:
            continue
        descendants = alt.endswith("//.") or alt.endswith("//*")
        a = _norm(alt[:-3] if descendants else alt.rstrip("/"))
        if descendants:
            if len(t) >= len(a) and all(x in ("*", y) for x, y in zip(a, t)):
                return True
        elif len(a) == len(t) and all(x in ("*", y) for x, y in zip(a, t)):
            return True
    return False


class Subscription:
    def __init__(self, svc, camera, kind, filt, termination, duration, consumer=None):
        self.id = new_uuid()
        self.svc = svc
        self.camera = camera
        self.kind = kind  # pull | push
        self.filter = filt
        self.termination = termination
        self.duration = duration
        self.consumer = consumer
        self.queue = []
        self.cond = threading.Condition()
        self.created = time.time()
        self.address = None

    def push(self, msg):
        if not topic_matches(self.filter, msg.topic):
            return
        with self.cond:
            self.queue.append(msg)
            if len(self.queue) > 1000:
                del self.queue[:len(self.queue) - 1000]
            self.cond.notify_all()

    def take(self, limit, timeout):
        deadline = time.time() + timeout
        with self.cond:
            while not self.queue and time.time() < deadline and not self.svc.stopping:
                self.cond.wait(min(1.0, max(0.0, deadline - time.time())))
            out, self.queue = self.queue[:limit], self.queue[limit:]
            return out


class EventService:
    def __init__(self, app):
        self.app = app
        self.subs = {}
        self.lock = threading.Lock()
        self.state = {}  # camera.id -> {"motion": bool}
        self.stopping = False
        self.max_pull_timeout = 60.0
        threading.Thread(target=self._reaper, name="evt-reaper", daemon=True).start()

    # ================================================================ device state
    def start_camera(self, cam):
        self.state[cam.id] = {"motion": False, "manual_until": 0}
        m = cam.motion
        if m.get("enabled", True):
            threading.Thread(target=self._motion_sim, args=(cam,), name=f"motion-{cam.id}",
                             daemon=True).start()

    def _motion_sim(self, cam):
        on_s = float(cam.motion.get("on_seconds", 10))
        off_s = float(cam.motion.get("off_seconds", 50))
        def stopped(t):
            return cam.stop_evt.wait(t) or self.app.stop_evt.is_set()
        # stagger cameras so a VMS doesn't receive all events at once
        if stopped(off_s * ((cam.index * 0.37) % 1.0)):
            return
        while True:
            if time.time() > self.state[cam.id]["manual_until"]:
                self.set_motion(cam, True)
            if stopped(on_s):
                return
            if time.time() > self.state[cam.id]["manual_until"]:
                self.set_motion(cam, False)
            if stopped(off_s):
                return

    def drop_camera(self, cam):
        with self.lock:
            for k in [k for k, s in self.subs.items() if s.camera is cam]:
                sub = self.subs.pop(k)
                with sub.cond:
                    sub.cond.notify_all()

    def _motion_msgs(self, cam, op):
        st = self.state.get(cam.id, {}).get("motion", False)
        v = "true" if st else "false"
        return [
            Message("tns1:VideoSource/MotionAlarm", op, [("Source", cam.video_source_token)],
                    [("State", v)]),
            Message("tns1:RuleEngine/CellMotionDetector/Motion", op,
                    [("VideoSourceConfigurationToken", cam.vsc_token),
                     ("VideoAnalyticsConfigurationToken", "VideoAnalyticsToken"),
                     ("Rule", "MyMotionDetectorRule")], [("IsMotion", v)]),
        ]

    def set_motion(self, cam, state, manual=False):
        st = self.state.setdefault(cam.id, {"motion": False, "manual_until": 0})
        if manual:
            st["manual_until"] = time.time() + 30
        if st["motion"] == state:
            return
        st["motion"] = state
        log.info("%s: motion %s%s", cam.id, "START" if state else "stop",
                 " (manual)" if manual else "")
        for m in self._motion_msgs(cam, "Changed"):
            self.publish(cam, m)

    def config_changed(self, cam, typ, token):
        self.publish(cam, Message("tns1:Media/ConfigurationChanged", None, [("Token", token)],
                                  [("Type", typ)]))

    def publish(self, cam, msg):
        with self.lock:
            subs = [s for s in self.subs.values() if s.camera is cam]
        for s in subs:
            s.push(msg)

    # ================================================================ SOAP helpers
    def caps_xml(self):
        return ('<tev:Capabilities WSSubscriptionPolicySupport="true" WSPullPointSupport="true" '
                'WSPausableSubscriptionManagerInterfaceSupport="false" '
                'MaxNotificationProducers="10" MaxPullPoints="32" '
                'PersistentNotificationStorage="false"/>')

    def _sub_from_ctx(self, ctx):
        sid = ctx.path.rstrip("/").rsplit("/", 1)[-1] if "/events/" in ctx.path else None
        if (not sid or sid not in self.subs) and ctx.header is not None:
            to = text(ctx.header, "To") or ""
            sid = to.rstrip("/").rsplit("/", 1)[-1]
        with self.lock:
            sub = self.subs.get(sid or "")
        if sub is None or sub.camera is not ctx.camera:
            raise SoapFault("Sender", "ter:InvalidArgVal", "ter:ResourceUnknown",
                            "Subscription does not exist or has expired")
        return sub

    def _relates(self, ctx, action):
        ctx.action = action
        if ctx.header is not None:
            ctx.relates_to = text(ctx.header, "MessageID")

    def _filter(self, req):
        f = child(req, "Filter")
        if f is None:
            return None
        exprs = [e.text.strip() for e in f if e.text and "TopicExpression" in e.tag]
        return "|".join(exprs) or None

    def _new_sub(self, ctx, kind, req, term_tag, consumer=None):
        now = utcnow()
        raw = text(req, term_tag)
        term = parse_time_or_duration(raw, now) if raw else None
        duration = None
        if raw and raw.strip().startswith("P"):
            duration = parse_duration(raw)
        if term is None:
            duration = duration or 60.0
            term = now + dt.timedelta(seconds=duration)
        if term <= now:
            raise SoapFault("Sender", "ter:InvalidArgVal", "wsnt:UnacceptableInitialTerminationTime",
                            "Termination time in the past")
        with self.lock:
            n = sum(1 for s in self.subs.values() if s.camera is ctx.camera)
            if n >= 64:
                raise SoapFault("Receiver", "ter:Action", "ter:TooManySubscriptions",
                                "Too many subscriptions")
        sub = Subscription(self, ctx.camera, kind, self._filter(req), term, duration or 60.0,
                           consumer)
        path = "pullpoint" if kind == "pull" else "subscription"
        sub.address = f"http://{ctx.host_ip}:{ctx.camera.http_port}/onvif/events/{path}/{sub.id}"
        with self.lock:
            self.subs[sub.id] = sub
        for m in self._motion_msgs(ctx.camera, "Initialized"):
            sub.push(m)
        log.info("%s: new %s subscription %s from %s filter=%s until %s", ctx.camera.id, kind,
                 sub.id[:8], ctx.handler.client_address[0], sub.filter, iso(term))
        if kind == "push":
            threading.Thread(target=self._pusher, args=(sub,), daemon=True).start()
        return sub, now

    # ================================================================ operations
    def GetServiceCapabilities(self, ctx, req):
        return f"<tev:GetServiceCapabilitiesResponse>{self.caps_xml()}" \
               "</tev:GetServiceCapabilitiesResponse>"

    def GetEventProperties(self, ctx, req):
        self._relates(ctx, A_EVENTS + "EventPortType/GetEventPropertiesResponse")

        def items(lst):
            return "".join(f'<tt:SimpleItemDescription Name="{n}" Type="{t}"/>' for n, t in lst)

        def desc(t):
            return (f'<tt:MessageDescription IsProperty="{"true" if t["property"] else "false"}">'
                    f"<tt:Source>{items(t['source'])}</tt:Source>"
                    f"<tt:Data>{items(t['data'])}</tt:Data></tt:MessageDescription>")

        # build the nested topic tree
        tree = {}
        for path, t in TOPICS.items():
            node = tree
            segs = path.split(":", 1)[1].split("/")
            for s in segs[:-1]:
                node = node.setdefault(s, {})
            node[segs[-1]] = t

        def render(name, node, root):
            tag = f"tns1:{name}" if root else name
            if "property" in node:
                return f'<{tag} wstop:topic="true">{desc(node)}</{tag}>'
            return f"<{tag}>" + "".join(render(k, v, False) for k, v in node.items()) + f"</{tag}>"

        topicset = "".join(render(k, v, True) for k, v in tree.items())
        return ("<tev:GetEventPropertiesResponse>"
                "<tev:TopicNamespaceLocation>http://www.onvif.org/onvif/ver10/topics/topicns.xml"
                "</tev:TopicNamespaceLocation><wsnt:FixedTopicSet>true</wsnt:FixedTopicSet>"
                f"<wstop:TopicSet>{topicset}</wstop:TopicSet>"
                f"<wsnt:TopicExpressionDialect>{DIALECT_CONCRETE_SET}</wsnt:TopicExpressionDialect>"
                "<wsnt:TopicExpressionDialect>http://docs.oasis-open.org/wsn/t-1/TopicExpression/"
                "Concrete</wsnt:TopicExpressionDialect>"
                "<tev:MessageContentFilterDialect>http://www.onvif.org/ver10/tev/"
                "messageContentFilter/ItemFilter</tev:MessageContentFilterDialect>"
                "<tev:MessageContentSchemaLocation>http://www.onvif.org/onvif/ver10/schema/onvif.xsd"
                "</tev:MessageContentSchemaLocation></tev:GetEventPropertiesResponse>")

    def CreatePullPointSubscription(self, ctx, req):
        self._relates(ctx, A_EVENTS + "EventPortType/CreatePullPointSubscriptionResponse")
        sub, now = self._new_sub(ctx, "pull", req, "InitialTerminationTime")
        return ("<tev:CreatePullPointSubscriptionResponse><tev:SubscriptionReference>"
                f"<wsa5:Address>{esc(sub.address)}</wsa5:Address></tev:SubscriptionReference>"
                f"<wsnt:CurrentTime>{iso(now)}</wsnt:CurrentTime>"
                f"<wsnt:TerminationTime>{iso(sub.termination)}</wsnt:TerminationTime>"
                "</tev:CreatePullPointSubscriptionResponse>")

    def PullMessages(self, ctx, req):
        self._relates(ctx, A_EVENTS + "PullPointSubscription/PullMessagesResponse")
        sub = self._sub_from_ctx(ctx)
        timeout = parse_duration(text(req, "Timeout"), 10.0)
        limit = max(1, int(text(req, "MessageLimit", default="100") or 100))
        if timeout > self.max_pull_timeout:
            timeout = self.max_pull_timeout
        msgs = sub.take(limit, max(0.0, timeout))
        now = utcnow()
        # keep pull points alive while the client keeps pulling
        sub.termination = max(sub.termination, now + dt.timedelta(seconds=sub.duration))
        return ("<tev:PullMessagesResponse>"
                f"<tev:CurrentTime>{iso(now)}</tev:CurrentTime>"
                f"<tev:TerminationTime>{iso(sub.termination)}</tev:TerminationTime>"
                + "".join(m.xml() for m in msgs) + "</tev:PullMessagesResponse>")

    def Seek(self, ctx, req):
        raise SoapFault("Receiver", "ter:ActionNotSupported", None, "Seek not supported")

    def SetSynchronizationPoint(self, ctx, req):
        self._relates(ctx, A_EVENTS + "PullPointSubscription/SetSynchronizationPointResponse")
        try:
            subs = [self._sub_from_ctx(ctx)]
        except SoapFault:
            with self.lock:
                subs = [s for s in self.subs.values() if s.camera is ctx.camera]
        for s in subs:
            for m in self._motion_msgs(ctx.camera, "Initialized"):
                s.push(m)
        return "<tev:SetSynchronizationPointResponse/>"

    def Renew(self, ctx, req):
        self._relates(ctx, A_WSN + "SubscriptionManager/RenewResponse")
        sub = self._sub_from_ctx(ctx)
        now = utcnow()
        raw = text(req, "TerminationTime")
        term = parse_time_or_duration(raw, now) if raw else now + dt.timedelta(seconds=sub.duration)
        if term is None or term <= now:
            raise SoapFault("Sender", "ter:InvalidArgVal", "wsnt:UnacceptableTerminationTime",
                            "Unacceptable termination time")
        if raw and raw.strip().startswith("P"):
            sub.duration = parse_duration(raw, sub.duration)
        sub.termination = term
        log.info("%s: subscription %s renewed by %s until %s", ctx.camera.id, sub.id[:8],
                 ctx.handler.client_address[0], iso(term))
        return (f"<wsnt:RenewResponse><wsnt:TerminationTime>{iso(term)}</wsnt:TerminationTime>"
                f"<wsnt:CurrentTime>{iso(now)}</wsnt:CurrentTime></wsnt:RenewResponse>")

    def Unsubscribe(self, ctx, req):
        self._relates(ctx, A_WSN + "SubscriptionManager/UnsubscribeResponse")
        sub = self._sub_from_ctx(ctx)
        with self.lock:
            self.subs.pop(sub.id, None)
        with sub.cond:
            sub.cond.notify_all()
        log.info("%s: subscription %s unsubscribed by %s after %ds", ctx.camera.id, sub.id[:8],
                 ctx.handler.client_address[0], time.time() - sub.created)
        return "<wsnt:UnsubscribeResponse/>"

    def Subscribe(self, ctx, req):
        self._relates(ctx, A_WSN + "NotificationProducer/SubscribeResponse")
        consumer = text(req, "ConsumerReference", "Address")
        if not consumer or not consumer.startswith(("http://", "https://")):
            raise SoapFault("Sender", "ter:InvalidArgVal", "wsnt:SubscribeCreationFailedFault",
                            "ConsumerReference address missing or invalid")
        sub, now = self._new_sub(ctx, "push", req, "InitialTerminationTime", consumer)
        return ("<wsnt:SubscribeResponse><wsnt:SubscriptionReference>"
                f"<wsa5:Address>{esc(sub.address)}</wsa5:Address></wsnt:SubscriptionReference>"
                f"<wsnt:CurrentTime>{iso(now)}</wsnt:CurrentTime>"
                f"<wsnt:TerminationTime>{iso(sub.termination)}</wsnt:TerminationTime>"
                "</wsnt:SubscribeResponse>")

    # ================================================================ push + lifecycle
    def _pusher(self, sub):
        fails = 0
        while not self.app.stop_evt.is_set():
            with self.lock:
                if sub.id not in self.subs:
                    return
            msgs = sub.take(100, 2.0)
            if not msgs:
                continue
            body = "<wsnt:Notify>" + "".join(m.xml(sub.address) for m in msgs) + "</wsnt:Notify>"
            payload = envelope(body, action=A_WSN + "NotificationConsumer/Notify")
            payload = payload.replace(
                b"</wsa5:Action>", f"</wsa5:Action><wsa5:To>{esc(sub.consumer)}</wsa5:To>".encode(), 1)
            try:
                r = urllib.request.Request(sub.consumer, data=payload, method="POST", headers={
                    "Content-Type": "application/soap+xml; charset=utf-8"})
                urllib.request.urlopen(r, timeout=5).read()
                fails = 0
            except Exception as exc:
                fails += 1
                log.warning("%s: Notify to %s failed (%s)", sub.camera.id, sub.consumer, exc)
                if fails >= 10:
                    with self.lock:
                        self.subs.pop(sub.id, None)
                    return

    def _reaper(self):
        while not self.app.stop_evt.wait(5):
            now = utcnow()
            with self.lock:
                dead = [k for k, s in self.subs.items() if s.termination < now]
                for k in dead:
                    s = self.subs.pop(k)
                    log.info("%s: subscription %s expired", s.camera.id, k[:8])

    def count(self, cam):
        with self.lock:
            return sum(1 for s in self.subs.values() if s.camera is cam)

    def shutdown(self):
        self.stopping = True
        with self.lock:
            subs = list(self.subs.values())
        for s in subs:
            with s.cond:
                s.cond.notify_all()


__all__ = ["EventService", "topic_matches", "NS"]
