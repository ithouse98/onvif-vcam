package com.ipsco.vcamagent

import android.content.Context
import android.os.Build
import java.util.UUID

/** Agent settings (SharedPreferences). */
class Prefs(ctx: Context) {
    private val p = ctx.getSharedPreferences("agent", Context.MODE_PRIVATE)

    val id: String
        get() = p.getString("id", null) ?: UUID.randomUUID().toString().also { p.edit().putString("id", it).apply() }
    var server: String
        get() = p.getString("server", "") ?: ""
        set(v) = p.edit().putString("server", v.trim()).apply()
    var port: Int
        get() = p.getInt("port", 8600)
        set(v) = p.edit().putInt("port", v).apply()
    var key: String
        get() = p.getString("key", "") ?: ""
        set(v) = p.edit().putString("key", v.trim()).apply()
    var name: String
        get() = p.getString("name", null) ?: "${Build.MANUFACTURER} ${Build.MODEL}".trim()
        set(v) = p.edit().putString("name", v.trim()).apply()
    var height: Int                       // 480 / 720 / 1080 (16:9)
        get() = p.getInt("height", 720)
        set(v) = p.edit().putInt("height", v).apply()
    var fps: Int
        get() = p.getInt("fps", 25)
        set(v) = p.edit().putInt("fps", v).apply()
    var bitrateKbps: Int
        get() = p.getInt("bitrate", 4000)
        set(v) = p.edit().putInt("bitrate", v).apply()
    var bootStart: Boolean
        get() = p.getBoolean("boot", false)
        set(v) = p.edit().putBoolean("boot", v).apply()
    var enabled: Boolean                  // the user pressed Start (service should run)
        get() = p.getBoolean("enabled", false)
        set(v) = p.edit().putBoolean("enabled", v).apply()
    /** auto (the VCam panel picks by monitor index) | back | front | screen */
    var source: String
        get() = p.getString("source", "auto") ?: "auto"
        set(v) = p.edit().putString("source", v).apply()
    /** system | en | fa */
    var lang: String
        get() = p.getString("lang", "system") ?: "system"
        set(v) = p.edit().putString("lang", v).apply()
    var theme: String
        get() = p.getString("theme", "cyber") ?: "cyber"
        set(v) = p.edit().putString("theme", v).apply()

    val width: Int get() = height * 16 / 9
}
