package com.uthelper.backgroundsync.notification

import android.app.AlarmManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.os.Build
import com.uthelper.backgroundsync.data.ReminderEntity

class AlarmScheduler(private val context: Context) {
    private val manager = context.getSystemService(AlarmManager::class.java)

    fun schedule(reminder: ReminderEntity) {
        val operation = pendingIntent(reminder, PendingIntent.FLAG_UPDATE_CURRENT) ?: return
        val triggerAtMillis = reminder.scheduledAtEpochSeconds * 1000
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S && manager.canScheduleExactAlarms()) {
            manager.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, triggerAtMillis, operation)
        } else if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            manager.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, triggerAtMillis, operation)
        } else {
            manager.set(AlarmManager.RTC_WAKEUP, triggerAtMillis, operation)
        }
    }

    fun cancel(reminder: ReminderEntity) {
        manager.cancel(pendingIntent(reminder, PendingIntent.FLAG_NO_CREATE) ?: return)
    }

    fun exactAlarmAllowed(): Boolean =
        Build.VERSION.SDK_INT < Build.VERSION_CODES.S || manager.canScheduleExactAlarms()

    private fun pendingIntent(reminder: ReminderEntity, flag: Int): PendingIntent? {
        val intent = Intent(context, DeadlineAlarmReceiver::class.java).apply {
            action = ACTION_DEADLINE
            putExtra(EXTRA_ACTIVITY_KEY, reminder.activityKey)
            putExtra(EXTRA_MILESTONE, reminder.milestoneMinutes)
            putExtra(EXTRA_REVISION, reminder.revision)
        }
        return PendingIntent.getBroadcast(
            context,
            reminder.notificationId,
            intent,
            flag or PendingIntent.FLAG_IMMUTABLE,
        )
    }

    companion object {
        const val ACTION_DEADLINE = "com.uthelper.backgroundsync.DEADLINE"
        const val EXTRA_ACTIVITY_KEY = "activity_key"
        const val EXTRA_MILESTONE = "milestone_minutes"
        const val EXTRA_REVISION = "revision"
    }
}
