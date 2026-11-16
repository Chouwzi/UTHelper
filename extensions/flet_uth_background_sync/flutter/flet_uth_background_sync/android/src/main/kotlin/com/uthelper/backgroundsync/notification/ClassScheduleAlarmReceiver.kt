package com.uthelper.backgroundsync.notification

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

class ClassScheduleAlarmReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != ClassScheduleAlarmScheduler.ACTION_CLASS_SCHEDULE) return
        val reminderId = intent.getStringExtra(ClassScheduleAlarmScheduler.KEY_REMINDER_ID).orEmpty()
        val startAt = intent.getLongExtra(ClassScheduleAlarmScheduler.KEY_START_AT, 0L)
        if (startAt > System.currentTimeMillis() / 1000L) {
            NotificationDispatcher(context).showClassSchedule(
                notificationId = reminderId.hashCode() and 0x7fffffff,
                subject = intent.getStringExtra("subject").orEmpty(),
                courseCode = intent.getStringExtra("course_code").orEmpty(),
                room = intent.getStringExtra("room").orEmpty(),
                campus = intent.getStringExtra("campus").orEmpty(),
                note = intent.getStringExtra("note").orEmpty(),
                deliveryMode = intent.getStringExtra("delivery_mode").orEmpty(),
                leadHours = intent.getIntExtra("lead_hours", 1),
                startAtEpochSeconds = startAt,
            )
        }
        ClassScheduleAlarmScheduler(context).remove(reminderId)
    }
}
