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
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import java.time.Instant

class MoodleSyncWorker(
    appContext: Context,
    workerParameters: WorkerParameters,
) : CoroutineWorker(appContext, workerParameters) {
    override suspend fun doWork(): Result {
        val database = UthDatabase.get(applicationContext)
        val dao = database.dao()

        val initialSnapshot = syncMutex.withLock {
            val settings = SettingsStore(applicationContext).read()
            if (!settings.enabled) return Result.success()
            val credential = CredentialVault(applicationContext).read()
            if (credential == null) {
                dao.setState(SyncStateEntity("token_status", "missing"))
                return Result.failure()
            }
            val startedAt = Instant.now().epochSecond
            dao.setState(SyncStateEntity("last_attempt_at", startedAt.toString()))
            val previous = dao.activities().associateBy { it.activityKey }
            Triple(settings, credential, previous)
        }
        val (settings, credential, previous) = initialSnapshot
        val generation = System.currentTimeMillis()

        return try {
            val discovery = MoodleWsClient(credential).fetchActivities(settings, generation)
            syncMutex.withLock {
                // Re-verify active credentials & settings after HTTP fetch so a concurrent
                // logout or account switch cannot be overwritten by stale Room rows or alarms.
                val currentSettings = SettingsStore(applicationContext).read()
                val activeCredential = CredentialVault(applicationContext).read()
                if (!currentSettings.enabled || activeCredential != credential) {
                    return@withLock Result.success()
                }
                val latestPrevious = dao.activities().associateBy { it.activityKey }
                val mergedActivities = discovery.activities.map { activity ->
                    val old = latestPrevious[activity.activityKey] ?: previous[activity.activityKey]
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
                    previous = latestPrevious,
                    allowImmediate = true,
                )
                dao.setState(SyncStateEntity("last_scheduled", reconciled.scheduled.toString()))
                dao.setState(SyncStateEntity("last_cancelled", reconciled.cancelled.toString()))
                dao.setState(SyncStateEntity("last_delivered", reconciled.delivered.toString()))
                Result.success()
            }
        } catch (exception: MoodleWsException) {
            syncMutex.withLock {
                val activeCredential = CredentialVault(applicationContext).read()
                if (activeCredential != credential) {
                    return@withLock Result.success()
                }
                val invalidToken = exception.errorCode in setOf("invalidtoken", "accessexception")
                dao.setState(SyncStateEntity("token_status", if (invalidToken) "expired" else "error"))
                dao.setState(SyncStateEntity("last_error", exception.message.orEmpty().take(500)))
                if (invalidToken) {
                    NotificationDispatcher(applicationContext).showAuthenticationRequired()
                }
                if (invalidToken) Result.failure() else Result.retry()
            }
        } catch (cancelled: CancellationException) {
            throw cancelled
        } catch (exception: Exception) {
            syncMutex.withLock {
                val activeCredential = CredentialVault(applicationContext).read()
                if (activeCredential != credential) {
                    return@withLock Result.success()
                }
                dao.setState(SyncStateEntity("last_error", exception.javaClass.simpleName))
                Result.retry()
            }
        }
    }

    companion object {
        internal val syncMutex = Mutex()
    }
}
