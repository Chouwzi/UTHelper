package com.uthelper.backgroundsync

import android.app.Activity
import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import java.util.Locale
import androidx.room.withTransaction
import com.uthelper.backgroundsync.data.SettingsStore
import com.uthelper.backgroundsync.data.ForegroundActivityImporter
import com.uthelper.backgroundsync.data.SyncStateEntity
import com.uthelper.backgroundsync.data.UthDatabase
import com.uthelper.backgroundsync.notification.AlarmScheduler
import com.uthelper.backgroundsync.notification.ClassScheduleAlarmScheduler
import com.uthelper.backgroundsync.notification.NotificationReconciler
import com.uthelper.backgroundsync.security.AppSecretVault
import com.uthelper.backgroundsync.security.CredentialVault
import com.uthelper.backgroundsync.worker.BackgroundSyncScheduler
import com.uthelper.backgroundsync.worker.MoodleSyncWorker
import io.flutter.embedding.engine.plugins.FlutterPlugin
import io.flutter.embedding.engine.plugins.activity.ActivityAware
import io.flutter.embedding.engine.plugins.activity.ActivityPluginBinding
import io.flutter.plugin.common.MethodCall
import io.flutter.plugin.common.MethodChannel
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext

class UthBackgroundSyncPlugin : FlutterPlugin, ActivityAware, MethodChannel.MethodCallHandler {
    private lateinit var context: Context
    @Volatile
    private var activity: Activity? = null
    private lateinit var channel: MethodChannel
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val mainHandler = Handler(Looper.getMainLooper())
    private val updateBridge: UpdateBridgeDelegate by lazy { UpdateBridgeFactory.create(context) }

    override fun onAttachedToEngine(binding: FlutterPlugin.FlutterPluginBinding) {
        context = binding.applicationContext
        channel = MethodChannel(binding.binaryMessenger, CHANNEL_NAME)
        channel.setMethodCallHandler(this)
    }

    override fun onDetachedFromEngine(binding: FlutterPlugin.FlutterPluginBinding) {
        activity = null
        if (::context.isInitialized) {
            updateBridge.cancel()
        }
        channel.setMethodCallHandler(null)
        scope.cancel()
    }

    override fun onAttachedToActivity(binding: ActivityPluginBinding) {
        activity = binding.activity
    }

    override fun onDetachedFromActivityForConfigChanges() {
        activity = null
    }

    override fun onReattachedToActivityForConfigChanges(binding: ActivityPluginBinding) {
        activity = binding.activity
    }

    override fun onDetachedFromActivity() {
        activity = null
    }

    override fun onMethodCall(call: MethodCall, result: MethodChannel.Result) {
        when (call.method) {
            "configure" -> execute(result) {
                MoodleSyncWorker.syncMutex.withLock {
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
            }
            "set_credentials" -> execute(result) {
                MoodleSyncWorker.syncMutex.withLock {
                    CredentialVault(context).write(
                        call.argument<String>("base_url").orEmpty(),
                        call.argument<String>("token").orEmpty(),
                    )
                    null
                }
            }
            "read_app_secrets" -> execute(result) {
                AppSecretVault(context).readAll()
            }
            "write_app_secrets" -> execute(result) {
                val raw = call.argument<Map<*, *>>("secrets") ?: emptyMap<Any, Any>()
                val sanitized = linkedMapOf<String, String>()
                for ((k, v) in raw) {
                    val key = k?.toString()?.trim().orEmpty()
                    val value = v?.toString().orEmpty()
                    if (key.isNotEmpty() && value.isNotBlank()) {
                        sanitized[key] = value
                    }
                }
                AppSecretVault(context).writeAll(sanitized)
                true
            }
            "clear_app_secrets" -> execute(result) {
                AppSecretVault(context).clear()
                true
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
            "schedule_class_sessions" -> execute(result) {
                val sessions = call.argument<List<*>>("sessions") ?: emptyList<Any>()
                val enabled = call.argument<Boolean>("enabled") ?: true
                ClassScheduleAlarmScheduler(context).reconcile(sessions, enabled)
            }
            "import_activities" -> execute(result) {
                MoodleSyncWorker.syncMutex.withLock {
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
                MoodleSyncWorker.syncMutex.withLock {
                    val reconciled = NotificationReconciler(context).reconcile(allowImmediate = false)
                    mapOf(
                        "desired" to reconciled.desired,
                        "scheduled" to reconciled.scheduled,
                        "cancelled" to reconciled.cancelled,
                        "delivered" to reconciled.delivered,
                    )
                }
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
            "launch_external_intent" -> {
                val rawUri = call.argument<String>("uri").orEmpty()
                val safeIntent = buildSafeExternalIntent(rawUri, context.packageName)
                if (safeIntent == null) {
                    result.success(false)
                } else {
                    mainHandler.post {
                        val hostContext = activity ?: context
                        val launched = try {
                            hostContext.startActivity(safeIntent)
                            true
                        } catch (_: ActivityNotFoundException) {
                            false
                        } catch (_: SecurityException) {
                            false
                        } catch (_: Exception) {
                            false
                        }
                        result.success(launched)
                    }
                }
            }
            "logout" -> execute(result) {
                MoodleSyncWorker.syncMutex.withLock {
                    BackgroundSyncScheduler(context).cancelAll()
                    // Portal class-schedule alarms are deliberately NOT cancelled
                    // here: AndroidBackgroundBridge.configure() calls logout()
                    // whenever the Moodle token is missing, which would wipe a
                    // freshly armed class schedule. Class reminders are cleared
                    // only through "schedule_class_sessions" with enabled=false.
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
            }
            else -> {
                if (!updateBridge.handleMethodCall(call, result, ::execute)) {
                    result.notImplemented()
                }
            }
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

    companion object {
        private const val CHANNEL_NAME = "com.uthelper/background_sync"
    }
}

private const val FIREBASE_DYNAMIC_LINK_ACTION =
    "com.google.firebase.dynamiclinks.VIEW_DYNAMIC_LINK"
private const val GMS_PACKAGE = "com.google.android.gms"
private val BLOCKED_EXTERNAL_SCHEMES = setOf(
    "",
    "javascript",
    "data",
    "file",
    "content",
    "about",
    "blob",
    "vbscript",
    "jar",
    "filesystem",
    "chrome",
    "chrome-extension",
    "devtools",
    "view-source",
    "intent",
)

internal fun buildSafeExternalIntent(rawUri: String, ownPackageName: String): Intent? {
    val trimmed = rawUri.trim()
    if (trimmed.isEmpty() || trimmed.any { it.code <= 0x20 || it.code == 0x7F }) {
        return null
    }
    val parsed: Intent = try {
        if (trimmed.startsWith("intent://", ignoreCase = true)) {
            Intent.parseUri(trimmed, Intent.URI_INTENT_SCHEME)
        } else {
            Intent(Intent.ACTION_VIEW, Uri.parse(trimmed))
        }
    } catch (_: Exception) {
        return null
    }

    val dataUri = parsed.data ?: return null
    val scheme = dataUri.scheme?.lowercase(Locale.ROOT).orEmpty()
    if (scheme in BLOCKED_EXTERNAL_SCHEMES) {
        return null
    }

    // Unwrap Firebase Dynamic Links (e.g. meet.app.goo.gl/?link=https://meet.google.com/...&apn=com.google.android.apps.tachyon)
    // directly to the target app package so startActivity fails cleanly with ActivityNotFoundException
    // when the target app is not installed (instead of succeeding against always-installed GMS).
    if ((parsed.`package` == GMS_PACKAGE || parsed.action == FIREBASE_DYNAMIC_LINK_ACTION) &&
        scheme == "https"
    ) {
        val innerLink = dataUri.getQueryParameter("link")?.trim().orEmpty()
        val apn = dataUri.getQueryParameter("apn")?.trim().orEmpty()
        if (innerLink.isNotEmpty() &&
            apn.isNotEmpty() &&
            !apn.equals(ownPackageName, ignoreCase = true)
        ) {
            val innerUri = try {
                Uri.parse(innerLink)
            } catch (_: Exception) {
                null
            }
            if (innerUri != null &&
                innerUri.scheme?.lowercase(Locale.ROOT) == "https" &&
                !innerUri.host.isNullOrEmpty()
            ) {
                return Intent(Intent.ACTION_VIEW, innerUri).apply {
                    addCategory(Intent.CATEGORY_BROWSABLE)
                    setPackage(apn)
                    flags = Intent.FLAG_ACTIVITY_NEW_TASK
                }
            }
        }
    }

    parsed.action = Intent.ACTION_VIEW
    parsed.addCategory(Intent.CATEGORY_BROWSABLE)
    parsed.component = null
    parsed.selector = null
    parsed.clipData = null
    parsed.replaceExtras(null as Bundle?)
    var flags = Intent.FLAG_ACTIVITY_NEW_TASK
    if ((scheme == "http" || scheme == "https") &&
        parsed.`package`.isNullOrEmpty() &&
        Build.VERSION.SDK_INT >= Build.VERSION_CODES.R
    ) {
        flags = flags or Intent.FLAG_ACTIVITY_REQUIRE_NON_BROWSER
    }
    parsed.flags = flags

    val targetPkg = parsed.`package`
    if (!targetPkg.isNullOrEmpty() && targetPkg.equals(ownPackageName, ignoreCase = true)) {
        return null
    }
    return parsed
}
