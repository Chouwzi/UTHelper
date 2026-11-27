package com.uthelper.backgroundsync.network

import com.uthelper.backgroundsync.data.ActivityEntity
import com.uthelper.backgroundsync.data.NativeSettings
import com.uthelper.backgroundsync.security.MoodleCredential
import com.uthelper.backgroundsync.security.TrustedMoodleUri
import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URI
import java.net.URLEncoder
import java.security.MessageDigest
import java.time.Instant

data class DiscoveryResult(
    val activities: List<ActivityEntity>,
    val authoritative: Boolean,
)

class MoodleWsException(message: String, val errorCode: String = "") : Exception(message)

class MoodleWsClient internal constructor(
    private val credential: MoodleCredential,
    private val connectionFactory: (String) -> HttpURLConnection = { endpoint ->
        URI(endpoint).toURL().openConnection() as HttpURLConnection
    },
) {
    constructor(credential: MoodleCredential) : this(
        credential,
        { endpoint -> URI(endpoint).toURL().openConnection() as HttpURLConnection },
    )
    fun fetchActivities(settings: NativeSettings, generation: Long): DiscoveryResult {
        val now = Instant.now().epochSecond
        val end = now + settings.fetchMonths * 31L * 24L * 3600L
        val events = mutableListOf<JSONObject>()
        var afterEventId = 0L
        var authoritative = true
        var pageIndex = 0
        while (pageIndex < MAX_PAGES) {
            val params = linkedMapOf<String, Any>(
                "timesortfrom" to now,
                "timesortto" to end,
                "limitnum" to PAGE_SIZE,
            )
            if (afterEventId > 0) params["aftereventid"] = afterEventId
            val page = callObject("core_calendar_get_action_events_by_timesort", params)
            val pageEvents = page.optJSONArray("events") ?: JSONArray()
            for (index in 0 until pageEvents.length()) {
                pageEvents.optJSONObject(index)?.let(events::add)
            }
            val more = page.optBoolean("moreevents", pageEvents.length() >= PAGE_SIZE)
            if (!more) break
            val lastId = page.optLong("lastid", 0L).takeIf { id -> id > afterEventId }
                ?: pageEvents.optJSONObject(pageEvents.length() - 1)?.optLong("id", 0L)
            if (lastId == null || lastId <= afterEventId) {
                authoritative = false
                break
            }
            afterEventId = lastId
            pageIndex++
            if (pageIndex == MAX_PAGES) authoritative = false
        }

        val parsed = events.mapNotNull { parseEvent(it, generation) }.toMutableList()
        enrichSubmissionStates(parsed)
        return DiscoveryResult(parsed.distinctBy(ActivityEntity::activityKey), authoritative)
    }

    private fun enrichSubmissionStates(activities: MutableList<ActivityEntity>) {
        val courseIds = activities.mapNotNull { it.courseId.toLongOrNull() }.distinct()
        if (courseIds.isEmpty()) return
        val assignmentIds = assignmentIdsByCmid(courseIds)
        val quizIds = quizIdsByCmid(courseIds)
        for (index in activities.indices) {
            val activity = activities[index]
            val cmid = cmid(activity.url) ?: continue
            activities[index] = when (activity.eventType) {
                "assignment" -> assignmentIds[cmid]?.let { assignmentId ->
                    runCatching { assignmentState(assignmentId) }.getOrNull()
                        ?.let { state ->
                            activity.copy(
                                submissionState = state,
                                graded = activity.graded || state == "graded",
                            )
                        }
                } ?: activity
                "quiz" -> quizIds[cmid]?.let { quizId ->
                    runCatching { quizState(quizId) }.getOrNull()
                        ?.let { state ->
                            activity.copy(
                                submissionState = state,
                                graded = activity.graded || state == "graded",
                            )
                        }
                } ?: activity
                else -> activity
            }
        }
    }

    private fun assignmentIdsByCmid(courseIds: List<Long>): Map<Long, Long> {
        val response = callObject(
            "mod_assign_get_assignments",
            courseIds.mapIndexed { index, id -> "courseids[$index]" to id }.toMap(),
        )
        val result = mutableMapOf<Long, Long>()
        val courses = response.optJSONArray("courses") ?: return result
        for (courseIndex in 0 until courses.length()) {
            val assignments = courses.optJSONObject(courseIndex)
                ?.optJSONArray("assignments") ?: continue
            for (index in 0 until assignments.length()) {
                assignments.optJSONObject(index)?.let {
                    val cmid = it.optLong("cmid", 0)
                    val id = it.optLong("id", 0)
                    if (cmid > 0 && id > 0) result[cmid] = id
                }
            }
        }
        return result
    }

    private fun quizIdsByCmid(courseIds: List<Long>): Map<Long, Long> {
        val response = callObject(
            "mod_quiz_get_quizzes_by_courses",
            courseIds.mapIndexed { index, id -> "courseids[$index]" to id }.toMap(),
        )
        val result = mutableMapOf<Long, Long>()
        val quizzes = response.optJSONArray("quizzes") ?: return result
        for (index in 0 until quizzes.length()) {
            quizzes.optJSONObject(index)?.let {
                val cmid = it.optLong("coursemodule", it.optLong("cmid", 0))
                val id = it.optLong("id", 0)
                if (cmid > 0 && id > 0) result[cmid] = id
            }
        }
        return result
    }

    private fun assignmentState(assignmentId: Long): String {
        val response = callObject(
            "mod_assign_get_submission_status",
            mapOf("assignid" to assignmentId),
        )
        return resolveAssignmentState(response)
    }

    private fun quizState(quizId: Long): String {
        val response = callObject(
            "mod_quiz_get_user_attempts",
            mapOf("quizid" to quizId, "status" to "all"),
        )
        return resolveQuizState(response)
    }

    internal fun resolveAssignmentState(response: JSONObject): String {
        if (response.has("exception")) return "unknown"
        val lastAttempt = response.optJSONObject("lastattempt")
        val gradingStatus = lastAttempt
            ?.optString("gradingstatus", "")
            ?.trim()
            ?.lowercase()
            .orEmpty()
        if (gradingStatus in GRADED_STATUSES) return "graded"

        val gradeObj = response.optJSONObject("feedback")?.optJSONObject("grade")
        if (hasValidFeedbackGrade(gradeObj)) return "graded"

        if (lastAttempt == null || lastAttempt.length() == 0) return "unknown"

        val rawStatuses = mutableListOf<String>()
        val normalizedStatuses = mutableListOf<String>()
        for (subKey in listOf("submission", "teamsubmission")) {
            val sub = lastAttempt.optJSONObject(subKey) ?: continue
            val raw = sub.optString("status", "").trim()
            if (raw.isNotEmpty()) {
                rawStatuses.add(raw)
                normalizedStatuses.add(normalizeAssignSubStatus(raw))
            }
        }
        return when {
            "graded" in normalizedStatuses -> "graded"
            "submitted" in normalizedStatuses -> "submitted"
            "draft" in normalizedStatuses -> "draft"
            "not_submitted" in normalizedStatuses -> "not_submitted"
            rawStatuses.isNotEmpty() -> normalizedStatuses.firstOrNull { it != "unknown" } ?: "unknown"
            else -> "not_submitted"
        }
    }

    internal fun resolveQuizState(response: JSONObject): String {
        if (response.has("exception")) return "unknown"
        val attempts = response.optJSONArray("attempts") ?: return "unknown"
        var hasInProgress = false
        var hasAnyAttempt = false
        for (index in 0 until attempts.length()) {
            val att = attempts.optJSONObject(index) ?: continue
            hasAnyAttempt = true
            when (att.optString("state", "").trim().lowercase()) {
                "finished" -> return "submitted"
                "inprogress" -> hasInProgress = true
            }
        }
        if (hasInProgress) return "in_progress"
        return if (hasAnyAttempt) "attempted" else "not_submitted"
    }

    private fun hasValidFeedbackGrade(gradeObj: JSONObject?): Boolean {
        if (gradeObj == null || !gradeObj.has("grade") || gradeObj.isNull("grade")) return false
        val raw = gradeObj.opt("grade")?.toString()?.trim().orEmpty()
        if (raw.isEmpty() || raw == "-") return false
        val numeric = raw.toDoubleOrNull()
        return if (numeric != null) numeric >= 0.0 else true
    }

    private fun normalizeAssignSubStatus(raw: String): String =
        when (raw.trim().lowercase()) {
            "submitted", "submitted for grading", "finished", "đã nộp", "đã hoàn thành" -> "submitted"
            "graded", "released", "đã chấm", "đã chấm điểm", "đã công bố" -> "graded"
            "draft", "bản nháp" -> "draft"
            "new", "reopened", "not_submitted", "not submitted", "chưa nộp" -> "not_submitted"
            else -> "unknown"
        }

    internal fun parseEvent(event: JSONObject, generation: Long): ActivityEntity? {
        val deadline = event.optLong("timesort", event.optLong("timestart", 0))
        if (deadline <= 0) return null
        val module = event.optString("modulename", "other").lowercase()
        val eventType = when (module) {
            "assign" -> "assignment"
            "quiz", "scorm", "lesson" -> "quiz"
            "attendance" -> "attendance"
            else -> module
        }
        val rawUrl = event.optString("url", "")
        val cmid = cmid(rawUrl) ?: event.optLong("instance", 0).takeIf { it > 0 }
        val url = if (TrustedMoodleUri.validateActivityUri(rawUrl, credential.baseUrl) != null) {
            rawUrl
        } else {
            ""
        }
        val eventId = event.optString("id", "")
        val key = if (cmid != null) "$module:$cmid" else "event:$eventId"
        val course = event.optJSONObject("course") ?: JSONObject()
        val revision = sha256("$key|$deadline")
        return ActivityEntity(
            activityKey = key,
            activityId = eventId.ifBlank { key },
            courseId = course.optString("id", event.optString("courseid", "")),
            courseName = course.optString("fullname", event.optString("coursefullname", "")),
            title = event.optString("name", "Hoạt động Moodle"),
            eventType = eventType,
            deadlineEpochSeconds = deadline,
            url = url,
            submissionState = "unknown",
            graded = false,
            revision = revision,
            lastSeenGeneration = generation,
        )
    }

    internal fun callObject(function: String, parameters: Map<String, Any>): JSONObject {
        val origin = TrustedMoodleUri.normalizeTrustedOrigin(credential.baseUrl)
            ?: throw MoodleWsException("Untrusted Moodle base URL")
        val endpoint = "$origin/webservice/rest/server.php"
        val form = linkedMapOf<String, Any>(
            "wstoken" to credential.token,
            "wsfunction" to function,
            "moodlewsrestformat" to "json",
        ).apply { putAll(parameters) }
        val body = form.entries.joinToString("&") { (key, value) ->
            "${encode(key)}=${encode(value.toString())}"
        }.toByteArray(Charsets.UTF_8)
        val connection = connectionFactory(endpoint)
        return try {
            connection.instanceFollowRedirects = false
            connection.requestMethod = "POST"
            connection.connectTimeout = 20_000
            connection.readTimeout = 25_000
            connection.doOutput = true
            connection.setRequestProperty(
                "Content-Type",
                "application/x-www-form-urlencoded; charset=UTF-8",
            )
            connection.outputStream.use { it.write(body) }
            val code = connection.responseCode
            if (code in 300..399) {
                throw MoodleWsException("Unexpected Moodle redirect: HTTP $code")
            }
            val stream = if (code in 200..299) {
                connection.inputStream
            } else {
                connection.errorStream
            }
            val text = stream?.bufferedReader(Charsets.UTF_8)?.use { it.readText() }
                ?: throw MoodleWsException("Empty Moodle response")
            val response = JSONObject(text)
            if (response.has("exception")) {
                throw MoodleWsException(
                    response.optString("message", "Moodle Web Service error"),
                    response.optString("errorcode", ""),
                )
            }
            response
        } finally {
            connection.disconnect()
        }
    }

    private fun cmid(url: String): Long? = runCatching {
        URI(url).query?.split('&')
            ?.firstOrNull { it.startsWith("id=") }
            ?.substringAfter('=')?.toLong()
    }.getOrNull()

    private fun encode(value: String) = URLEncoder.encode(value, Charsets.UTF_8.name())

    private fun sha256(value: String): String = MessageDigest.getInstance("SHA-256")
        .digest(value.toByteArray(Charsets.UTF_8))
        .joinToString("") { "%02x".format(it) }

    companion object {
        private const val PAGE_SIZE = 100
        private const val MAX_PAGES = 10
        private val GRADED_STATUSES = setOf(
            "graded",
            "released",
            "đã chấm",
            "đã chấm điểm",
            "đã công bố",
        )
    }
}
