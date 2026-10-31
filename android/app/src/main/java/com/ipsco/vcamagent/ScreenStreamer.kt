package com.ipsco.vcamagent

import android.app.Service
import android.content.Context
import android.content.Intent
import android.hardware.display.DisplayManager
import android.hardware.display.VirtualDisplay
import android.media.projection.MediaProjection
import android.media.projection.MediaProjectionManager
import android.os.Handler
import android.os.Looper
import android.util.DisplayMetrics
import android.util.Log
import android.view.Surface

/**
 * Phone screen via MediaProjection. Android 14+ allows one virtual display per user consent, so the
 * display is created once and only its target surface changes between streams.
 */
object ScreenCapture {
    @Volatile private var projection: MediaProjection? = null
    private var display: VirtualDisplay? = null
    @Volatile var onEnded: (() -> Unit)? = null

    val active: Boolean get() = projection != null

    fun begin(service: Service, code: Int, data: Intent) {
        end()
        val mpm = service.getSystemService(MediaProjectionManager::class.java)
        val mp = mpm.getMediaProjection(code, data) ?: throw IllegalStateException("screen capture refused")
        mp.registerCallback(object : MediaProjection.Callback() {
            override fun onStop() {
                Log.i("VCamAgent", "screen capture stopped by the system / user")
                display?.release()
                display = null
                projection = null
                onEnded?.invoke()
            }
        }, Handler(Looper.getMainLooper()))
        projection = mp
    }

    /** (width, height, dpi) to stream: the screen's aspect, long side = 16:9 width of the chosen height. */
    fun size(ctx: Context, prefs: Prefs): Triple<Int, Int, Int> {
        // DisplayManager works from a Service context (WindowManager is for visual contexts only)
        val d = ctx.getSystemService(DisplayManager::class.java).getDisplay(android.view.Display.DEFAULT_DISPLAY)
        val m = DisplayMetrics()
        @Suppress("DEPRECATION")
        d.getRealMetrics(m)
        val sw = m.widthPixels
        val sh = m.heightPixels
        // always a 16:9 frame (what VMS profiles expect): the mirror keeps the screen's aspect and
        // pillarboxes a portrait screen instead of the server stretching it; rotation needs no resize
        val w = maxOf(320, minOf(prefs.width, maxOf(sw, sh)) / 16 * 16)
        return Triple(w, w * 9 / 16 / 16 * 16, m.densityDpi)
    }

    fun attach(surface: Surface, w: Int, h: Int, dpi: Int) {
        val mp = projection ?: throw IllegalStateException("screen capture is not allowed")
        val d = display
        if (d == null) {
            display = mp.createVirtualDisplay("VCam", w, h, dpi, DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR,
                surface, null, null)
        } else {
            d.resize(w, h, dpi)
            d.surface = surface
        }
    }

    fun detach() {
        display?.surface = null
    }

    fun end() {
        val mp = projection
        projection = null
        display?.release()
        display = null
        try { mp?.stop() } catch (_: Exception) {}
    }
}

/** Phone screen feeding the encoder surface. */
class ScreenStreamer(
    ctx: Context, private val w: Int, private val h: Int, private val dpi: Int,
    fps: Int, bitrateKbps: Int, host: String, port: Int, token: String, auth: String, onEnd: (String?) -> Unit,
) : StreamBase(ctx, fps, bitrateKbps, host, port, token, auth, onEnd) {
    override fun onConnected() {
        ScreenCapture.attach(startEncoder(w, h), w, h, dpi)
    }

    override fun onRelease() {
        ScreenCapture.detach()
    }
}
