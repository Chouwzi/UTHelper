package com.uthelper.backgroundsync.notification

import android.content.Context
import com.uthelper.backgroundsync.data.ActivityEntity
import com.uthelper.backgroundsync.data.DeliveredMilestoneEntity
import com.uthelper.backgroundsync.data.ReminderEntity
import com.uthelper.backgroundsync.data.SettingsStore
import com.uthelper.backgroundsync.data.UthDatabase

data class ReconcileResult(
    val desired: Int,
    val scheduled: Int,
    val cancelled: Int,
    val delivered: Int,
)

class NotificationReconciler(private val context: Context) {
    private val database = UthDatabase.get(context)
    private val dao = database.dao()
    private val scheduler = AlarmScheduler(context)
    private val dispatcher = NotificationDispatcher(context)

    suspend fun reconcile(
        previous: Map<String, ActivityEntity> = emptyMap(),
        allowImmediate: Boolean = false,
    ): ReconcileResult {
        val now = System.currentTimeMillis() / 1000
        val activities = dao.activities()
        val policy = NotificationPolicy(SettingsStore(context).read())
        val desired = policy.desired(activities, now).associateBy { it.notificationId }
        val existing = dao.reminders().associateBy { it.notificationId }
        var cancelled = 0
        var scheduled = 0
        for ((id, reminder) in existing) {
            val wanted = desired[id]
            if (wanted == null || wanted.scheduledAtEpochSeconds != reminder.scheduledAtEpochSeconds ||
                wanted.activity.revision != reminder.revision
            ) {
                scheduler.cancel(reminder)
                dao.deleteReminder(id)
                cancelled++
            }
        }
        for ((id, wanted) in desired) {
            val old = existing[id]
            if (old != null && old.scheduledAtEpochSeconds == wanted.scheduledAtEpochSeconds &&
                old.revision == wanted.activity.revision
            ) continue
            val reminder = ReminderEntity(
                notificationId = id,
                activityKey = wanted.activity.activityKey,
                milestoneMinutes = wanted.milestoneMinutes,
                revision = wanted.activity.revision,
                scheduledAtEpochSeconds = wanted.scheduledAtEpochSeconds,
            )
            scheduler.schedule(reminder)
            dao.upsertReminder(reminder)
            scheduled++
        }

        var delivered = 0
        if (allowImmediate) {
            for (activity in activities) {
                val old = previous[activity.activityKey]
                if (old != null && old.revision == activity.revision) continue
                val milestone = policy.dueMilestone(activity, now) ?: continue
                val key = deliveryKey(activity, milestone)
                if (dao.wasDelivered(key)) continue
                if (dispatcher.show(activity, milestone)) {
                    dao.markDelivered(
                        DeliveredMilestoneEntity(
                            deliveryKey = key,
                            activityKey = activity.activityKey,
                            revision = activity.revision,
                            milestoneMinutes = milestone,
                            deliveredAtEpochSeconds = now,
                        )
                    )
                    delivered++
                }
            }
        }
        dao.pruneDelivered(now - DELIVERED_TTL_SECONDS)
        return ReconcileResult(desired.size, scheduled, cancelled, delivered)
    }

    suspend fun reschedulePersisted() {
        val now = System.currentTimeMillis() / 1000
        dao.reminders().filter { it.scheduledAtEpochSeconds > now }.forEach(scheduler::schedule)
        reconcile(allowImmediate = false)
    }

    companion object { private const val DELIVERED_TTL_SECONDS = 180L * 24L * 3600L }
}
