package com.ipsco.vcamagent

import android.content.Context
import android.media.MediaCodec
import android.media.MediaCodecInfo
import android.media.MediaFormat
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.util.Log
import android.view.Surface
import org.json.JSONObject
import java.io.BufferedOutputStream
import java.io.BufferedReader
import java.io.InputStreamReader
import java.net.InetSocketAddress
import java.net.Socket
import java.util.concurrent.atomic.AtomicBoolean

/**
 * One agent stream: <source surface> -> hardware H.264 (MediaCodec) -> MPEG-TS -> data connection to
 * the VCam agent hub ({"type": "data", "stream", "auth"} then raw TS). Subclasses feed the encoder's
 * input surface (camera or screen).
 */
abstract class StreamBase(
    protected val ctx: Context,
    protected val fps: Int, private val bitrateKbps: Int,
    private val host: String, private val port: Int, private val token: String, private val auth: String,
    private val onEnd: (String?) -> Unit,
) {
    protected val tag = "VCamStreamer"
    protected val main = Handler(Looper.getMainLooper())
    private val ended = AtomicBoolean(false)
    @Volatile protected var running = true
    private var socket: Socket? = null
    private var mux: TsMuxer? = null
    private var codec: MediaCodec? = null
    @Volatile var frames = 0L
        private set
    val startedAt = System.currentTimeMillis()

    fun start() {
        Thread({
            try {
                val s = Socket()
                s.connect(InetSocketAddress(host, port), 10000)
                s.tcpNoDelay = true
                s.sendBufferSize = 512 * 1024
                BufferedReader(InputStreamReader(s.getInputStream())).readLine()   // challenge
                val out = BufferedOutputStream(s.getOutputStream(), 256 * 1024)
                out.write((JSONObject().put("type", "data").put("stream", token).put("auth", auth)
                    .toString() + "\n").toByteArray())
                out.flush()
                socket = s
                mux = TsMuxer(out)
                main.post {
                    try {
                        if (running) onConnected()
                    } catch (e: Exception) {
                        stop("source error: ${e.message}")
                    }
                }
            } catch (e: Exception) {
                stop("cannot open the data connection: ${e.message}")
            }
        }, "vcam-data").start()
    }

    /** Data connection is up (main thread): open the source and call startEncoder(). */
    protected abstract fun onConnected()

    /** Stop feeding the encoder (main thread). */
    protected abstract fun onRelease()

    /** Configures and starts the encoder, returns its input surface. */
    protected fun startEncoder(w: Int, h: Int): Surface {
        val c = configureEncoder(w, h)
        val surface = c.createInputSurface()
        c.start()
        codec = c
        Log.i(tag, "encoding ${w}x$h @$fps, $bitrateKbps kbps")
        Thread({ drain(c) }, "vcam-encoder").start()
        return surface
    }

    /** Some encoders (e.g. the emulator's software AVC) reject optional keys with ERROR_UNSUPPORTED:
     *  retry with fewer. Repeat-previous-frame keeps a constant rate for a still screen. */
    private fun configureEncoder(w: Int, h: Int): MediaCodec {
        var last: Exception? = null
        for (level in 2 downTo 0) {
            val fmt = MediaFormat.createVideoFormat(MediaFormat.MIMETYPE_VIDEO_AVC, w, h).apply {
                setInteger(MediaFormat.KEY_COLOR_FORMAT, MediaCodecInfo.CodecCapabilities.COLOR_FormatSurface)
                setInteger(MediaFormat.KEY_BIT_RATE, bitrateKbps * 1000)
                setInteger(MediaFormat.KEY_FRAME_RATE, fps)
                setInteger(MediaFormat.KEY_I_FRAME_INTERVAL, 2)
                if (level >= 1) setLong(MediaFormat.KEY_REPEAT_PREVIOUS_FRAME_AFTER, 1_000_000L / fps)
                if (level >= 2 && Build.VERSION.SDK_INT >= 29) setInteger(MediaFormat.KEY_PREPEND_HEADER_TO_SYNC_FRAMES, 1)
            }
            val c = MediaCodec.createEncoderByType(MediaFormat.MIMETYPE_VIDEO_AVC)
            try {
                c.configure(fmt, null, null, MediaCodec.CONFIGURE_FLAG_ENCODE)
                return c
            } catch (e: Exception) {
                last = e
                Log.w(tag, "encoder rejected format (level $level): ${e.message}")
                c.release()
            }
        }
        throw last ?: IllegalStateException("no H.264 encoder")
    }

    private fun drain(c: MediaCodec) {
        val info = MediaCodec.BufferInfo()
        var config: ByteArray? = null
        var t0 = -1L
        var lastSync = 0L
        try {
            while (running) {
                val idx = c.dequeueOutputBuffer(info, 100_000)
                if (idx < 0) continue
                val buf = c.getOutputBuffer(idx)
                if (buf == null) {
                    c.releaseOutputBuffer(idx, false)
                    continue
                }
                val data = ByteArray(info.size)
                buf.position(info.offset)
                buf.get(data)
                c.releaseOutputBuffer(idx, false)
                if (info.flags and MediaCodec.BUFFER_FLAG_CODEC_CONFIG != 0) {
                    config = data
                    continue
                }
                val key = info.flags and MediaCodec.BUFFER_FLAG_KEY_FRAME != 0
                if (t0 < 0) {
                    if (!key) {  // clients need an IDR to start; ask for one
                        if (System.currentTimeMillis() - lastSync > 500) {
                            c.setParameters(Bundle().apply { putInt(MediaCodec.PARAMETER_KEY_REQUEST_SYNC_FRAME, 0) })
                            lastSync = System.currentTimeMillis()
                        }
                        continue
                    }
                    t0 = info.presentationTimeUs
                }
                val pts = 90000L + (info.presentationTimeUs - t0) * 9 / 100
                val cfg = config
                val frame = if (key && cfg != null && !hasSps(data)) cfg + data else data
                mux?.writeFrame(frame, pts, key) ?: break
                frames++
            }
        } catch (e: Exception) {
            if (running) stop("stream closed: ${e.message}")
        }
    }

    private fun hasSps(d: ByteArray): Boolean {
        var i = 0
        while (i + 4 < d.size && i < 64) {
            if (d[i].toInt() == 0 && d[i + 1].toInt() == 0 && d[i + 2].toInt() == 1 && (d[i + 3].toInt() and 0x1F) == 7) return true
            i++
        }
        return false
    }

    fun stop(reason: String? = null) {
        running = false
        try { socket?.close() } catch (_: Exception) {}
        main.post {
            try { onRelease() } catch (_: Exception) {}
            val c = codec
            codec = null
            if (c != null) Thread {
                try { c.stop() } catch (_: Exception) {}
                try { c.release() } catch (_: Exception) {}
            }.start()
        }
        if (ended.compareAndSet(false, true)) onEnd(reason)
    }
}
