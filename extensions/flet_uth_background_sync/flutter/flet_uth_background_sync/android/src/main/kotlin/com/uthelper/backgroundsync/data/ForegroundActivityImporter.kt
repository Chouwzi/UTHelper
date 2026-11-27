package com.uthelper.backgroundsync.data

import com.uthelper.backgroundsync.notification.NotificationPolicy
import java.net.URI
import java.security.MessageDigest
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
        val siteIdentity = value.text("site_identity", "site", "moodle_site").lowercase()
        val sourceKind = value.text("source", "source_kind", "source_type").lowercase()
        val isThnn = isThnnActivity(url, siteIdentity, sourceKind)
        val key = value.text("activity_key").ifBlank {
            nativeActivityKey(url, eventType, isThnn)
                ?: activityId.takeIf(String::isNotBlank)?.let {
                    if (isThnn) "thnn:activity:$it" else "activity:$it"
                }
                ?: url.takeIf(String::isNotBlank)
                ?: return@mapNotNull null
        }
        val deadline = deadline(value) ?: return@mapNotNull null
        val old = previous[key]
        val incomingState = extractNormalizedState(value)
        val oldCompleted = old != null &&
            (old.graded || NotificationPolicy.isCompletedState(old.submissionState))
        val state = if (incomingState == "unknown" && old != null && oldCompleted) {
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

    private fun isThnnActivity(url: String, siteIdentity: String, sourceKind: String): Boolean {
        if (siteIdentity == "thnn" || sourceKind == "thnn_sync") return true
        val host = runCatching { URI(url).host?.lowercase().orEmpty() }.getOrDefault("")
        return host == "thnn.ut.edu.vn" || host.endsWith(".thnn.ut.edu.vn")
    }

    private fun nativeActivityKey(
        url: String,
        eventType: String,
        isThnn: Boolean = false,
    ): String? = runCatching {
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
        val host = uri.host?.lowercase().orEmpty()
        if (isThnn || host == "thnn.ut.edu.vn" || host.endsWith(".thnn.ut.edu.vn")) {
            "thnn:$module:$cmid"
        } else {
            "$module:$cmid"
        }
    }.getOrNull()

    private fun extractNormalizedState(value: Map<*, *>): String {
        if (value.boolean("graded")) return "graded"
        val top = normalizeSubmissionStatus(value.text("submission_status"))
        val details = value["details"] as? Map<*, *>
        val statusData = details?.get("status_data") as? Map<*, *>
        if (statusData != null) {
            for (gKey in listOf("Chấm điểm", "Grading status", "Trạng thái chấm")) {
                if (normalizeSubmissionStatus(statusData[gKey]?.toString().orEmpty()) == "graded") {
                    return "graded"
                }
            }
        }
        val detailStatus = details?.let { det ->
            val quizAtt = normalizeSubmissionStatus(det["quiz_attempt_status"]?.toString().orEmpty())
            if (quizAtt != "unknown") return@let quizAtt
            val detSub = normalizeSubmissionStatus(det["submission_status"]?.toString().orEmpty())
            if (detSub != "unknown") return@let detSub
            statusData?.let { sd ->
                listOf("Trạng thái nộp bài", "Submission status", "Trạng thái")
                    .asSequence()
                    .map { normalizeSubmissionStatus(sd[it]?.toString().orEmpty()) }
                    .firstOrNull { it != "unknown" }
            } ?: "unknown"
        } ?: "unknown"

        if (top == "graded" || detailStatus == "graded") return "graded"
        if (top == "submitted" || detailStatus == "submitted") return "submitted"
        return if (top != "unknown") top else detailStatus
    }

    internal fun normalizeSubmissionStatus(raw: String): String {
        val cleaned = raw.trim().lowercase().split(Regex("\\s+")).joinToString(" ")
        if (cleaned.isBlank()) return "unknown"
        return when (cleaned) {
            "unknown", "not_opened", "not_submitted", "draft", "submitted",
            "graded", "in_progress", "overdue", "abandoned", "attempted",
            -> cleaned
            "new", "reopened", "not submitted", "no submission", "no submissions",
            "chưa nộp", "chưa làm", "được mở lại",
            -> "not_submitted"
            "bản nháp" -> "draft"
            "submitted for grading", "finished", "đã nộp", "đã nộp, chờ chấm",
            "đã làm", "đã hoàn thành",
            -> "submitted"
            "released", "đã chấm", "đã chấm điểm", "đã công bố" -> "graded"
            "not opened", "chưa mở" -> "not_opened"
            "inprogress", "đang làm" -> "in_progress"
            "quá hạn" -> "overdue"
            "chưa hoàn thành" -> "abandoned"
            "đã bắt đầu" -> "attempted"
            else -> "unknown"
        }
    }

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
