package com.uthelper.backgroundsync.worker

import android.content.Context
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import java.time.Duration
import java.util.concurrent.TimeUnit
import kotlin.random.Random

class BackgroundSyncScheduler(context: Context) {
    private val workManager = WorkManager.getInstance(context.applicationContext)

    fun schedulePeriodic(intervalMinutes: Int) {
        require(intervalMinutes in ALLOWED_INTERVALS)
        val interval = Duration.ofMinutes(intervalMinutes.toLong())
        val flex = Duration.ofMinutes(minOf(30L, maxOf(15L, intervalMinutes / 4L)))
        val request = PeriodicWorkRequestBuilder<MoodleSyncWorker>(interval, flex)
            .setConstraints(networkConstraints())
            .setInitialDelay(Random.nextLong(0, 11), TimeUnit.MINUTES)
            .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, Duration.ofMinutes(15))
            .addTag(PERIODIC_WORK_NAME)
            .build()
        workManager.enqueueUniquePeriodicWork(
            PERIODIC_WORK_NAME,
            ExistingPeriodicWorkPolicy.UPDATE,
            request,
        )
    }

    fun syncNow(force: Boolean): String {
        val request = OneTimeWorkRequestBuilder<MoodleSyncWorker>()
            .setConstraints(networkConstraints())
            .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, Duration.ofMinutes(15))
            .addTag(IMMEDIATE_WORK_NAME)
            .build()
        workManager.enqueueUniqueWork(
            IMMEDIATE_WORK_NAME,
            if (force) ExistingWorkPolicy.REPLACE else ExistingWorkPolicy.KEEP,
            request,
        )
        return request.id.toString()
    }

    fun cancelPeriodic() = workManager.cancelUniqueWork(PERIODIC_WORK_NAME)

    fun cancelAll() {
        workManager.cancelUniqueWork(PERIODIC_WORK_NAME)
        workManager.cancelUniqueWork(IMMEDIATE_WORK_NAME)
    }

    fun diagnostics(): Map<String, Any> = runCatching {
        val info = workManager.getWorkInfosForUniqueWork(PERIODIC_WORK_NAME)
            .get()
            .maxByOrNull { it.generation }
        mapOf(
            "worker_state" to (info?.state?.name ?: "NOT_SCHEDULED"),
            "worker_stop_reason" to (info?.stopReason ?: 0),
            "worker_run_attempt_count" to (info?.runAttemptCount ?: 0),
            "worker_next_schedule_at_ms" to (info?.nextScheduleTimeMillis ?: 0L),
        )
    }.getOrElse { mapOf("worker_diagnostics_error" to it.javaClass.simpleName) }

    private fun networkConstraints() = Constraints.Builder()
        .setRequiredNetworkType(NetworkType.CONNECTED)
        .build()

    companion object {
        val ALLOWED_INTERVALS = setOf(60, 180, 360)
        const val PERIODIC_WORK_NAME = "uth_activity_sync_v1"
        const val IMMEDIATE_WORK_NAME = "uth_activity_sync_now_v1"
    }
}
