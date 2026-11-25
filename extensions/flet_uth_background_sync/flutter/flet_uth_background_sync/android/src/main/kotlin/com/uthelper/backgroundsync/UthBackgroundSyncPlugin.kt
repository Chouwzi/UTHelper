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
private val ANDROID_PACKAGE_NAME_REGEX =
    Regex("^[A-Za-z][A-Za-z0-9_]*(\\.[A-Za-z][A-Za-z0-9_]*)+$")
private val RAW_AUTHORITY_REGEX =
    Regex("^[a-z][a-z0-9+.-]*://([^/?#]+)", RegexOption.IGNORE_CASE)
private val ALLOWED_EXTERNAL_SCHEMES = setOf(
    "https",
    "market",
    "zalo",
    "zoomus",
    "zoomphonecall",
    "zoom",
    "msteams",
    "mailto",
    "tel",
    "sms",
    "tg",
    "fb-messenger",
    "whatsapp",
    "viber",
    "skype",
    "slack",
    "discord",
    "googledocs",
    "googlesheets",
    "googleslides",
    "googlemeet",
    "googleclassroom",
    "googledrive",
    "vnd.youtube",
    "ms-word",
    "ms-excel",
    "ms-powerpoint",
    "ms-onenote",
    "ms-outlook",
    "onedrive",
)
private val BLOCKED_SYSTEM_TARGET_PACKAGES = setOf(
    "android",
    "com.android.settings",
    "com.android.packageinstaller",
    "com.google.android.packageinstaller",
    "com.android.shell",
)
private val BLOCKED_HTTPS_BROWSER_PACKAGES = BLOCKED_SYSTEM_TARGET_PACKAGES + setOf(
    "com.android.chrome",
    "com.chrome.beta",
    "com.chrome.dev",
    "com.chrome.canary",
    "com.sec.android.app.sbrowser",
    "com.sec.android.app.sbrowser.beta",
    "org.mozilla.firefox",
    "org.mozilla.firefox_beta",
    "org.mozilla.fenix",
    "com.microsoft.emmx",
    "com.brave.browser",
    "com.opera.browser",
    "com.opera.mini.native",
    "com.vivaldi.browser",
    "com.duckduckgo.mobile.android",
    "com.ucmobile.intl",
    "com.android.browser",
)

private fun isUnsafeIpv4Octets(a: Int, b: Int, c: Int, d: Int): Boolean {
    if (a == 0 || a == 10 || a == 127 || a >= 224) return true
    if (a == 100 && b in 64..127) return true
    if (a == 169 && b == 254) return true
    if (a == 172 && b in 16..31) return true
    if (a == 192 && b == 168) return true
    if (a == 192 && b == 0 && (c == 0 || c == 2)) return true
    if (a == 198 && (b == 18 || b == 19)) return true
    if (a == 198 && b == 51 && c == 100) return true
    if (a == 203 && b == 0 && c == 113) return true
    return false
}

private fun parseIpv4Long(host: String): Long? {
    if (host.isEmpty() || host.startsWith('.') || host.endsWith('.')) return null
    val parts = host.split('.')
    if (parts.isEmpty() || parts.size > 4) return null
    val values = LongArray(parts.size)
    for ((idx, part) in parts.withIndex()) {
        if (part.isEmpty()) return null
        val lower = part.lowercase(Locale.ROOT)
        val parsed: Long = when {
            lower.startsWith("0x") -> {
                val digits = lower.substring(2)
                if (digits.isEmpty() || digits.any { it !in '0'..'9' && it !in 'a'..'f' }) {
                    return null
                }
                digits.toLongOrNull(16) ?: return null
            }
            lower.length > 1 && lower.startsWith("0") -> {
                if (lower.any { it !in '0'..'7' }) return null
                lower.toLongOrNull(8) ?: return null
            }
            else -> {
                if (lower.any { it !in '0'..'9' }) return null
                lower.toLongOrNull(10) ?: return null
            }
        }
        if (parsed < 0L) return null
        values[idx] = parsed
    }
    return when (values.size) {
        1 -> if (values[0] <= 0xFFFFFFFFL) values[0] else null
        2 -> if (values[0] <= 0xFFL && values[1] <= 0xFFFFFFL) (values[0] shl 24) or values[1] else null
        3 -> if (values[0] <= 0xFFL && values[1] <= 0xFFL && values[2] <= 0xFFFFL) {
            (values[0] shl 24) or (values[1] shl 16) or values[2]
        } else {
            null
        }
        else -> if (values.all { it <= 0xFFL }) {
            (values[0] shl 24) or (values[1] shl 16) or (values[2] shl 8) or values[3]
        } else {
            null
        }
    }
}

private fun parseIpv6Bytes(rawHost: String): IntArray? {
    var addr = rawHost.trim()
    if (addr.startsWith('[') && addr.endsWith(']')) {
        addr = addr.substring(1, addr.length - 1)
    }
    val zoneIdx = addr.indexOf('%')
    if (zoneIdx >= 0) {
        addr = addr.substring(0, zoneIdx)
    }
    if (addr.isEmpty()) return null

    val lastColon = addr.lastIndexOf(':')
    if (lastColon >= 0 && '.' in addr.substring(lastColon + 1)) {
        val ipv4 = parseIpv4Long(addr.substring(lastColon + 1)) ?: return null
        val hi = ((ipv4 ushr 16) and 0xFFFFL).toString(16)
        val lo = (ipv4 and 0xFFFFL).toString(16)
        addr = "${addr.substring(0, lastColon + 1)}$hi:$lo"
    }

    val doubleColonParts = addr.split("::")
    if (doubleColonParts.size > 2) return null

    fun parseGroups(segment: String): List<Int>? {
        if (segment.isEmpty()) return emptyList()
        val out = ArrayList<Int>()
        for (g in segment.split(':')) {
            if (g.isEmpty() || g.length > 4 || g.any {
                    it !in '0'..'9' && it !in 'a'..'f' && it !in 'A'..'F'
                }
            ) {
                return null
            }
            val v = g.toIntOrNull(16) ?: return null
            if (v !in 0..0xFFFF) return null
            out.add(v)
        }
        return out
    }

    val groups = ArrayList<Int>(8)
    if (doubleColonParts.size == 2) {
        val left = parseGroups(doubleColonParts[0]) ?: return null
        val right = parseGroups(doubleColonParts[1]) ?: return null
        val missing = 8 - (left.size + right.size)
        if (missing < 1) return null
        groups.addAll(left)
        repeat(missing) { groups.add(0) }
        groups.addAll(right)
    } else {
        val full = parseGroups(addr) ?: return null
        if (full.size != 8) return null
        groups.addAll(full)
    }

    val bytes = IntArray(16)
    for (i in 0 until 8) {
        bytes[i * 2] = (groups[i] ushr 8) and 0xFF
        bytes[i * 2 + 1] = groups[i] and 0xFF
    }
    return bytes
}

private fun isUnsafeHttpsHost(rawHost: String): Boolean {
    var host = rawHost.trim().lowercase(Locale.ROOT)
    if (host.startsWith('[') && host.endsWith(']')) {
        host = host.substring(1, host.length - 1)
    }
    host = host.trimEnd('.')
    if (host.isEmpty() ||
        host == "localhost" ||
        host.endsWith(".localhost") ||
        host.endsWith(".local")
    ) {
        return true
    }
    if (':' in host) {
        val bytes = parseIpv6Bytes(host) ?: return true
        val isV4Mapped = (0 until 10).all { bytes[it] == 0 } &&
            bytes[10] == 0xFF &&
            bytes[11] == 0xFF
        if (isV4Mapped) {
            return isUnsafeIpv4Octets(bytes[12], bytes[13], bytes[14], bytes[15])
        }
        if ((0 until 12).all { bytes[it] == 0 }) return true
        if (bytes[0] == 0xFE && (bytes[1] and 0xC0) == 0x80) return true
        if (bytes[0] == 0xFE && (bytes[1] and 0xC0) == 0xC0) return true
        if ((bytes[0] and 0xFE) == 0xFC) return true
        if (bytes[0] == 0xFF) return true
        if (bytes[0] == 0x20 && bytes[1] == 0x01 && bytes[2] == 0x0D && bytes[3] == 0xB8) {
            return true
        }
        if ((bytes[0] and 0xE0) != 0x20) return true
        return false
    }
    val ipv4 = parseIpv4Long(host)
    if (ipv4 != null) {
        return isUnsafeIpv4Octets(
            ((ipv4 ushr 24) and 0xFFL).toInt(),
            ((ipv4 ushr 16) and 0xFFL).toInt(),
            ((ipv4 ushr 8) and 0xFFL).toInt(),
            (ipv4 and 0xFFL).toInt(),
        )
    }
    return false
}

internal fun isSafePublicHttpsUri(uri: Uri, rawUriString: String? = null): Boolean {
    if (rawUriString != null) {
        if (rawUriString.isEmpty() ||
            '\\' in rawUriString ||
            rawUriString.any { it.code <= 0x20 || it.code == 0x7F }
        ) {
            return false
        }
        val rawAuth = RAW_AUTHORITY_REGEX.find(rawUriString)?.groupValues?.getOrNull(1).orEmpty()
        if (rawAuth.isEmpty() || '@' in rawAuth) {
            return false
        }
    }
    if (uri.scheme?.lowercase(Locale.ROOT) != "https") return false
    if (!uri.userInfo.isNullOrEmpty()) return false
    val encodedAuth = uri.encodedAuthority.orEmpty()
    if (encodedAuth.isEmpty() ||
        '@' in encodedAuth ||
        '\\' in encodedAuth ||
        encodedAuth.any { it.code <= 0x20 || it.code == 0x7F }
    ) {
        return false
    }
    val host = uri.host.orEmpty()
    if (host.isEmpty() || isUnsafeHttpsHost(host)) return false
    return true
}

private fun isValidExternalTargetPackage(
    pkg: String,
    ownPackageName: String,
    forHttpsScheme: Boolean,
): Boolean {
    if (pkg.isEmpty() ||
        pkg.equals(ownPackageName, ignoreCase = true) ||
        !ANDROID_PACKAGE_NAME_REGEX.matches(pkg)
    ) {
        return false
    }
    val lowered = pkg.lowercase(Locale.ROOT)
    val blocked = if (forHttpsScheme) BLOCKED_HTTPS_BROWSER_PACKAGES else BLOCKED_SYSTEM_TARGET_PACKAGES
    return lowered !in blocked
}

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
    val parsedPkg = parsed.`package`?.trim().orEmpty()
    val parsedAction = parsed.action.orEmpty()

    // Immediately scrub redirect/component/extras state on the parsed carrier as defense-in-depth.
    parsed.component = null
    parsed.selector = null
    parsed.clipData = null
    parsed.replaceExtras(null as Bundle?)

    val scheme = dataUri.scheme?.lowercase(Locale.ROOT).orEmpty()
    if (scheme !in ALLOWED_EXTERNAL_SCHEMES) {
        return null
    }
    if (scheme == "market" && dataUri.host?.lowercase(Locale.ROOT) != "details") {
        return null
    }

    if (scheme == "https") {
        if (!isSafePublicHttpsUri(dataUri)) {
            return null
        }
        // Unwrap Firebase Dynamic Links (e.g. meet.app.goo.gl/?link=https://meet.google.com/...&apn=com.google.android.apps.tachyon)
        // directly to the target app package so startActivity fails cleanly with ActivityNotFoundException
        // when the target app is not installed (instead of succeeding against always-installed GMS).
        if (parsedPkg == GMS_PACKAGE || parsedAction == FIREBASE_DYNAMIC_LINK_ACTION) {
            val innerLink = dataUri.getQueryParameter("link")?.trim().orEmpty()
            val apn = dataUri.getQueryParameter("apn")?.trim().orEmpty()
            if (!isValidExternalTargetPackage(apn, ownPackageName, forHttpsScheme = true)) {
                return null
            }
            val innerUri = try {
                Uri.parse(innerLink)
            } catch (_: Exception) {
                null
            }
            if (innerUri == null || !isSafePublicHttpsUri(innerUri, innerLink)) {
                return null
            }
            return Intent(Intent.ACTION_VIEW, innerUri).apply {
                addCategory(Intent.CATEGORY_BROWSABLE)
                setPackage(apn)
                flags = Intent.FLAG_ACTIVITY_NEW_TASK
            }
        }
    }

    if (parsedPkg.isNotEmpty() &&
        !isValidExternalTargetPackage(parsedPkg, ownPackageName, forHttpsScheme = (scheme == "https"))
    ) {
        return null
    }

    var flags = Intent.FLAG_ACTIVITY_NEW_TASK
    if (scheme == "https" &&
        parsedPkg.isEmpty() &&
        Build.VERSION.SDK_INT >= Build.VERSION_CODES.R
    ) {
        flags = flags or Intent.FLAG_ACTIVITY_REQUIRE_NON_BROWSER
    }

    return Intent(Intent.ACTION_VIEW, dataUri).apply {
        addCategory(Intent.CATEGORY_BROWSABLE)
        if (parsedPkg.isNotEmpty()) {
            setPackage(parsedPkg)
        }
        this.flags = flags
    }
}
