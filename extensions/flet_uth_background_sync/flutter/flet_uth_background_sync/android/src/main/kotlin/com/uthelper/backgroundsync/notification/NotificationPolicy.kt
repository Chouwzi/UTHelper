package com.uthelper.backgroundsync.notification

import com.uthelper.backgroundsync.data.ActivityEntity
import com.uthelper.backgroundsync.data.NativeSettings
import java.net.URI
import java.time.Instant
import java.time.ZoneId
import java.time.ZonedDateTime

data class DesiredReminder(
    val notificationId: Int,
    val activity: ActivityEntity,
    val milestoneMinutes: Int,
    val scheduledAtEpochSeconds: Long,
)

class NotificationPolicy(private val settings: NativeSettings) {
    fun accepts(activity: ActivityEntity): Boolean {
        if (activity.deadlineEpochSeconds <= 0) return false
        if (settings.notifyTypes.isNotEmpty() && activity.eventType !in settings.notifyTypes) {
            return false
        }
        if (settings.mutedCourses.any { it.trim().equals(activity.courseName.trim(), ignoreCase = true) }) {
            return false
        }
        if (settings.ignoreSubmitted) {
            if (isCompleted(activity)) return false
            if (isUnverifiedCoursesActivity(activity)) return false
        }
        return true
    }

    fun isDnd(epochSeconds: Long): Boolean {
        if (!settings.dndEnabled) return false
        val hour = Instant.ofEpochSecond(epochSeconds).atZone(ZoneId.systemDefault()).hour
        val start = settings.dndStartHour
        val end = settings.dndEndHour
        if (start == end) return true
        return if (start > end) hour >= start || hour < end else hour in start until end
    }

    fun desired(activities: List<ActivityEntity>, now: Long): List<DesiredReminder> {
        if (settings.dndEnabled && settings.dndStartHour == settings.dndEndHour) {
            return emptyList()
        }
        val candidates = activities.flatMap { activity ->
            if (!accepts(activity)) return@flatMap emptyList()
            settings.countdownMinutes.mapNotNull { milestone ->
                val raw = activity.deadlineEpochSeconds - milestone * 60L
                val scheduled = moveAfterDnd(raw)
                if (scheduled <= now || scheduled >= activity.deadlineEpochSeconds) null
                else DesiredReminder(
                    stableNotificationId(activity.activityKey, milestone),
                    activity,
                    milestone,
                    scheduled,
                )
            }
        }
        return candidates.groupBy { "${it.activity.activityKey}|${it.scheduledAtEpochSeconds}" }
            .values.map { values -> values.minBy { it.milestoneMinutes } }
    }

    fun dueMilestone(activity: ActivityEntity, now: Long): Int? {
        if (!accepts(activity) || activity.deadlineEpochSeconds <= now || isDnd(now)) {
            return null
        }
        val remainingMinutes = (activity.deadlineEpochSeconds - now + 59) / 60
        return settings.countdownMinutes
            .filter { it.toLong() >= remainingMinutes }
            .minOrNull()
    }

    private fun moveAfterDnd(epochSeconds: Long): Long {
        if (!isDnd(epochSeconds)) return epochSeconds
        var candidate = Instant.ofEpochSecond(epochSeconds).atZone(ZoneId.systemDefault())
            .withHour(settings.dndEndHour).withMinute(0).withSecond(0).withNano(0)
        if (settings.dndStartHour > settings.dndEndHour &&
            ZonedDateTime.ofInstant(Instant.ofEpochSecond(epochSeconds), ZoneId.systemDefault()).hour >=
            settings.dndStartHour
        ) {
            candidate = candidate.plusDays(1)
        }
        return candidate.toEpochSecond()
    }

    private fun isCompleted(activity: ActivityEntity): Boolean =
        activity.graded || isCompletedState(activity.submissionState)

    private fun isThnnActivity(activity: ActivityEntity): Boolean {
        if (activity.activityKey.startsWith("thnn:")) return true
        val host = runCatching { URI(activity.url).host?.lowercase().orEmpty() }.getOrDefault("")
        return host == "thnn.ut.edu.vn" || host.endsWith(".thnn.ut.edu.vn")
    }

    private fun isUnverifiedCoursesActivity(activity: ActivityEntity): Boolean {
        if (normalizeStateValue(activity.submissionState) !in UNVERIFIED_STATES) return false
        if (isThnnActivity(activity)) return false
        if (activity.eventType.trim().lowercase() !in VERIFIABLE_TYPES) return false
        if (resolveModuleName(activity) !in VERIFIABLE_MODULES) return false
        val host = runCatching { URI(activity.url).host?.lowercase().orEmpty() }.getOrDefault("")
        if (host in COURSES_HOSTS) return true
        return activity.url.isBlank() &&
            COURSES_WORKER_PREFIXES.any { prefix -> activity.activityKey.startsWith(prefix) }
    }

    private fun resolveModuleName(activity: ActivityEntity): String {
        val cleanKey = activity.activityKey.removePrefix("thnn:").lowercase()
        for ((prefix, mod) in KEY_MODULE_PREFIXES) {
            if (cleanKey.startsWith(prefix)) return mod
        }
        val urlPath = runCatching { URI(activity.url).path.orEmpty() }.getOrDefault("")
        val urlModule = Regex("/mod/([^/]+)/").find(urlPath)
            ?.groupValues?.getOrNull(1)?.lowercase()
        if (!urlModule.isNullOrBlank()) return urlModule
        return when (val typeNorm = activity.eventType.trim().lowercase()) {
            "assignment", "assign" -> "assign"
            "quiz" -> "quiz"
            else -> typeNorm
        }
    }

    companion object {
        internal fun normalizeStateValue(value: String): String =
            value.trim().lowercase().split(Regex("\\s+")).joinToString(" ")

        internal fun isCompletedState(value: String): Boolean =
            normalizeStateValue(value) in COMPLETED_STATES

        private val COMPLETED_STATES = setOf(
            "submitted",
            "graded",
            "finished",
            "submitted for grading",
            "đã nộp",
            "đã nộp, chờ chấm",
            "đã làm",
            "đã hoàn thành",
            "đã chấm",
            "đã chấm điểm",
            "đã công bố",
            "released",
        )
        private val UNVERIFIED_STATES = setOf("", "unknown")
        private val VERIFIABLE_TYPES = setOf("assignment", "assign", "quiz")
        private val VERIFIABLE_MODULES = setOf("assign", "quiz")
        private val COURSES_HOSTS = setOf("courses.ut.edu.vn", "elearning.ut.edu.vn")
        private val COURSES_WORKER_PREFIXES = listOf("assign:", "quiz:")
        private val KEY_MODULE_PREFIXES = listOf(
            "scorm:" to "scorm",
            "scorm_" to "scorm",
            "lesson:" to "lesson",
            "lesson_" to "lesson",
            "assign:" to "assign",
            "assign_" to "assign",
            "quiz:" to "quiz",
            "quiz_" to "quiz",
        )
    }
}

fun stableNotificationId(activityKey: String, milestoneMinutes: Int): Int {
    var hash = 0x811c9dc5.toInt()
    "$activityKey|$milestoneMinutes".toByteArray(Charsets.UTF_8).forEach { byte ->
        hash = (hash xor (byte.toInt() and 0xff)) * 16777619
    }
    return (hash and 0x7fffffff).takeIf { it != 0 } ?: 1
}

fun deliveryKey(activity: ActivityEntity, milestoneMinutes: Int) =
    "${activity.activityKey}|${activity.revision}|$milestoneMinutes"
