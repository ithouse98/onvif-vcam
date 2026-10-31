package com.ipsco.vcamagent

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Build
import androidx.core.app.NotificationCompat

/** Starts the agent after a reboot when the user enabled it. Android 14+ does not allow a camera
 *  service to start from boot, so there a notification asks for one tap instead. */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(ctx: Context, intent: Intent) {
        if (intent.action != Intent.ACTION_BOOT_COMPLETED) return
        val p = Prefs(ctx)
        if (!p.bootStart || !p.enabled) return
        if (Build.VERSION.SDK_INT < 34) {
            try {
                AgentService.start(ctx)
                return
            } catch (_: Exception) { }
        }
        val nm = ctx.getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(NotificationChannel("vcam-boot", ctx.getString(R.string.app_name), NotificationManager.IMPORTANCE_DEFAULT))
        val open = PendingIntent.getActivity(ctx, 0, Intent(ctx, MainActivity::class.java).putExtra("start", true),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        nm.notify(2, NotificationCompat.Builder(ctx, "vcam-boot").setSmallIcon(R.drawable.ic_stat_camera)
            .setContentTitle(ctx.getString(R.string.app_name)).setContentText(ctx.getString(R.string.boot_tap))
            .setContentIntent(open).setAutoCancel(true).build())
    }
}
