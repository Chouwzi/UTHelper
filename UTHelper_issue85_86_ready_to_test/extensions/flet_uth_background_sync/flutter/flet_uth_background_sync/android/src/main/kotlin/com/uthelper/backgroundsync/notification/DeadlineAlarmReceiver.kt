package com.uthelper.backgroundsync.notification

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import com.uthelper.backgroundsync.data.DeliveredMilestoneEntity
import com.uthelper.backgroundsync.data.SettingsStore
import com.uthelper.backgroundsync.data.UthDatabase
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch

class DeadlineAlarmReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != AlarmScheduler.ACTION_DEADLINE) return
        val pending = goAsync()
        CoroutineScope(SupervisorJob() + Dispatchers.IO).launch {
            try {
                val dao = UthDatabase.get(context).dao()
                val activityKey = intent.getStringExtra(AlarmScheduler.EXTRA_ACTIVITY_KEY).orEmpty()
                val milestone = intent.getIntExtra(AlarmScheduler.EXTRA_MILESTONE, 0)
                val revision = intent.getStringExtra(AlarmScheduler.EXTRA_REVISION).orEmpty()
                val activity = dao.activity(activityKey) ?: return@launch
                val reminderId = stableNotificationId(activityKey, milestone)
                if (activity.revision != revision || milestone <= 0) {
                    dao.deleteReminder(reminderId)
                    return@launch
                }
                val policy = NotificationPolicy(SettingsStore(context).read())
                val now = System.currentTimeMillis() / 1000
                if (!policy.accepts(activity) || activity.deadlineEpochSeconds <= now) {
                    dao.deleteReminder(reminderId)
                    return@launch
                }
                if (policy.isDnd(now)) {
                    NotificationReconciler(context).reconcile(allowImmediate = false)
                    return@launch
                }
                val key = deliveryKey(activity, milestone)
                if (!dao.wasDelivered(key) && NotificationDispatcher(context).show(activity, milestone)) {
                    dao.markDelivered(
                        DeliveredMilestoneEntity(
                            key,
                            activity.activityKey,
                            activity.revision,
                            milestone,
                            now,
                        )
                    )
                }
                dao.deleteReminder(reminderId)
            } finally {
                pending.finish()
            }
        }
    }
}
