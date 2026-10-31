package com.ipsco.vcamagent

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.net.wifi.WifiManager
import android.os.Build
import android.os.PowerManager
import android.util.Log
import androidx.camera.core.CameraSelector
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat
import androidx.lifecycle.LifecycleService
import org.json.JSONArray
import org.json.JSONObject
import java.io.BufferedReader
import java.io.InputStreamReader
import java.io.OutputStream
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.Socket
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

/**
 * VCam agent protocol (same as vcam/agent.py) for a phone: registers with the VCam agent hub and
 * streams when the server asks. The phone's sources are reported as the agent's "screens"
 * (back camera, front camera, phone screen), so an unchanged VCam 1.3.x server can use them.
 */
class AgentService : LifecycleService() {
    enum class Kind { BACK, FRONT, SCREEN }
    data class Src(val kind: Kind, val label: String)

    companion object {
        const val VERSION = "1.3.1-android"
        private const val CHANNEL = "vcam-agent"
        private const val NOTIF_ID = 1
        private const val TAG = "VCamAgent"
        private const val ACTION_CONSENT = "consent"
        private const val ACTION_SOURCE = "source"
        @Volatile var status: String = ""
        @Volatile var running = false
        @Volatile var streaming: String? = null      // label of the source being streamed
        @Volatile var instance: AgentService? = null
        @Volatile private var consent: Pair<Int, Intent>? = null

        fun start(ctx: Context) {
            Prefs(ctx).enabled = true
            ContextCompat.startForegroundService(ctx, Intent(ctx, AgentService::class.java))
        }

        fun stop(ctx: Context) {
            Prefs(ctx).enabled = false
            ctx.stopService(Intent(ctx, AgentService::class.java))
        }

        /** Screen-capture permission granted in the activity. */
        fun screenConsent(ctx: Context, code: Int, data: Intent) {
            consent = code to data
            ContextCompat.startForegroundService(ctx, Intent(ctx, AgentService::class.java).setAction(ACTION_CONSENT))
        }

        /** The source was changed in the app: switch the running stream. */
        fun sourceChanged(ctx: Context) {
            if (running) ctx.startService(Intent(ctx, AgentService::class.java).setAction(ACTION_SOURCE))
        }

        val frames: Long get() = instance?.streamer?.frames ?: 0L
        val streamSeconds: Long get() = instance?.streamer?.let { (System.currentTimeMillis() - it.startedAt) / 1000 } ?: 0L

        fun sign(key: String, msg: String): String {
            if (key.isEmpty()) return ""
            val mac = Mac.getInstance("HmacSHA256")
            mac.init(SecretKeySpec(key.toByteArray(), "HmacSHA256"))
            return mac.doFinal(msg.toByteArray()).joinToString("") { "%02x".format(it) }
        }
    }

    @Volatile private var stopping = false
    private var socket: Socket? = null
    private var out: OutputStream? = null
    @Volatile private var streamer: StreamBase? = null
    private var streamToken: String? = null
    private var wakeLock: PowerManager.WakeLock? = null
    private var wifiLock: WifiManager.WifiLock? = null
    private lateinit var prefs: Prefs

    override fun attachBaseContext(base: Context) {
        super.attachBaseContext(Lang.wrap(base))
    }

    override fun onCreate() {
        super.onCreate()
        prefs = Prefs(this)
        instance = this
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(NotificationChannel(CHANNEL, getString(R.string.app_name), NotificationManager.IMPORTANCE_LOW))
        foreground(false)
        running = true
        wakeLock = (getSystemService(POWER_SERVICE) as PowerManager)
            .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "vcam:agent").apply { acquire() }
        wifiLock = (applicationContext.getSystemService(WIFI_SERVICE) as WifiManager)
            .createWifiLock(WifiManager.WIFI_MODE_FULL_HIGH_PERF, "vcam:agent").apply { acquire() }
        ScreenCapture.onEnded = { if (streamer is ScreenStreamer) streamer?.stop("screen capture was stopped on the phone") }
        Thread({ loop() }, "vcam-agent").start()
    }

    private fun foreground(screen: Boolean) {
        var type = if (Build.VERSION.SDK_INT >= 30) ServiceInfo.FOREGROUND_SERVICE_TYPE_CAMERA else 0
        if (screen && Build.VERSION.SDK_INT >= 29) type = type or ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION
        ServiceCompat.startForeground(this, NOTIF_ID, notification(status.ifEmpty { getString(R.string.st_starting) }), type)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        super.onStartCommand(intent, flags, startId)
        when (intent?.action) {
            ACTION_CONSENT -> consent?.let { (code, data) ->
                consent = null
                try {
                    foreground(true)    // must be a mediaProjection service before using the consent
                    ScreenCapture.begin(this, code, data)
                    setStatus(status)
                    if (prefs.source == "screen") streamer?.stop()   // switch now
                } catch (e: Exception) {
                    Log.w(TAG, "screen capture: ${e.message}")
                    setStatus(getString(R.string.screen_denied))
                }
            }
            ACTION_SOURCE -> streamer?.stop()   // the server reconnects at once and gets the new source
        }
        return START_STICKY
    }

    override fun onDestroy() {
        stopping = true
        running = false
        streamer?.stop()
        ScreenCapture.end()
        try { socket?.close() } catch (_: Exception) {}
        wakeLock?.let { if (it.isHeld) it.release() }
        wifiLock?.let { if (it.isHeld) it.release() }
        instance = null
        streaming = null
        setStatus(getString(R.string.st_stopped))
        super.onDestroy()
    }

    private fun notification(text: String): Notification {
        val open = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        return NotificationCompat.Builder(this, CHANNEL)
            .setSmallIcon(R.drawable.ic_stat_camera)
            .setContentTitle(getString(R.string.app_name))
            .setContentText(text)
            .setContentIntent(open)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .build()
    }

    private fun setStatus(s: String) {
        status = s
        Log.i(TAG, s)
        if (running) getSystemService(NotificationManager::class.java).notify(NOTIF_ID, notification(s))
    }

    /** The phone's sources in "screen" index order: back, front (if present), then the screen. */
    private fun sources(): List<Src> {
        val pm = packageManager
        val list = mutableListOf<Src>()
        if (pm.hasSystemFeature(PackageManager.FEATURE_CAMERA)) list += Src(Kind.BACK, getString(R.string.cam_back))
        if (pm.hasSystemFeature(PackageManager.FEATURE_CAMERA_FRONT)) list += Src(Kind.FRONT, getString(R.string.cam_front))
        list += Src(Kind.SCREEN, getString(R.string.cam_screen))
        return list
    }

    private fun loop() {
        var backoff = 2000L
        while (!stopping) {
            try {
                val target = resolveServer()
                if (target == null) {
                    setStatus(getString(R.string.st_searching))
                } else {
                    session(target.first, target.second)
                    backoff = 2000L
                }
            } catch (e: SecurityException) {
                setStatus(getString(R.string.st_denied, e.message))
                backoff = 30000L
            } catch (e: Exception) {
                if (!stopping) setStatus(getString(R.string.st_noserver, e.message ?: e.javaClass.simpleName))
            }
            if (stopping) break
            Thread.sleep(backoff)
            backoff = minOf(backoff * 2, 30000L)
        }
    }

    private fun resolveServer(): Pair<String, Int>? {
        val s = prefs.server
        if (s.isNotEmpty()) {
            val i = s.lastIndexOf(':')
            return if (i > 0 && s.substring(i + 1).toIntOrNull() != null) s.substring(0, i) to s.substring(i + 1).toInt()
            else s to prefs.port
        }
        DatagramSocket().use { u ->     // broadcast discovery, same as the PC agent
            u.broadcast = true
            u.soTimeout = 3000
            val msg = """{"type": "discover"}""".toByteArray()
            u.send(DatagramPacket(msg, msg.size, InetAddress.getByName("255.255.255.255"), prefs.port))
            val buf = ByteArray(2048)
            val p = DatagramPacket(buf, buf.size)
            return try {
                u.receive(p)
                val r = JSONObject(String(p.data, 0, p.length))
                if (r.optString("type") == "vcam") p.address.hostAddress!! to r.optInt("port", prefs.port) else null
            } catch (_: Exception) { null }
        }
    }

    private fun send(msg: JSONObject) {
        synchronized(this) {
            out?.write((msg.toString() + "\n").toByteArray())
            out?.flush()
        }
    }

    private fun session(host: String, port: Int) {
        setStatus(getString(R.string.st_connecting, "$host:$port"))
        val s = Socket()
        s.connect(InetSocketAddress(host, port), 10000)
        socket = s
        out = s.getOutputStream()
        val reader = BufferedReader(InputStreamReader(s.getInputStream()))
        val challenge = JSONObject(reader.readLine() ?: throw java.io.IOException("no challenge"))
        val srcs = sources()
        val screens = JSONArray()
        val (sw, sh, _) = ScreenCapture.size(this, prefs)
        srcs.forEachIndexed { i, c ->
            val (w, h) = if (c.kind == Kind.SCREEN) sw to sh else prefs.width to prefs.height
            screens.put(JSONObject().put("index", i).put("name", c.label).put("width", w).put("height", h)
                .put("x", 0).put("y", 0).put("primary", i == 0))
        }
        send(JSONObject().put("type", "hello").put("id", prefs.id).put("name", prefs.name)
            .put("host", Build.MODEL).put("os", "Android ${Build.VERSION.RELEASE} (${Build.MANUFACTURER} ${Build.MODEL})")
            .put("version", VERSION).put("screens", screens)
            .put("auth", sign(prefs.key, challenge.optString("nonce"))))
        val reply = JSONObject(reader.readLine() ?: throw java.io.IOException("connection closed"))
        if (reply.optString("type") != "welcome") throw SecurityException(reply.optString("reason", "refused"))
        setStatus(getString(R.string.st_registered, "$host:$port"))
        val pinger = Thread({
            try {
                while (!stopping && !s.isClosed) {
                    Thread.sleep(10000)
                    send(JSONObject().put("type", "ping"))
                }
            } catch (_: Exception) {}
        }, "vcam-ping")
        pinger.isDaemon = true
        pinger.start()
        try {
            while (!stopping) {
                val line = reader.readLine() ?: break
                val msg = try { JSONObject(line) } catch (_: Exception) { continue }
                when (msg.optString("type")) {
                    "start" -> startStream(msg, host, port, srcs)
                    "stop" -> if (msg.optString("stream") == streamToken) streamer?.stop()
                }
            }
        } finally {
            streamer?.stop()
            try { s.close() } catch (_: Exception) {}
        }
        if (!stopping) setStatus(getString(R.string.st_lost))
    }

    private fun pick(msg: JSONObject, srcs: List<Src>): Src {
        val want = when (prefs.source) {
            "back" -> Kind.BACK
            "front" -> Kind.FRONT
            "screen" -> Kind.SCREEN
            else -> null
        }
        if (want != null) return srcs.firstOrNull { it.kind == want } ?: srcs[0]
        val idx = msg.optString("screen").toIntOrNull() ?: 0     // "", "all", "0+1" -> first source
        return srcs.getOrElse(idx) { srcs[0] }
    }

    private fun startStream(msg: JSONObject, host: String, port: Int, srcs: List<Src>) {
        val tok = msg.optString("stream")
        if (streamer != null) {  // one source at a time on a phone
            send(JSONObject().put("type", "stream_error").put("stream", tok)
                .put("msg", "the phone is already streaming (one source at a time)"))
            return
        }
        val src = pick(msg, srcs)
        if (src.kind == Kind.SCREEN && !ScreenCapture.active) {
            setStatus(getString(R.string.screen_needed))
            send(JSONObject().put("type", "stream_error").put("stream", tok)
                .put("msg", "screen capture is not allowed on the phone yet: open VCam Agent and allow it"))
            return
        }
        val fps = msg.optInt("fps", prefs.fps).coerceIn(1, 60)
        val br = minOf(msg.optInt("bitrate", prefs.bitrateKbps), prefs.bitrateKbps)
        setStatus(getString(R.string.st_streaming, src.label))
        streaming = src.label
        streamToken = tok
        val auth = sign(prefs.key, tok)
        val done: (String?) -> Unit = { reason ->
            streamer = null
            streamToken = null
            streaming = null
            if (reason != null && !stopping) {
                try {
                    send(JSONObject().put("type", "stream_error").put("stream", tok).put("msg", reason))
                } catch (_: Exception) {}
            }
            if (!stopping && socket?.isClosed == false) setStatus(getString(R.string.st_registered, "$host:$port"))
        }
        val st: StreamBase = if (src.kind == Kind.SCREEN) {
            val (w, h, dpi) = ScreenCapture.size(this, prefs)
            ScreenStreamer(this, w, h, dpi, fps, br, host, port, tok, auth, done)
        } else {
            val facing = if (src.kind == Kind.BACK) CameraSelector.LENS_FACING_BACK else CameraSelector.LENS_FACING_FRONT
            CameraStreamer(this, facing, prefs.width, prefs.height, fps, br, host, port, tok, auth, done)
        }
        streamer = st
        st.start()
    }
}
