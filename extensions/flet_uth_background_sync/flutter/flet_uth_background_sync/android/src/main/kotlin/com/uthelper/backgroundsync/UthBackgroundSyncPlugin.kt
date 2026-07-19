package com.uthelper.backgroundsync

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import androidx.room.withTransaction
import com.uthelper.backgroundsync.data.SettingsStore
import com.uthelper.backgroundsync.data.ForegroundActivityImporter
import com.uthelper.backgroundsync.data.SyncStateEntity
import com.uthelper.backgroundsync.data.UthDatabase
import com.uthelper.backgroundsync.notification.AlarmScheduler
import com.uthelper.backgroundsync.notification.NotificationReconciler
import com.uthelper.backgroundsync.security.CredentialVault
import com.uthelper.backgroundsync.update.ApkUpdateInstaller
import com.uthelper.backgroundsync.worker.BackgroundSyncScheduler
import io.flutter.embedding.engine.plugins.FlutterPlugin
import io.flutter.plugin.common.MethodCall
import io.flutter.plugin.common.MethodChannel
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

class UthBackgroundSyncPlugin : FlutterPlugin, MethodChannel.MethodCallHandler {
    private lateinit var context: Context
    private lateinit var channel: MethodChannel
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val mainHandler = Handler(Looper.getMainLooper())

    override fun onAttachedToEngine(binding: FlutterPlugin.FlutterPluginBinding) {
        context = binding.applicationContext
        channel = MethodChannel(binding.binaryMessenger, CHANNEL_NAME)
        channel.setMethodCallHandler(this)
    }

    override fun onDetachedFromEngine(binding: FlutterPlugin.FlutterPluginBinding) {
        channel.setMethodCallHandler(null)
        scope.cancel()
    }

    override fun onMethodCall(call: MethodCall, result: MethodChannel.Result) {
        when (call.method) {
            "configure" -> execute(result) {
                val raw = call.argument<Map<*, *>>("settings") ?: emptyMap<Any, Any>()
                SettingsStore(context).update(raw)
                val settings = SettingsStore(context).read()
                if (settings.enabled) {
                    BackgroundSyncScheduler(context).schedulePeriodic(settings.intervalMinutes)
                } else {
                    BackgroundSyncScheduler(context).cancelPeriodic()
                }
                val reconciled = NotificationReconciler(context).reconcile(allowImmediate = false)
                mapOf(
                    "interval_minutes" to settings.intervalMinutes,
                    "enabled" to settings.enabled,
                    "scheduled" to reconciled.scheduled,
                    "cancelled" to reconciled.cancelled,
                )
            }
            "set_credentials" -> execute(result) {
                CredentialVault(context).write(
                    call.argument<String>("base_url").orEmpty(),
                    call.argument<String>("token").orEmpty(),
                )
                null
            }
            "schedule_periodic" -> execute(result) {
                val interval = call.argument<Number>("interval_minutes")?.toInt() ?: 60
                BackgroundSyncScheduler(context).schedulePeriodic(interval)
                mapOf("interval_minutes" to interval, "scheduled" to true)
            }
            "cancel_periodic" -> execute(result) {
                BackgroundSyncScheduler(context).cancelPeriodic()
                null
            }
            "sync_now" -> execute(result) {
                BackgroundSyncScheduler(context).syncNow(
                    call.argument<Boolean>("force") ?: false
                )
            }
            "get_cached_activities" -> execute(result) {
                UthDatabase.get(context).dao().activities().map { activity ->
                    mapOf(
                        "id" to activity.activityId,
                        "activity_key" to activity.activityKey,
                        "course_id" to activity.courseId,
                        "course_name" to activity.courseName,
                        "course" to activity.courseName,
                        "title" to activity.title,
                        "type" to activity.eventType,
                        "deadline_epoch" to activity.deadlineEpochSeconds,
                        "url" to activity.url,
                        "submission_status" to activity.submissionState,
                        "graded" to activity.graded,
                        "revision" to activity.revision,
                        "source" to "android_worker_cache",
                    )
                }
            }
            "import_activities" -> execute(result) {
                val database = UthDatabase.get(context)
                val dao = database.dao()
                val previous = dao.activities().associateBy { it.activityKey }
                val generation = System.currentTimeMillis()
                val imported = ForegroundActivityImporter().parse(
                    call.argument<List<*>>("activities") ?: emptyList<Any>(),
                    generation,
                    previous,
                )
                val authoritative = call.argument<Boolean>("authoritative") ?: true
                database.withTransaction {
                    if (authoritative) {
                        dao.replaceAuthoritativeSnapshot(imported, generation)
                    } else if (imported.isNotEmpty()) {
                        dao.upsertActivities(imported)
                    }
                    dao.setState(SyncStateEntity("last_foreground_import_at", (generation / 1000).toString()))
                    dao.setState(SyncStateEntity("activity_count", imported.size.toString()))
                }
                val reconciled = NotificationReconciler(context).reconcile(
                    previous = previous,
                    allowImmediate = true,
                )
                mapOf(
                    "imported" to imported.size,
                    "authoritative" to authoritative,
                    "scheduled" to reconciled.scheduled,
                    "cancelled" to reconciled.cancelled,
                    "delivered" to reconciled.delivered,
                )
            }
            "get_diagnostics" -> execute(result) {
                val dao = UthDatabase.get(context).dao()
                dao.states().associate { it.key to it.value }.toMutableMap<String, Any>().apply {
                    put("worker_backend", "androidx.work")
                    putAll(BackgroundSyncScheduler(context).diagnostics())
                    put("exact_alarm_allowed", AlarmScheduler(context).exactAlarmAllowed())
                    put("pending_reminders", dao.reminders().size)
                    put("credential_available", CredentialVault(context).read() != null)
                }
            }
            "reconcile_cached" -> execute(result) {
                val reconciled = NotificationReconciler(context).reconcile(allowImmediate = false)
                mapOf(
                    "desired" to reconciled.desired,
                    "scheduled" to reconciled.scheduled,
                    "cancelled" to reconciled.cancelled,
                    "delivered" to reconciled.delivered,
                )
            }
            "request_exact_alarm_access" -> execute(result) {
                val scheduler = AlarmScheduler(context)
                if (scheduler.exactAlarmAllowed() || Build.VERSION.SDK_INT < Build.VERSION_CODES.S) {
                    true
                } else {
                    val intent = Intent(Settings.ACTION_REQUEST_SCHEDULE_EXACT_ALARM).apply {
                        data = Uri.parse("package:${context.packageName}")
                        addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                    }
                    context.startActivity(intent)
                    false
                }
            }
            "install_update" -> execute(result) {
                val update = ApkUpdateInstaller(context).downloadVerifyAndOpen(
                    url = call.argument<String>("url").orEmpty(),
                    expectedSha256 = call.argument<String>("sha256").orEmpty(),
                    expectedSize = call.argument<Number>("expected_size")?.toLong() ?: 0L,
                )
                mapOf(
                    "status" to update.status,
                    "bytes" to update.bytes,
                    "sha256" to update.sha256,
                )
            }
            "logout" -> execute(result) {
                BackgroundSyncScheduler(context).cancelAll()
                val database = UthDatabase.get(context)
                database.dao().reminders().forEach(AlarmScheduler(context)::cancel)
                database.withTransaction {
                    database.dao().clearReminders()
                    database.dao().clearActivities()
                    database.dao().clearState()
                }
                CredentialVault(context).clear()
                SettingsStore(context).clear()
                null
            }
            else -> result.notImplemented()
        }
    }

    private fun execute(
        result: MethodChannel.Result,
        operation: suspend () -> Any?,
    ) {
        scope.launch {
            try {
                val value = operation()
                withContext(Dispatchers.Main) { result.success(value) }
            } catch (exception: Exception) {
                mainHandler.post {
                    result.error(
                        "native_background_sync_error",
                        exception.message ?: exception.javaClass.simpleName,
                        null,
                    )
                }
            }
        }
    }

    companion object { private const val CHANNEL_NAME = "com.uthelper/background_sync" }
}
