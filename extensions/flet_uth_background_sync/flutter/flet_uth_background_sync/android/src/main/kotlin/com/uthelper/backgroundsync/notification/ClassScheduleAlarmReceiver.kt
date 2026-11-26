package com.uthelper.backgroundsync.notification

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log
import kotlinx.coroutines.CoroutineExceptionHandler
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch

class ClassScheduleAlarmReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != ClassScheduleAlarmScheduler.ACTION_CLASS_SCHEDULE) return
        val pending = goAsync()
        val reminderId = intent.getStringExtra(ClassScheduleAlarmScheduler.KEY_REMINDER_ID).orEmpty()
        val startAt = intent.getLongExtra(ClassScheduleAlarmScheduler.KEY_START_AT, 0L)
        val subject = intent.getStringExtra("subject").orEmpty()
        val courseCode = intent.getStringExtra("course_code").orEmpty()
        val room = intent.getStringExtra("room").orEmpty()
        val campus = intent.getStringExtra("campus").orEmpty()
        val note = intent.getStringExtra("note").orEmpty()
        val deliveryMode = intent.getStringExtra("delivery_mode").orEmpty()
        val leadHours = intent.getIntExtra("lead_hours", 1)
        val exceptionHandler = CoroutineExceptionHandler { _, throwable ->
            Log.e(TAG, "Unhandled class schedule alarm failure: ${throwable.javaClass.simpleName}")
        }
        CoroutineScope(SupervisorJob() + Dispatchers.IO + exceptionHandler).launch {
            try {
                val scheduler = ClassScheduleAlarmScheduler(context)
                val stillScheduled = scheduler.contains(reminderId)
                if (stillScheduled && startAt > System.currentTimeMillis() / 1000L) {
                    NotificationDispatcher(context).showClassSchedule(
                        notificationId = reminderId.hashCode() and 0x7fffffff,
                        subject = subject,
                        courseCode = courseCode,
                        room = room,
                        campus = campus,
                        note = note,
                        deliveryMode = deliveryMode,
                        leadHours = leadHours,
                        startAtEpochSeconds = startAt,
                    )
                }
                scheduler.remove(reminderId)
            } catch (throwable: Throwable) {
                Log.w(TAG, "Class schedule alarm processing failed: ${throwable.javaClass.simpleName}")
            } finally {
                pending.finish()
            }
        }
    }

    private companion object {
        const val TAG = "ClassScheduleAlarmReceiver"
    }
}
