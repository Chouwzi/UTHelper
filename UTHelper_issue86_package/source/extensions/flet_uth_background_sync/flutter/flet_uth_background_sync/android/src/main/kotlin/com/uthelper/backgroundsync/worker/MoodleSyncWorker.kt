package com.uthelper.backgroundsync.worker

import android.content.Context
import androidx.room.withTransaction
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import com.uthelper.backgroundsync.data.SettingsStore
import com.uthelper.backgroundsync.data.SyncStateEntity
import com.uthelper.backgroundsync.data.UthDatabase
import com.uthelper.backgroundsync.network.MoodleWsClient
import com.uthelper.backgroundsync.network.MoodleWsException
import com.uthelper.backgroundsync.notification.NotificationReconciler
import com.uthelper.backgroundsync.notification.NotificationDispatcher
import com.uthelper.backgroundsync.security.CredentialVault
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import java.time.Instant

class MoodleSyncWorker(
    appContext: Context,
    workerParameters: WorkerParameters,
) : CoroutineWorker(appContext, workerParameters) {
    override suspend fun doWork(): Result = syncMutex.withLock {
        val database = UthDatabase.get(applicationContext)
        val dao = database.dao()
        val settings = SettingsStore(applicationContext).read()
        if (!settings.enabled) return@withLock Result.success()
        val credential = CredentialVault(applicationContext).read()
        if (credential == null) {
            dao.setState(SyncStateEntity("token_status", "missing"))
            return@withLock Result.failure()
        }
        val startedAt = Instant.now().epochSecond
        dao.setState(SyncStateEntity("last_attempt_at", startedAt.toString()))
        return@withLock try {
            val previous = dao.activities().associateBy { it.activityKey }
            val generation = System.currentTimeMillis()
            val discovery = MoodleWsClient(credential).fetchActivities(settings, generation)
            val mergedActivities = discovery.activities.map { activity ->
                val old = previous[activity.activityKey]
                if (activity.submissionState == "unknown" && old != null) {
                    activity.copy(
                        submissionState = old.submissionState,
                        graded = old.graded,
                    )
                } else activity
            }
            database.withTransaction {
                if (discovery.authoritative) {
                    dao.replaceAuthoritativeSnapshot(mergedActivities, generation)
                } else {
                    dao.upsertActivities(mergedActivities)
                    dao.setState(SyncStateEntity("snapshot_generation", generation.toString()))
                    dao.setState(SyncStateEntity("activity_count", mergedActivities.size.toString()))
                }
                dao.setState(SyncStateEntity("last_success_at", Instant.now().epochSecond.toString()))
                dao.setState(SyncStateEntity("last_error", ""))
                dao.setState(SyncStateEntity("token_status", "valid"))
                dao.setState(SyncStateEntity("authoritative", discovery.authoritative.toString()))
            }
            val reconciled = NotificationReconciler(applicationContext).reconcile(
                previous = previous,
                allowImmediate = true,
            )
            dao.setState(SyncStateEntity("last_scheduled", reconciled.scheduled.toString()))
            dao.setState(SyncStateEntity("last_cancelled", reconciled.cancelled.toString()))
            dao.setState(SyncStateEntity("last_delivered", reconciled.delivered.toString()))
            Result.success()
        } catch (exception: MoodleWsException) {
            val invalidToken = exception.errorCode in setOf("invalidtoken", "accessexception")
            dao.setState(SyncStateEntity("token_status", if (invalidToken) "expired" else "error"))
            dao.setState(SyncStateEntity("last_error", exception.message.orEmpty().take(500)))
            if (invalidToken) {
                NotificationDispatcher(applicationContext).showAuthenticationRequired()
            }
            if (invalidToken) Result.failure() else Result.retry()
        } catch (exception: Exception) {
            dao.setState(SyncStateEntity("last_error", exception.javaClass.simpleName))
            Result.retry()
        }
    }

    companion object { private val syncMutex = Mutex() }
}
