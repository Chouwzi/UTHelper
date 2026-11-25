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

class RescheduleReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        // Only the manifest-declared system events may trigger a reschedule;
        // an arbitrary broadcast must not touch persisted alarms.
        if (intent.action !in ALLOWED_ACTIONS) return
        val pending = goAsync()
        val exceptionHandler = CoroutineExceptionHandler { _, throwable ->
            Log.e(TAG, "Unhandled reschedule error: ${throwable.javaClass.simpleName}")
        }
        CoroutineScope(SupervisorJob() + Dispatchers.IO + exceptionHandler).launch {
            try {
                runCatching { NotificationReconciler(context).reschedulePersisted() }
                    .onFailure { Log.w(TAG, "Deadline reschedule failed: ${it.javaClass.simpleName}") }
                runCatching { ClassScheduleAlarmScheduler(context).reschedulePersisted() }
                    .onFailure { Log.w(TAG, "Class schedule reschedule failed: ${it.javaClass.simpleName}") }
            } catch (throwable: Throwable) {
                Log.w(TAG, "Reschedule receiver failed: ${throwable.javaClass.simpleName}")
            } finally {
                pending.finish()
            }
        }
    }

    private companion object {
        const val TAG = "RescheduleReceiver"
        val ALLOWED_ACTIONS = setOf(
            Intent.ACTION_BOOT_COMPLETED,
            Intent.ACTION_MY_PACKAGE_REPLACED,
            Intent.ACTION_TIME_CHANGED,
            Intent.ACTION_TIMEZONE_CHANGED,
            "android.app.action.SCHEDULE_EXACT_ALARM_PERMISSION_STATE_CHANGED",
        )
    }
}
