package com.uthelper.backgroundsync.notification

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import com.uthelper.backgroundsync.data.ActivityEntity
import kotlin.math.max

class NotificationDispatcher(private val context: Context) {
    fun show(activity: ActivityEntity, milestoneMinutes: Int): Boolean {
        if (Build.VERSION.SDK_INT >= 33 && ContextCompat.checkSelfPermission(
                context,
                Manifest.permission.POST_NOTIFICATIONS,
            ) != PackageManager.PERMISSION_GRANTED
        ) return false
        ensureChannel()
        val remaining = max(0L, (activity.deadlineEpochSeconds - System.currentTimeMillis() / 1000 + 59) / 60)
        val body = if (activity.courseName.isBlank()) {
            "Còn ${formatRemaining(remaining)}"
        } else {
            "${activity.courseName} · Còn ${formatRemaining(remaining)}"
        }
        val openIntent = Intent(Intent.ACTION_VIEW, Uri.parse(activity.url)).apply {
            flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP
        }
        val notificationId = stableNotificationId(activity.activityKey, milestoneMinutes)
        val contentIntent = PendingIntent.getActivity(
            context,
            notificationId,
            openIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val notification = NotificationCompat.Builder(context, CHANNEL_ID)
            .setSmallIcon(context.applicationInfo.icon)
            .setContentTitle(activity.title)
            .setContentText(body)
            .setStyle(NotificationCompat.BigTextStyle().bigText(body))
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setAutoCancel(true)
            .setContentIntent(contentIntent)
            .build()
        return runCatching {
            NotificationManagerCompat.from(context).notify(notificationId, notification)
            true
        }.getOrDefault(false)
    }

    fun showAuthenticationRequired(): Boolean {
        if (Build.VERSION.SDK_INT >= 33 && ContextCompat.checkSelfPermission(
                context,
                Manifest.permission.POST_NOTIFICATIONS,
            ) != PackageManager.PERMISSION_GRANTED
        ) return false
        ensureChannel()
        val launch = context.packageManager.getLaunchIntentForPackage(context.packageName)
            ?.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)
        val contentIntent = launch?.let {
            PendingIntent.getActivity(
                context,
                AUTH_NOTIFICATION_ID,
                it,
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
            )
        }
        val notification = NotificationCompat.Builder(context, CHANNEL_ID)
            .setSmallIcon(context.applicationInfo.icon)
            .setContentTitle("UTHelper cần đăng nhập lại")
            .setContentText("Token Moodle đã hết hạn. Mở ứng dụng để tiếp tục đồng bộ.")
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setAutoCancel(true)
            .setContentIntent(contentIntent)
            .build()
        return runCatching {
            NotificationManagerCompat.from(context).notify(AUTH_NOTIFICATION_ID, notification)
            true
        }.getOrDefault(false)
    }

    private fun ensureChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val manager = context.getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(
            NotificationChannel(
                CHANNEL_ID,
                "Nhắc deadline học tập",
                NotificationManager.IMPORTANCE_HIGH,
            ).apply {
                description = "Quiz, bài tập và điểm danh sắp đến hạn"
            }
        )
    }

    private fun formatRemaining(minutes: Long): String = when {
        minutes >= 1440 -> "${minutes / 1440} ngày"
        minutes >= 60 -> "${minutes / 60} giờ ${minutes % 60} phút"
        else -> "$minutes phút"
    }

    companion object {
        private const val CHANNEL_ID = "uth_deadline_reminders"
        private const val AUTH_NOTIFICATION_ID = 0x55_54_48
    }
}
