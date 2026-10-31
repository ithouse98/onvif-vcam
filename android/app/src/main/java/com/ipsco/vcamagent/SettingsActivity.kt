package com.ipsco.vcamagent

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.content.res.ColorStateList
import android.net.Uri
import android.os.Bundle
import android.os.PowerManager
import android.provider.Settings
import android.text.InputType
import android.view.Gravity
import android.view.View
import android.widget.CheckBox
import android.widget.EditText
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.Toast

/** Language, theme, video, startup, about. */
class SettingsActivity : Activity() {
    private lateinit var prefs: Prefs
    private lateinit var ui: Ui
    private var fFps: EditText? = null
    private var fBitrate: EditText? = null
    private var builtLang = ""

    override fun attachBaseContext(base: Context) {
        super.attachBaseContext(Lang.wrap(base))
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        prefs = Prefs(this)
        build()
    }

    override fun onPause() {
        save()
        super.onPause()
    }

    private fun save() {
        fFps?.let { prefs.fps = it.text.toString().toIntOrNull()?.coerceIn(1, 60) ?: 25 }
        fBitrate?.let { prefs.bitrateKbps = it.text.toString().toIntOrNull()?.coerceIn(200, 20000) ?: 4000 }
    }

    private fun build() {
        save()
        if (builtLang.isNotEmpty() && builtLang != prefs.lang) {  // new language: reload resources
            builtLang = prefs.lang
            recreate()
            return
        }
        builtLang = prefs.lang
        ui = Ui(this)
        ui.styleWindow(this)
        val p = ui.p
        val root = ScrollView(this).apply { background = ui.background(); isFillViewport = true }
        val col = ui.column(18)

        val head = ui.row()
        head.addView(ui.text(if (layoutRtl()) "→" else "←", 24f, false, p.accent).apply {
            setPadding(ui.dp(10), ui.dp(2), ui.dp(14), ui.dp(2))
            setOnClickListener { finish() }
        })
        head.addView(ui.text(getString(R.string.settings), 22f, true, p.accent))
        col.addView(head, ui.lp(bottom = 18))

        // language
        val lc = ui.card(getString(R.string.sec_language))
        val lf = FlowLayout(this, ui.dp(8))
        for ((k, res) in listOf("system" to R.string.lang_system, "en" to R.string.lang_en, "fa" to R.string.lang_fa)) {
            lf.addView(ui.chip(getString(res), prefs.lang == k) { prefs.lang = k; build() })
        }
        lc.addView(lf)
        col.addView(lc, ui.lp(bottom = 14))

        // theme
        val tc = ui.card(getString(R.string.sec_theme))
        val tf = FlowLayout(this, ui.dp(8))
        for (t in Themes.all) {
            val sel = prefs.theme == t.key
            val chip = ui.row().apply {
                setPadding(ui.dp(10), ui.dp(8), ui.dp(14), ui.dp(8))
                background = ui.ripple(if (sel) ui.shape(p.field, p.accent, 20, 2.5) else ui.shape(0, p.stroke, 20, 1.5))
                setOnClickListener { prefs.theme = t.key; build() }
            }
            val sw = View(this).apply { background = ui.gradient(t.accent, t.accent2, 10) }
            val swWrap = LinearLayout(this).apply {
                background = ui.shape(t.bg1, t.stroke, 12)
                setPadding(ui.dp(4), ui.dp(4), ui.dp(4), ui.dp(4))
                addView(sw, LinearLayout.LayoutParams(ui.dp(22), ui.dp(22)))
            }
            chip.addView(swWrap)
            chip.addView(ui.text(getString(t.nameRes), 13.5f, sel).apply { setPadding(ui.dp(8), 0, 0, 0) })
            tf.addView(chip)
        }
        tc.addView(tf)
        col.addView(tc, ui.lp(bottom = 14))

        // video
        val vc = ui.card(getString(R.string.sec_video))
        vc.addView(ui.label(getString(R.string.lbl_resolution)))
        val rf = FlowLayout(this, ui.dp(8))
        for (h in listOf(480, 720, 1080)) {
            rf.addView(ui.chip("${h * 16 / 9}×$h", prefs.height == h) { prefs.height = h; build() })
        }
        vc.addView(rf)
        val vr = ui.row()
        val fc = ui.column()
        fc.addView(ui.label(getString(R.string.lbl_fps)))
        fFps = ui.field(prefs.fps.toString(), "25", InputType.TYPE_CLASS_NUMBER, ltr = true).also { fc.addView(it) }
        vr.addView(fc, ui.lp(0, weight = 1f))
        val bc = ui.column().apply { setPadding(ui.dp(10), 0, 0, 0) }
        bc.addView(ui.label(getString(R.string.lbl_bitrate)))
        fBitrate = ui.field(prefs.bitrateKbps.toString(), "4000", InputType.TYPE_CLASS_NUMBER, ltr = true).also { bc.addView(it) }
        vr.addView(bc, ui.lp(0, weight = 1f))
        vc.addView(vr)
        vc.addView(ui.text(getString(R.string.video_hint), 12f, false, p.mut).apply { setPadding(0, ui.dp(8), 0, 0) })
        col.addView(vc, ui.lp(bottom = 14))

        // startup / battery
        val sc = ui.card(getString(R.string.sec_startup))
        sc.addView(CheckBox(this).apply {
            text = getString(R.string.lbl_boot)
            isChecked = prefs.bootStart
            setTextColor(p.text)
            typeface = ui.regular
            buttonTintList = ColorStateList.valueOf(p.accent)
            setOnCheckedChangeListener { _, v -> prefs.bootStart = v }
        })
        sc.addView(ui.chip(getString(R.string.btn_battery), false) { askBattery() }, ui.lp(top = 8))
        sc.addView(ui.text(getString(R.string.battery_hint), 12f, false, p.mut).apply { setPadding(0, ui.dp(8), 0, 0) })
        col.addView(sc, ui.lp(bottom = 14))

        // about
        val ac = ui.card(getString(R.string.sec_about))
        val ar = ui.row()
        ar.addView(ImageView(this).apply { setImageResource(R.drawable.logo_mark); adjustViewBounds = true },
            LinearLayout.LayoutParams(ui.dp(64), ui.dp(60)))
        val at = ui.column().apply { setPadding(ui.dp(12), 0, ui.dp(12), 0) }
        at.addView(ui.text(getString(R.string.app_name) + " " + AgentService.VERSION.substringBefore("-"), 16f, true))
        at.addView(ui.text(getString(R.string.credit_by), 13f))
        at.addView(ui.text(getString(R.string.credit_title), 12.5f, false, p.mut))
        at.addView(ui.text(getString(R.string.credit_copy), 12f, false, p.mut))
        ar.addView(at, ui.lp(0, weight = 1f))
        ac.addView(ar)
        val links = FlowLayout(this, ui.dp(8))
        links.addView(ui.chip("Telegram @B_8241", false) { open("https://t.me/B_8241") })
        links.addView(ui.chip("GitHub", false) { open("https://github.com/ithouse98/onvif-vcam") })
        ac.addView(links, ui.lp(top = 10))
        ac.addView(ui.text(getString(R.string.agent_id, prefs.id), 11.5f, false, p.mut).apply {
            setPadding(0, ui.dp(10), 0, 0); setTextIsSelectable(true); textDirection = View.TEXT_DIRECTION_LTR
        })
        col.addView(ac, ui.lp(bottom = 8))

        root.addView(col)
        setContentView(root)
    }

    private fun layoutRtl() = resources.configuration.layoutDirection == View.LAYOUT_DIRECTION_RTL

    private fun open(url: String) {
        try { startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url))) } catch (_: Exception) {}
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
