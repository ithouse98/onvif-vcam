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
 * VCam screen-agent protocol (same as vcam/agent.py) for a phone: registers with the VCam agent hub
 * and streams a camera when the server asks. The phone's cameras are reported as the agent's
 * "screens" (0 = back, 1 = front), so an unchanged VCam 1.3.x server can use them.
 */
class AgentService : LifecycleService() {
    companion object {
        const val VERSION = "1.3.1-android"
        private const val CHANNEL = "vcam-agent"
        private const val NOTIF_ID = 1
        private const val TAG = "VCamAgent"
        @Volatile var status: String = ""
        @Volatile var running = false

        fun start(ctx: Context) {
            Prefs(ctx).enabled = true
            androidx.core.content.ContextCompat.startForegroundService(ctx, Intent(ctx, AgentService::class.java))
        }

        fun stop(ctx: Context) {
            Prefs(ctx).enabled = false
            ctx.stopService(Intent(ctx, AgentService::class.java))
        }

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
    private var streamer: CameraStreamer? = null
    private var streamToken: String? = null
    private var wakeLock: PowerManager.WakeLock? = null
    private var wifiLock: WifiManager.WifiLock? = null
    private lateinit var prefs: Prefs

    override fun onCreate() {
        super.onCreate()
        prefs = Prefs(this)
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(NotificationChannel(CHANNEL, getString(R.string.app_name), NotificationManager.IMPORTANCE_LOW))
        val type = if (Build.VERSION.SDK_INT >= 30) ServiceInfo.FOREGROUND_SERVICE_TYPE_CAMERA else 0
        ServiceCompat.startForeground(this, NOTIF_ID, notification(getString(R.string.st_starting)), type)
        running = true
        wakeLock = (getSystemService(POWER_SERVICE) as PowerManager)
            .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "vcam:agent").apply { acquire() }
        wifiLock = (applicationContext.getSystemService(WIFI_SERVICE) as WifiManager)
            .createWifiLock(WifiManager.WIFI_MODE_FULL_HIGH_PERF, "vcam:agent").apply { acquire() }
        Thread({ loop() }, "vcam-agent").start()
    }

    override fun onDestroy() {
        stopping = true
        running = false
        streamer?.stop()
        try { socket?.close() } catch (_: Exception) {}
        wakeLock?.let { if (it.isHeld) it.release() }
        wifiLock?.let { if (it.isHeld) it.release() }
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

    /** Cameras of this phone in "screen" index order. */
    private fun cameras(): List<Pair<Int, String>> {
        val pm = packageManager
        val list = mutableListOf<Pair<Int, String>>()
        if (pm.hasSystemFeature(PackageManager.FEATURE_CAMERA)) list += CameraSelector.LENS_FACING_BACK to getString(R.string.cam_back)
        if (pm.hasSystemFeature(PackageManager.FEATURE_CAMERA_FRONT)) list += CameraSelector.LENS_FACING_FRONT to getString(R.string.cam_front)
        if (list.isEmpty()) list += CameraSelector.LENS_FACING_BACK to getString(R.string.cam_back)
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
        // broadcast discovery, same as the PC agent
        DatagramSocket().use { u ->
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
        val cams = cameras()
        val screens = JSONArray()
        cams.forEachIndexed { i, c ->
            screens.put(JSONObject().put("index", i).put("name", c.second).put("width", prefs.width)
                .put("height", prefs.height).put("x", 0).put("y", 0).put("primary", i == 0))
        }
        val key = prefs.key
        send(JSONObject().put("type", "hello").put("id", prefs.id).put("name", prefs.name)
            .put("host", Build.MODEL).put("os", "Android ${Build.VERSION.RELEASE} (${Build.MANUFACTURER} ${Build.MODEL})")
            .put("version", VERSION).put("screens", screens)
            .put("auth", sign(key, challenge.optString("nonce"))))
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
                    "start" -> startStream(msg, host, port, cams)
                    "stop" -> if (msg.optString("stream") == streamToken) streamer?.stop()
                }
            }
        } finally {
            streamer?.stop()
            try { s.close() } catch (_: Exception) {}
        }
        if (!stopping) setStatus(getString(R.string.st_lost))
    }

    private fun startStream(msg: JSONObject, host: String, port: Int, cams: List<Pair<Int, String>>) {
        val tok = msg.optString("stream")
        if (streamer != null) {  // one camera at a time on a phone
            send(JSONObject().put("type", "stream_error").put("stream", tok)
                .put("msg", "the phone is already streaming a camera (one camera at a time)"))
            return
        }
        val idx = msg.optString("screen").toIntOrNull() ?: 0     // "", "all", "0+1" -> first camera
        val cam = cams.getOrElse(idx) { cams[0] }
        val fps = msg.optInt("fps", prefs.fps).coerceIn(1, 60)
        val br = minOf(msg.optInt("bitrate", prefs.bitrateKbps), prefs.bitrateKbps)
        setStatus(getString(R.string.st_streaming, cam.second))
        streamToken = tok
        streamer = CameraStreamer(this, cam.first, prefs.width, prefs.height, fps, br, host, port, tok,
            sign(prefs.key, tok)) { reason ->
            streamer = null
            streamToken = null
            if (reason != null && !stopping) {
                try {
                    send(JSONObject().put("type", "stream_error").put("stream", tok).put("msg", reason))
                } catch (_: Exception) {}
            }
            if (!stopping && socket?.isClosed == false) setStatus(getString(R.string.st_registered, "$host:$port"))
        }
        streamer!!.start()
    }
}
