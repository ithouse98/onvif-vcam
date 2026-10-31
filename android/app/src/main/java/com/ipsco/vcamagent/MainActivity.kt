package com.ipsco.vcamagent

import android.Manifest
import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.PowerManager
import android.provider.Settings
import android.widget.ArrayAdapter
import android.widget.Button
import android.widget.CheckBox
import android.widget.EditText
import android.widget.Spinner
import android.widget.TextView
import android.widget.Toast

/** Settings + start / stop. Extras for automation (adb): server, port, key, name, height, fps,
 *  bitrate, start=true. */
class MainActivity : Activity() {
    private lateinit var prefs: Prefs
    private val heights = intArrayOf(480, 720, 1080)
    private val handler = Handler(Looper.getMainLooper())
    private var pendingStart = false

    private val tick = object : Runnable {
        override fun run() {
            findViewById<TextView>(R.id.status).text =
                if (AgentService.running) AgentService.status else getString(R.string.st_stopped)
            findViewById<Button>(R.id.start).isEnabled = !AgentService.running
            findViewById<Button>(R.id.stop).isEnabled = AgentService.running
            handler.postDelayed(this, 1000)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        prefs = Prefs(this)
        intent?.extras?.let { x ->
            x.getString("server")?.let { prefs.server = it }
            x.getString("key")?.let { prefs.key = it }
            x.getString("name")?.let { prefs.name = it }
            if (x.containsKey("port")) prefs.port = x.getString("port")?.toIntOrNull() ?: x.getInt("port", 8600)
            if (x.containsKey("height")) prefs.height = x.getString("height")?.toIntOrNull() ?: x.getInt("height", 720)
            if (x.containsKey("fps")) prefs.fps = x.getString("fps")?.toIntOrNull() ?: x.getInt("fps", 25)
            if (x.containsKey("bitrate")) prefs.bitrateKbps = x.getString("bitrate")?.toIntOrNull() ?: x.getInt("bitrate", 4000)
            pendingStart = x.getBoolean("start", false) || x.getString("start") == "true"
        }
        findViewById<EditText>(R.id.server).setText(prefs.server)
        findViewById<EditText>(R.id.port).setText(prefs.port.toString())
        findViewById<EditText>(R.id.key).setText(prefs.key)
        findViewById<EditText>(R.id.name).setText(prefs.name)
        findViewById<EditText>(R.id.fps).setText(prefs.fps.toString())
        findViewById<EditText>(R.id.bitrate).setText(prefs.bitrateKbps.toString())
        findViewById<CheckBox>(R.id.boot).isChecked = prefs.bootStart
        val res = findViewById<Spinner>(R.id.resolution)
        res.adapter = ArrayAdapter(this, android.R.layout.simple_spinner_dropdown_item,
            heights.map { "${it * 16 / 9}×$it" })
        res.setSelection(heights.indexOf(prefs.height).coerceAtLeast(0))
        findViewById<Button>(R.id.start).setOnClickListener { save(); startAgent() }
        findViewById<Button>(R.id.stop).setOnClickListener { AgentService.stop(this) }
        findViewById<Button>(R.id.battery).setOnClickListener { askBattery() }
        findViewById<TextView>(R.id.idtext).text = getString(R.string.agent_id, prefs.id)
        if (pendingStart) startAgent()
    }

    override fun onResume() {
        super.onResume()
        handler.post(tick)
    }

    override fun onPause() {
        handler.removeCallbacks(tick)
        super.onPause()
    }

    private fun save() {
        prefs.server = findViewById<EditText>(R.id.server).text.toString()
        prefs.port = findViewById<EditText>(R.id.port).text.toString().toIntOrNull()?.coerceIn(1, 65535) ?: 8600
        prefs.key = findViewById<EditText>(R.id.key).text.toString()
        prefs.name = findViewById<EditText>(R.id.name).text.toString().ifBlank { "${Build.MANUFACTURER} ${Build.MODEL}" }
        prefs.fps = findViewById<EditText>(R.id.fps).text.toString().toIntOrNull()?.coerceIn(1, 60) ?: 25
        prefs.bitrateKbps = findViewById<EditText>(R.id.bitrate).text.toString().toIntOrNull()?.coerceIn(200, 20000) ?: 4000
        prefs.height = heights[findViewById<Spinner>(R.id.resolution).selectedItemPosition.coerceIn(0, 2)]
        prefs.bootStart = findViewById<CheckBox>(R.id.boot).isChecked
    }

    private fun startAgent() {
        val need = mutableListOf<String>()
        if (checkSelfPermission(Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED) need += Manifest.permission.CAMERA
        if (Build.VERSION.SDK_INT >= 33 &&
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) need += Manifest.permission.POST_NOTIFICATIONS
        if (need.isNotEmpty()) {
            pendingStart = true
            requestPermissions(need.toTypedArray(), 1)
            return
        }
        pendingStart = false
        if (AgentService.running) AgentService.stop(this)
        AgentService.start(this)
        Toast.makeText(this, R.string.started, Toast.LENGTH_SHORT).show()
    }

    override fun onRequestPermissionsResult(code: Int, perms: Array<out String>, results: IntArray) {
        super.onRequestPermissionsResult(code, perms, results)
        if (checkSelfPermission(Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED) {
            if (pendingStart) startAgent()
        } else {
            Toast.makeText(this, R.string.need_camera, Toast.LENGTH_LONG).show()
        }
    }

    private fun askBattery() {
        val pm = getSystemService(PowerManager::class.java)
        if (pm.isIgnoringBatteryOptimizations(packageName)) {
            Toast.makeText(this, R.string.battery_ok, Toast.LENGTH_SHORT).show()
            return
        }
        try {
            startActivity(Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:$packageName")))
        } catch (_: Exception) {
            startActivity(Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS))
        }
    }
}
