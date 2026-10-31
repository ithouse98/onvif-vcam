package com.ipsco.vcamagent

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.Gravity
import android.view.View
import android.view.animation.AlphaAnimation
import android.view.animation.AnimationSet
import android.view.animation.ScaleAnimation
import android.widget.FrameLayout
import android.widget.ImageView
import android.widget.LinearLayout

/** Logo + copyright for a moment, then the main screen. */
class SplashActivity : Activity() {
    override fun attachBaseContext(base: Context) {
        super.attachBaseContext(Lang.wrap(base))
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val ui = Ui(this)
        ui.styleWindow(this)
        val root = FrameLayout(this).apply { background = ui.background() }
        val center = ui.column().apply { gravity = Gravity.CENTER_HORIZONTAL }
        val logo = ImageView(this).apply { setImageResource(R.drawable.logo_mark); adjustViewBounds = true }
        center.addView(logo, LinearLayout.LayoutParams(ui.dp(190), ui.dp(177)).apply { gravity = Gravity.CENTER_HORIZONTAL })
        center.addView(ui.text("IPS-Co.", 26f, true, ui.p.accent).apply {
            gravity = Gravity.CENTER; letterSpacing = 0.12f; setPadding(0, ui.dp(10), 0, 0)
        })
        center.addView(ui.text(getString(R.string.app_name), 20f, true).apply { gravity = Gravity.CENTER })
        center.addView(ui.text(getString(R.string.app_sub), 13f, false, ui.p.mut).apply { gravity = Gravity.CENTER })
        center.addView(View(this).apply { background = ui.gradient(ui.p.accent, ui.p.accent2, 2) },
            LinearLayout.LayoutParams(ui.dp(120), ui.dp(3)).apply { gravity = Gravity.CENTER_HORIZONTAL; topMargin = ui.dp(18) })
        root.addView(center, FrameLayout.LayoutParams(FrameLayout.LayoutParams.MATCH_PARENT,
            FrameLayout.LayoutParams.WRAP_CONTENT, Gravity.CENTER))
        val credits = ui.column(20).apply { gravity = Gravity.CENTER_HORIZONTAL }
        credits.addView(ui.text(getString(R.string.credit_by), 13.5f, true).apply { gravity = Gravity.CENTER })
        credits.addView(ui.text(getString(R.string.credit_title), 12.5f, false, ui.p.mut).apply { gravity = Gravity.CENTER })
        credits.addView(ui.text(getString(R.string.credit_copy), 12f, false, ui.p.mut).apply { gravity = Gravity.CENTER })
        root.addView(credits, FrameLayout.LayoutParams(FrameLayout.LayoutParams.MATCH_PARENT,
            FrameLayout.LayoutParams.WRAP_CONTENT, Gravity.BOTTOM))
        setContentView(root)

        val anim = AnimationSet(true).apply {
            addAnimation(AlphaAnimation(0f, 1f))
            addAnimation(ScaleAnimation(0.85f, 1f, 0.85f, 1f, ScaleAnimation.RELATIVE_TO_SELF, 0.5f,
                ScaleAnimation.RELATIVE_TO_SELF, 0.5f))
            duration = 900
        }
        center.startAnimation(anim)
        credits.startAnimation(AlphaAnimation(0f, 1f).apply { duration = 1400 })

        Handler(Looper.getMainLooper()).postDelayed({
            val next = Intent(this, MainActivity::class.java)
            intent?.extras?.let { next.putExtras(it) }
            startActivity(next)
            overridePendingTransition(android.R.anim.fade_in, android.R.anim.fade_out)
            finish()
        }, 2400)
    }
}
