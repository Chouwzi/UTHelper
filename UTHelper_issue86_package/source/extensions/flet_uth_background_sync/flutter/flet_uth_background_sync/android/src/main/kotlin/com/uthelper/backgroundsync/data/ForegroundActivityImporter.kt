package com.uthelper.backgroundsync.data

import java.security.MessageDigest
import java.net.URI
import java.time.Instant
import java.time.LocalDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter

class ForegroundActivityImporter {
    fun parse(
        values: List<*>,
        generation: Long,
        previous: Map<String, ActivityEntity>,
    ): List<ActivityEntity> = values.mapNotNull { raw ->
        val value = raw as? Map<*, *> ?: return@mapNotNull null
        val activityId = value.text("id", "activity_id", "event_id")
        val url = value.text("url")
        val eventType = value.text("type", "event_type").lowercase()
        val key = value.text("activity_key").ifBlank {
            nativeActivityKey(url, eventType)
                ?: activityId.takeIf(String::isNotBlank)?.let { "activity:$it" }
                ?: url.takeIf(String::isNotBlank)
                ?: return@mapNotNull null
        }
        val deadline = deadline(value) ?: return@mapNotNull null
        val old = previous[key]
        val incomingState = value.text("submission_status").lowercase().ifBlank { "unknown" }
        val state = if (incomingState == "unknown" && old != null) {
            old.submissionState
        } else incomingState
        val incomingRevision = value.text("revision", "timemodified", "updated_at")
        ActivityEntity(
            activityKey = key,
            activityId = activityId.ifBlank { key },
            courseId = value.text("course_id", "courseid"),
            courseName = value.text("course_name", "course"),
            title = value.text("title", "name").ifBlank { "Hoạt động Moodle" },
            eventType = eventType.ifBlank { "other" },
            deadlineEpochSeconds = deadline,
            url = url,
            submissionState = state,
            graded = value.boolean("graded") || state == "graded" || old?.graded == true,
            revision = sha256("$incomingRevision|$key|$deadline"),
            lastSeenGeneration = generation,
        )
    }.distinctBy(ActivityEntity::activityKey)

    private fun deadline(value: Map<*, *>): Long? {
        value.number("deadline_epoch", "deadline_epoch_seconds")?.let { return it.toLong() }
        val raw = value.text("deadline", "deadline_str")
        if (raw.isBlank()) return null
        return runCatching { Instant.parse(raw).epochSecond }.recoverCatching {
            LocalDateTime.parse(raw, DateTimeFormatter.ISO_LOCAL_DATE_TIME)
                .atZone(ZoneId.systemDefault()).toEpochSecond()
        }.recoverCatching {
            LocalDateTime.parse(raw, DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss"))
                .atZone(ZoneId.systemDefault()).toEpochSecond()
        }.getOrNull()
    }

    private fun nativeActivityKey(url: String, eventType: String): String? = runCatching {
        if (url.isBlank()) return null
        val uri = URI(url)
        val cmid = uri.query?.split('&')
            ?.firstOrNull { it.startsWith("id=") }
            ?.substringAfter('=')?.toLongOrNull() ?: return null
        val moduleFromUrl = Regex("/mod/([^/]+)/").find(uri.path.orEmpty())
            ?.groupValues?.getOrNull(1)?.lowercase()
        val module = moduleFromUrl ?: when (eventType) {
            "assignment" -> "assign"
            "quiz" -> "quiz"
            "attendance" -> "attendance"
            else -> eventType
        }
        "$module:$cmid"
    }.getOrNull()

    private fun Map<*, *>.text(vararg keys: String): String = keys.firstNotNullOfOrNull { key ->
        get(key)?.toString()?.takeIf(String::isNotBlank)
    }.orEmpty()

    private fun Map<*, *>.number(vararg keys: String): Number? = keys.firstNotNullOfOrNull { key ->
        get(key) as? Number
    }

    private fun Map<*, *>.boolean(key: String): Boolean = when (val value = get(key)) {
        is Boolean -> value
        is Number -> value.toInt() != 0
        else -> value?.toString()?.toBooleanStrictOrNull() ?: false
    }

    private fun sha256(value: String): String = MessageDigest.getInstance("SHA-256")
        .digest(value.toByteArray(Charsets.UTF_8))
        .joinToString("") { "%02x".format(it) }
}
