package com.ipsco.vcamagent

import android.app.Activity
import android.content.Context
import android.content.res.ColorStateList
import android.content.res.Configuration
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.Drawable
import android.graphics.drawable.GradientDrawable
import android.graphics.drawable.RippleDrawable
import android.os.Build
import android.text.InputType
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.view.WindowInsetsController
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView
import androidx.core.content.res.ResourcesCompat
import java.util.Locale

/** Colour themes of the app (same family as the VCam web panel). */
data class Palette(
    val key: String, val nameRes: Int, val light: Boolean,
    val bg1: Int, val bg2: Int, val card: Int, val stroke: Int, val field: Int,
    val accent: Int, val accent2: Int, val text: Int, val mut: Int,
    val ok: Int = 0xFF2EE6A6.toInt(), val bad: Int = 0xFFFF4D6D.toInt(), val warn: Int = 0xFFFFB020.toInt(),
)

object Themes {
    private fun c(s: String) = Color.parseColor(s)
    val all = listOf(
        Palette("cyber", R.string.th_cyber, false, c("#070B1A"), c("#1A0733"), c("#CC171034"), c("#5A3DC8"),
            c("#0E1430"), c("#00E5FF"), c("#FF2BD6"), c("#E8F1FF"), c("#8B9BC7")),
        Palette("matrix", R.string.th_matrix, false, c("#020A06"), c("#04200F"), c("#CC0A2416"), c("#1E7A46"),
            c("#061A10"), c("#39FF88"), c("#00C2A8"), c("#DDFBE9"), c("#7FB79A")),
        Palette("datacenter", R.string.th_datacenter, false, c("#06101F"), c("#0B2142"), c("#CC10264A"), c("#2A5598"),
            c("#0A1A33"), c("#38BDF8"), c("#2563EB"), c("#E3EDF9"), c("#8AA3C2")),
        Palette("amber", R.string.th_amber, false, c("#120C04"), c("#251708"), c("#CC2A1C0A"), c("#7A5218"),
            c("#1A1206"), c("#FFC233"), c("#FF7A1A"), c("#FFF1D6"), c("#C2A578")),
        Palette("purple", R.string.th_purple, false, c("#0D0A1F"), c("#22134A"), c("#CC231A48"), c("#5B45B0"),
            c("#150F30"), c("#B18CFF"), c("#EC4899"), c("#EEE8FF"), c("#A39CC8")),
        Palette("light", R.string.th_light, true, c("#EEF3FA"), c("#D6E2F5"), c("#F2FFFFFF"), c("#BFD0EA"),
            c("#F2F6FB"), c("#0B6BCB"), c("#7C3AED"), c("#13202E"), c("#5F6D80"),
            ok = c("#0E9F6E"), bad = c("#E11D48")),
    )

    fun current(ctx: Context): Palette = all.firstOrNull { it.key == Prefs(ctx).theme } ?: all[0]
}

/** Language override (Settings): wraps a base context with the chosen locale. */
object Lang {
    fun wrap(base: Context): Context {
        val lang = Prefs(base).lang
        if (lang == "system") return base
        val loc = Locale(lang)
        Locale.setDefault(loc)
        val cfg = Configuration(base.resources.configuration)
        cfg.setLocale(loc)
        cfg.setLayoutDirection(loc)
        return base.createConfigurationContext(cfg)
    }
}

/** Small view builders that paint everything from the current palette. */
class Ui(val ctx: Context, val p: Palette = Themes.current(ctx)) {
    val regular: Typeface? = ResourcesCompat.getFont(ctx, R.font.vazirmatn_regular)
    val bold: Typeface? = ResourcesCompat.getFont(ctx, R.font.vazirmatn_bold)

    fun dp(v: Number): Int = TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, v.toFloat(), ctx.resources.displayMetrics).toInt()

    fun background(): Drawable = GradientDrawable(GradientDrawable.Orientation.TL_BR, intArrayOf(p.bg1, p.bg2))

    fun styleWindow(a: Activity) {
        a.window.statusBarColor = p.bg1
        a.window.navigationBarColor = p.bg2
        if (Build.VERSION.SDK_INT >= 30) {
            val f = WindowInsetsController.APPEARANCE_LIGHT_STATUS_BARS or WindowInsetsController.APPEARANCE_LIGHT_NAVIGATION_BARS
            // via decorView: window.insetsController NPEs on API 30 before setContentView
            a.window.decorView.windowInsetsController?.setSystemBarsAppearance(if (p.light) f else 0, f)
        } else if (p.light) {
            @Suppress("DEPRECATION")
            a.window.decorView.systemUiVisibility = View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR
        }
    }

    fun shape(fill: Int, stroke: Int = 0, radius: Number = 18, strokeWidth: Number = 1): GradientDrawable =
        GradientDrawable().apply {
            cornerRadius = dp(radius).toFloat()
            setColor(fill)
            if (stroke != 0) setStroke(dp(strokeWidth), stroke)
        }

    fun gradient(c1: Int, c2: Int, radius: Number = 28): GradientDrawable =
        GradientDrawable(GradientDrawable.Orientation.LEFT_RIGHT, intArrayOf(c1, c2)).apply { cornerRadius = dp(radius).toFloat() }

    fun ripple(d: Drawable): Drawable = RippleDrawable(ColorStateList.valueOf(0x40FFFFFF), d, null)

    fun text(s: CharSequence, size: Float = 14f, b: Boolean = false, color: Int = p.text): TextView =
        TextView(ctx).apply {
            text = s
            setTextSize(TypedValue.COMPLEX_UNIT_SP, size)
            setTextColor(color)
            typeface = if (b) bold else regular
            setLineSpacing(0f, 1.15f)
        }

    fun column(pad: Number = 0): LinearLayout = LinearLayout(ctx).apply {
        orientation = LinearLayout.VERTICAL
        val px = dp(pad); setPadding(px, px, px, px)
    }

    fun row(): LinearLayout = LinearLayout(ctx).apply {
        orientation = LinearLayout.HORIZONTAL
        gravity = Gravity.CENTER_VERTICAL
    }

    /** Card with a neon edge and a section title. */
    fun card(title: String?): LinearLayout = column(16).apply {
        background = shape(p.card, p.stroke)
        elevation = dp(4).toFloat()
        if (title != null) {
            val bar = row()
            bar.addView(View(ctx).apply { background = gradient(p.accent, p.accent2, 2) }, LinearLayout.LayoutParams(dp(4), dp(18)))
            bar.addView(text(title, 15f, true, p.accent).apply { setPadding(dp(8), 0, dp(8), 0) })
            addView(bar, lp(bottom = 10))
        }
    }

    fun lp(w: Int = LinearLayout.LayoutParams.MATCH_PARENT, h: Int = LinearLayout.LayoutParams.WRAP_CONTENT,
           top: Number = 0, bottom: Number = 0, weight: Float = 0f): LinearLayout.LayoutParams =
        LinearLayout.LayoutParams(w, h, weight).apply { topMargin = dp(top); bottomMargin = dp(bottom) }

    fun label(s: String): TextView = text(s, 12.5f, false, p.mut).apply { setPadding(dp(4), dp(8), dp(4), dp(4)) }

    fun field(value: String, hint: String = "", type: Int = InputType.TYPE_CLASS_TEXT, ltr: Boolean = false): EditText =
        EditText(ctx).apply {
            setText(value)
            this.hint = hint
            inputType = type
            isSingleLine = true
            setTextColor(p.text)
            setHintTextColor(p.mut)
            typeface = regular
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 15f)
            background = shape(p.field, p.stroke, 12)
            setPadding(dp(14), dp(10), dp(14), dp(10))
            if (ltr) {
                textDirection = View.TEXT_DIRECTION_LTR
                textAlignment = View.TEXT_ALIGNMENT_VIEW_START
            }
        }

    /** Pill button; selected = filled with the accent gradient. */
    fun chip(s: String, selected: Boolean, onClick: () -> Unit): TextView =
        text(s, 13.5f, selected, if (selected) (if (p.light) Color.WHITE else p.bg1) else p.text).apply {
            gravity = Gravity.CENTER
            setPadding(dp(14), dp(9), dp(14), dp(9))
            background = ripple(if (selected) gradient(p.accent, p.accent2, 20) else shape(0, p.stroke, 20, 1.5))
            setOnClickListener { onClick() }
        }

    /** One equal-width segment of a segmented control; selected = accent fill. */
    fun segment(s: String, selected: Boolean, onClick: () -> Unit): TextView =
        text(s, 13.5f, selected, if (selected) (if (p.light) Color.WHITE else p.bg1) else p.text).apply {
            gravity = Gravity.CENTER
            minHeight = dp(46)
            setPadding(dp(8), dp(10), dp(8), dp(10))
            background = ripple(if (selected) gradient(p.accent, p.accent2, 14) else shape(p.field, p.stroke, 14))
            setOnClickListener { onClick() }
        }

    fun bigButton(s: String, c1: Int, c2: Int, onClick: () -> Unit): TextView =
        text(s, 18f, true, Color.WHITE).apply {
            gravity = Gravity.CENTER
            setPadding(dp(18), dp(16), dp(18), dp(16))
            background = ripple(gradient(c1, c2, 30))
            elevation = dp(6).toFloat()
            setOnClickListener { onClick() }
        }

    fun dot(color: Int, size: Number = 14): View = View(ctx).apply {
        background = GradientDrawable().apply { shape = GradientDrawable.OVAL; setColor(color) }
        layoutParams = LinearLayout.LayoutParams(dp(size), dp(size))
    }
}

/** Wraps chips onto several lines. */
class FlowLayout(ctx: Context, private val gap: Int) : android.view.ViewGroup(ctx) {
    override fun onMeasure(wSpec: Int, hSpec: Int) {
        val maxW = MeasureSpec.getSize(wSpec) - paddingLeft - paddingRight
        var x = 0; var y = 0; var lineH = 0
        for (i in 0 until childCount) {
            val c = getChildAt(i)
            measureChild(c, MeasureSpec.makeMeasureSpec(maxW, MeasureSpec.AT_MOST), hSpec)
            if (x > 0 && x + c.measuredWidth > maxW) { x = 0; y += lineH + gap; lineH = 0 }
            x += c.measuredWidth + gap
            lineH = maxOf(lineH, c.measuredHeight)
        }
        setMeasuredDimension(MeasureSpec.getSize(wSpec), y + lineH + paddingTop + paddingBottom)
    }

    override fun onLayout(changed: Boolean, l: Int, t: Int, r: Int, b: Int) {
        val maxW = r - l - paddingLeft - paddingRight
        val rtl = layoutDirection == LAYOUT_DIRECTION_RTL
        var x = 0; var y = 0; var lineH = 0
        for (i in 0 until childCount) {
            val c = getChildAt(i)
            if (x > 0 && x + c.measuredWidth > maxW) { x = 0; y += lineH + gap; lineH = 0 }
            val left = if (rtl) paddingLeft + maxW - x - c.measuredWidth else paddingLeft + x
            c.layout(left, paddingTop + y, left + c.measuredWidth, paddingTop + y + c.measuredHeight)
            x += c.measuredWidth + gap
            lineH = maxOf(lineH, c.measuredHeight)
        }
    }
}
