package com.ipsco.vcamagent

import android.hardware.camera2.CaptureRequest
import android.util.Range
import android.util.Size
import androidx.camera.camera2.interop.Camera2Interop
import androidx.camera.camera2.interop.ExperimentalCamera2Interop
import androidx.camera.core.CameraSelector
import androidx.camera.core.Preview
import androidx.camera.core.SurfaceRequest
import androidx.camera.core.resolutionselector.AspectRatioStrategy
import androidx.camera.core.resolutionselector.ResolutionSelector
import androidx.camera.core.resolutionselector.ResolutionStrategy
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.core.content.ContextCompat
import androidx.lifecycle.LifecycleService
import java.util.concurrent.Executors

/** Camera (CameraX Preview) feeding the encoder surface. */
class CameraStreamer(
    private val service: LifecycleService, private val facing: Int,
    private val width: Int, private val height: Int,
    fps: Int, bitrateKbps: Int, host: String, port: Int, token: String, auth: String, onEnd: (String?) -> Unit,
) : StreamBase(service, fps, bitrateKbps, host, port, token, auth, onEnd) {
    private val executor = Executors.newSingleThreadExecutor()
    private var provider: ProcessCameraProvider? = null

    @androidx.annotation.OptIn(ExperimentalCamera2Interop::class)
    override fun onConnected() {
        val future = ProcessCameraProvider.getInstance(service)
        future.addListener({
            try {
                if (!running) return@addListener
                val p = future.get()
                provider = p
                val sel = ResolutionSelector.Builder()
                    .setAspectRatioStrategy(AspectRatioStrategy.RATIO_16_9_FALLBACK_AUTO_STRATEGY)  // VMS cameras are 16:9
                    .setResolutionStrategy(ResolutionStrategy(Size(width, height),
                        ResolutionStrategy.FALLBACK_RULE_CLOSEST_HIGHER_THEN_LOWER))
                    .build()
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
            val surface = startEncoder(req.resolution.width, req.resolution.height)
            req.provideSurface(surface, executor) { }
        } catch (e: Exception) {
            req.willNotProvideSurface()
            stop("encoder error: ${e.message}")
        }
    }

    override fun onRelease() {
        provider?.unbindAll()
    }
}
