package com.ipsco.vcamagent

import android.Manifest
import android.animation.ObjectAnimator
import android.animation.ValueAnimator
import android.app.Activity
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.drawable.GradientDrawable
import android.media.projection.MediaProjectionManager
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.text.InputType
import android.view.Gravity
import android.view.View
import android.widget.EditText
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast

/** Status, image source, server; start / stop. Extras for automation (adb): server, port, key, name,
 *  height, fps, bitrate, source (auto|back|front|screen), theme, lang, start=true. */
class MainActivity : Activity() {
    private lateinit var prefs: Prefs
    private lateinit var ui: Ui
    private val handler = Handler(Looper.getMainLooper())
    private var pendingStart = false
    private var builtTheme = ""
    private var builtLang = ""
    private lateinit var statusText: TextView
    private lateinit var statsText: TextView
    private lateinit var dot: View
    private lateinit var btnMain: TextView
    private var btnRunning: Boolean? = null
    private var fServer: EditText? = null
    private var fPort: EditText? = null
    private var fKey: EditText? = null
    private var fName: EditText? = null
    private var pulse: ObjectAnimator? = null

    companion object {
        private const val REQ_PERM = 1
        private const val REQ_SCREEN = 7
    }

    override fun attachBaseContext(base: Context) {
        super.attachBaseContext(Lang.wrap(base))
    }

    private val tick = object : Runnable {
        override fun run() {
            refresh()
            handler.postDelayed(this, 1000)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        prefs = Prefs(this)
        applyExtras(intent)
        build()
        if (pendingStart) startAgent()
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        val before = prefs.source
        val lang = prefs.lang
        applyExtras(intent)
        build()
        if (prefs.source != before) onSourceChanged()
        if (pendingStart) startAgent()
        if (prefs.lang != lang) recreate()   // the language lives in the activity's base context
    }

    override fun onResume() {
        super.onResume()
        if (prefs.theme != builtTheme || prefs.lang != builtLang) {
            recreate()
            return
        }
        handler.post(tick)
    }

    override fun onPause() {
        handler.removeCallbacks(tick)
        save()
        super.onPause()
    }

    private fun applyExtras(i: Intent?) {
        val x = i?.extras ?: return
        fun int(k: String, d: Int) = x.getString(k)?.toIntOrNull() ?: x.getInt(k, d)
        x.getString("server")?.let { prefs.server = it }
        x.getString("key")?.let { prefs.key = it }
        x.getString("name")?.let { prefs.name = it }
        x.getString("source")?.let { if (it in listOf("auto", "back", "front", "screen")) prefs.source = it }
        x.getString("theme")?.let { t -> if (Themes.all.any { it.key == t }) prefs.theme = t }
        x.getString("lang")?.let { if (it in listOf("system", "en", "fa")) prefs.lang = it }
        if (x.containsKey("port")) prefs.port = int("port", 8600)
        if (x.containsKey("height")) prefs.height = int("height", 720)
        if (x.containsKey("fps")) prefs.fps = int("fps", 25)
        if (x.containsKey("bitrate")) prefs.bitrateKbps = int("bitrate", 4000)
        pendingStart = x.getBoolean("start", false) || x.getString("start") == "true"
        i!!.replaceExtras(Bundle())   // apply once (recreate / rotation keep the intent)
    }

    private fun build() {
        ui = Ui(this)
        ui.styleWindow(this)
        builtTheme = prefs.theme
        builtLang = prefs.lang
        btnRunning = null
        val p = ui.p
        val root = ScrollView(this).apply { background = ui.background(); isFillViewport = true }
        val col = ui.column(18)

        // header
        val head = ui.row()
        head.addView(ImageView(this).apply { setImageResource(R.drawable.logo_mark); adjustViewBounds = true },
            LinearLayout.LayoutParams(ui.dp(56), ui.dp(52)))
        val titles = ui.column().apply { setPadding(ui.dp(12), 0, ui.dp(12), 0) }
        titles.addView(ui.text(getString(R.string.app_name), 22f, true, p.accent))
        titles.addView(ui.text(getString(R.string.app_sub), 12.5f, false, p.mut))
        head.addView(titles, ui.lp(0, weight = 1f))
        head.addView(ui.text("⚙", 26f, false, p.accent).apply {
            setPadding(ui.dp(12), ui.dp(4), ui.dp(12), ui.dp(4))
            background = ui.ripple(ui.shape(0, p.stroke, 14))
            contentDescription = getString(R.string.settings)
            setOnClickListener { save(); startActivity(Intent(this@MainActivity, SettingsActivity::class.java)) }
        })
        col.addView(head, ui.lp(bottom = 18))

        // status
        val sc = ui.card(getString(R.string.sec_status))
        val r = ui.row()
        dot = ui.dot(p.mut, 14)
        r.addView(dot)
        statusText = ui.text("", 16f, true).apply { setPadding(ui.dp(10), 0, ui.dp(10), 0) }
        r.addView(statusText, ui.lp(0, weight = 1f))
        sc.addView(r)
        statsText = ui.text("", 12.5f, false, p.mut).apply { setPadding(0, ui.dp(8), 0, 0) }
        sc.addView(statsText)
        col.addView(sc, ui.lp(bottom = 14))

        // source
        val src = ui.card(getString(R.string.sec_source))
        val options = listOf("auto" to R.string.src_auto, "back" to R.string.src_back,
                             "front" to R.string.src_front, "screen" to R.string.src_screen)
        for (pair in options.chunked(2)) {      // 2 x 2 grid of equal segments
            val gridRow = ui.row()
            for ((k, res) in pair) {
                gridRow.addView(ui.segment(getString(res), prefs.source == k) { selectSource(k) },
                    LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
                        .apply { setMargins(ui.dp(4), 0, ui.dp(4), ui.dp(8)) })
            }
            src.addView(gridRow)
        }
        src.addView(ui.text(getString(if (prefs.source == "auto") R.string.src_hint_auto else R.string.src_hint_fixed),
            12.5f, false, p.mut).apply { setPadding(0, ui.dp(10), 0, 0) })
        if (prefs.source == "screen") {
            if (ScreenCapture.active) {
                src.addView(ui.text("✓ " + getString(R.string.screen_ok), 13f, true, p.ok).apply { setPadding(0, ui.dp(8), 0, 0) })
            } else {
                src.addView(ui.chip(getString(R.string.screen_allow), false) { requestScreen() }, ui.lp(top = 10))
            }
        }
        col.addView(src, ui.lp(bottom = 14))

        // server
        val sv = ui.card(getString(R.string.sec_server))
        sv.addView(ui.label(getString(R.string.lbl_server)))
        fServer = ui.field(prefs.server, getString(R.string.hint_server),
            InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_URI, ltr = true).also { sv.addView(it) }
        val pr = ui.row()
        val pc = ui.column()
        pc.addView(ui.label(getString(R.string.lbl_port)))
        fPort = ui.field(prefs.port.toString(), "8600", InputType.TYPE_CLASS_NUMBER, ltr = true).also { pc.addView(it) }
        pr.addView(pc, ui.lp(0, weight = 1f))
        val kc = ui.column().apply { setPadding(ui.dp(10), 0, 0, 0) }
        kc.addView(ui.label(getString(R.string.lbl_key)))
        fKey = ui.field(prefs.key, "", InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD, ltr = true)
            .also { kc.addView(it) }
        pr.addView(kc, ui.lp(0, weight = 2f))
        sv.addView(pr)
        sv.addView(ui.label(getString(R.string.lbl_name)))
        fName = ui.field(prefs.name).also { sv.addView(it) }
        col.addView(sv, ui.lp(bottom = 18))

        // one button: starts the agent, or stops it while it runs
        btnMain = ui.bigButton("", p.accent, p.accent2) {
            if (AgentService.running) AgentService.stop(this) else { save(); startAgent() }
        }
        col.addView(btnMain, ui.lp(top = 6, bottom = 4))

        col.addView(ui.text(getString(R.string.credit_by) + "\n" + getString(R.string.credit_copy), 11.5f, false, p.mut).apply {
            gravity = Gravity.CENTER
            setPadding(0, ui.dp(18), 0, ui.dp(8))
        })
        root.addView(col)
        setContentView(root)
        pulse = null
        refresh()
    }

    private fun refresh() {
        val p = ui.p
        val running = AgentService.running
        val live = AgentService.streaming
        statusText.text = if (running) AgentService.status.ifEmpty { getString(R.string.st_starting) } else getString(R.string.st_stopped)
        (dot.background as GradientDrawable).setColor(if (live != null) p.ok else if (running) p.accent else p.mut)
        if (live != null && pulse == null) {
            pulse = ObjectAnimator.ofFloat(dot, View.ALPHA, 1f, 0.25f).apply {
                duration = 700; repeatMode = ValueAnimator.REVERSE; repeatCount = ValueAnimator.INFINITE; start()
            }
        } else if (live == null && pulse != null) {
            pulse?.cancel(); pulse = null; dot.alpha = 1f
        }
        val secs = AgentService.streamSeconds
        statsText.text = if (live != null) getString(R.string.stats, live, AgentService.frames,
            if (secs > 0) AgentService.frames / secs else 0L)
        else getString(R.string.stats_idle, srcName(prefs.source), "${prefs.width}×${prefs.height} @${prefs.fps}")
        if (running != btnRunning) {            // restyle the button only when its state changes
            btnRunning = running
            btnMain.text = if (running) "■  " + getString(R.string.btn_stop) else "▶  " + getString(R.string.btn_start)
            val (c1, c2) = if (running) p.bad to 0xFF7A1238.toInt() else p.accent to p.accent2
            btnMain.background = ui.ripple(ui.gradient(c1, c2, 30))
        }
    }

    private fun srcName(k: String) = getString(when (k) {
        "back" -> R.string.src_back; "front" -> R.string.src_front; "screen" -> R.string.src_screen; else -> R.string.src_auto
    })

    private fun save() {
        fServer?.let { prefs.server = it.text.toString() }
        fPort?.let { prefs.port = it.text.toString().toIntOrNull()?.coerceIn(1, 65535) ?: 8600 }
        fKey?.let { prefs.key = it.text.toString() }
        fName?.let { prefs.name = it.text.toString().ifBlank { "${Build.MANUFACTURER} ${Build.MODEL}" } }
    }

    private fun selectSource(k: String) {
        save()
        if (prefs.source == k) return
        prefs.source = k
        build()
        onSourceChanged()
    }

    private fun onSourceChanged() {
        if (prefs.source == "screen" && AgentService.running && !ScreenCapture.active) requestScreen()
        else AgentService.sourceChanged(this)
    }

    private fun requestScreen() {
        val mpm = getSystemService(MediaProjectionManager::class.java)
        startActivityForResult(mpm.createScreenCaptureIntent(), REQ_SCREEN)
    }

    @Deprecated("Activity result API of the platform Activity")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode != REQ_SCREEN) return
        if (resultCode == RESULT_OK && data != null) {
            if (!AgentService.running) prefs.enabled = true
            AgentService.screenConsent(this, resultCode, data)
            pendingStart = false
            handler.postDelayed({ build() }, 600)
        } else {
            Toast.makeText(this, R.string.screen_denied, Toast.LENGTH_LONG).show()
        }
    }

    private fun startAgent() {
        val need = mutableListOf<String>()
        if (checkSelfPermission(Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED) need += Manifest.permission.CAMERA
        if (Build.VERSION.SDK_INT >= 33 &&
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) need += Manifest.permission.POST_NOTIFICATIONS
        if (need.isNotEmpty()) {
            pendingStart = true
            requestPermissions(need.toTypedArray(), REQ_PERM)
            return
        }
        if (prefs.source == "screen" && !ScreenCapture.active) {   // consent first; it also starts the agent
            pendingStart = true
            requestScreen()
            return
        }
        pendingStart = false
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
}
