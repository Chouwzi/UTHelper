package com.uthelper.backgroundsync.notification

import com.uthelper.backgroundsync.data.ActivityEntity
import com.uthelper.backgroundsync.data.NativeSettings
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
        if (activity.courseName.lowercase() in settings.mutedCourses) return false
        if (settings.ignoreSubmitted && activity.eventType in SUBMITTABLE_TYPES &&
            activity.submissionState.lowercase() == "unknown"
        ) return false
        if (settings.ignoreSubmitted && isCompleted(activity)) return false
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
        activity.graded || activity.submissionState.lowercase() in COMPLETED_STATES

    companion object {
        private val COMPLETED_STATES = setOf("submitted", "graded", "đã nộp", "đã chấm")
        private val SUBMITTABLE_TYPES = setOf("assignment", "quiz")
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
