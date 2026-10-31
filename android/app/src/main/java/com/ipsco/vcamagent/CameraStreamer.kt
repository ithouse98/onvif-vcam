package com.ipsco.vcamagent

import android.hardware.camera2.CaptureRequest
import android.media.MediaCodec
import android.media.MediaCodecInfo
import android.media.MediaFormat
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.util.Log
import android.util.Range
import android.util.Size
import androidx.camera.camera2.interop.Camera2Interop
import androidx.camera.camera2.interop.ExperimentalCamera2Interop
import androidx.camera.core.CameraSelector
import androidx.camera.core.Preview
import androidx.camera.core.SurfaceRequest
import androidx.camera.core.resolutionselector.ResolutionSelector
import androidx.camera.core.resolutionselector.ResolutionStrategy
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.core.content.ContextCompat
import androidx.lifecycle.LifecycleService
import org.json.JSONObject
import java.io.BufferedOutputStream
import java.io.BufferedReader
import java.io.InputStreamReader
import java.net.InetSocketAddress
import java.net.Socket
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean

/**
 * One agent stream: camera (CameraX) -> hardware H.264 (MediaCodec) -> MPEG-TS -> data connection
 * to the VCam agent hub ({"type": "data", "stream", "auth"} then raw TS).
 */
class CameraStreamer(
    private val service: LifecycleService,
    private val facing: Int,                 // CameraSelector.LENS_FACING_BACK / FRONT
    private val width: Int, private val height: Int, private val fps: Int, private val bitrateKbps: Int,
    private val host: String, private val port: Int, private val token: String, private val auth: String,
    private val onEnd: (String?) -> Unit,
) {
    private val tag = "VCamStreamer"
    private val main = Handler(Looper.getMainLooper())
    private val executor = Executors.newSingleThreadExecutor()
    private val ended = AtomicBoolean(false)
    @Volatile private var running = true
    private var socket: Socket? = null
    private var mux: TsMuxer? = null
    private var codec: MediaCodec? = null
    private var provider: ProcessCameraProvider? = null
    @Volatile var frames = 0L
        private set

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
                main.post { bindCamera() }
            } catch (e: Exception) {
                stop("cannot open the data connection: ${e.message}")
            }
        }, "vcam-data").start()
    }

    @androidx.annotation.OptIn(ExperimentalCamera2Interop::class)
    private fun bindCamera() {
        if (!running) return
        val future = ProcessCameraProvider.getInstance(service)
        future.addListener({
            try {
                val p = future.get()
                provider = p
                val sel = ResolutionSelector.Builder().setResolutionStrategy(
                    ResolutionStrategy(Size(width, height), ResolutionStrategy.FALLBACK_RULE_CLOSEST_HIGHER_THEN_LOWER)
                ).build()
                val builder = Preview.Builder().setResolutionSelector(sel)
                Camera2Interop.Extender(builder)
                    .setCaptureRequestOption(CaptureRequest.CONTROL_AE_TARGET_FPS_RANGE, Range(fps, fps))
                val preview = builder.build()
                preview.setSurfaceProvider(executor) { req -> onSurfaceRequest(req) }
                p.unbindAll()
                p.bindToLifecycle(service, CameraSelector.Builder().requireLensFacing(facing).build(), preview)
            } catch (e: Exception) {
                stop("camera error: ${e.message}")
            }
        }, ContextCompat.getMainExecutor(service))
    }

    private fun onSurfaceRequest(req: SurfaceRequest) {
        if (!running) {
            req.willNotProvideSurface()
            return
        }
        try {
            val size = req.resolution
            val fmt = MediaFormat.createVideoFormat(MediaFormat.MIMETYPE_VIDEO_AVC, size.width, size.height).apply {
                setInteger(MediaFormat.KEY_COLOR_FORMAT, MediaCodecInfo.CodecCapabilities.COLOR_FormatSurface)
                setInteger(MediaFormat.KEY_BIT_RATE, bitrateKbps * 1000)
                setInteger(MediaFormat.KEY_FRAME_RATE, fps)
                setInteger(MediaFormat.KEY_I_FRAME_INTERVAL, 2)
                if (Build.VERSION.SDK_INT >= 29) setInteger(MediaFormat.KEY_PREPEND_HEADER_TO_SYNC_FRAMES, 1)
            }
            val c = MediaCodec.createEncoderByType(MediaFormat.MIMETYPE_VIDEO_AVC)
            c.configure(fmt, null, null, MediaCodec.CONFIGURE_FLAG_ENCODE)
            val surface = c.createInputSurface()
            c.start()
            codec = c
            req.provideSurface(surface, executor) { surface.release() }
            Log.i(tag, "encoding ${size.width}x${size.height} @$fps, $bitrateKbps kbps")
            Thread({ drain(c) }, "vcam-encoder").start()
        } catch (e: Exception) {
            req.willNotProvideSurface()
            stop("encoder error: ${e.message}")
        }
    }

    private fun drain(c: MediaCodec) {
        val info = MediaCodec.BufferInfo()
        var config: ByteArray? = null
        var t0 = -1L
        var lastSync = System.currentTimeMillis()
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
            try { provider?.unbindAll() } catch (_: Exception) {}
            executor.execute {
                try { codec?.stop() } catch (_: Exception) {}
                try { codec?.release() } catch (_: Exception) {}
                codec = null
            }
        }
        if (ended.compareAndSet(false, true)) onEnd(reason)
    }
}
