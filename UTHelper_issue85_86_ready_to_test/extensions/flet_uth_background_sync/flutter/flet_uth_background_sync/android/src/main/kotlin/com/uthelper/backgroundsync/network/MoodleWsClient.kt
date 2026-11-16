package com.uthelper.backgroundsync.network

import com.uthelper.backgroundsync.data.ActivityEntity
import com.uthelper.backgroundsync.data.NativeSettings
import com.uthelper.backgroundsync.security.MoodleCredential
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

class MoodleWsClient(private val credential: MoodleCredential) {
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
                        ?.let { state -> activity.copy(submissionState = state) }
                } ?: activity
                "quiz" -> quizIds[cmid]?.let { quizId ->
                    runCatching { quizState(quizId) }.getOrNull()
                        ?.let { state -> activity.copy(submissionState = state) }
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
        val submission = response.optJSONObject("lastattempt")
            ?.optJSONObject("submission")
        return when (submission?.optString("status", "")?.lowercase()) {
            "submitted" -> "submitted"
            "graded" -> "graded"
            else -> "not_submitted"
        }
    }

    private fun quizState(quizId: Long): String {
        val response = callObject(
            "mod_quiz_get_user_attempts",
            mapOf("quizid" to quizId, "status" to "all"),
        )
        val attempts = response.optJSONArray("attempts") ?: return "not_submitted"
        for (index in 0 until attempts.length()) {
            val state = attempts.optJSONObject(index)?.optString("state", "")?.lowercase()
            if (state == "finished") return "submitted"
        }
        return "not_submitted"
    }

    private fun parseEvent(event: JSONObject, generation: Long): ActivityEntity? {
        val deadline = event.optLong("timesort", event.optLong("timestart", 0))
        if (deadline <= 0) return null
        val module = event.optString("modulename", "other").lowercase()
        val eventType = when (module) {
            "assign" -> "assignment"
            "quiz", "scorm", "lesson" -> "quiz"
            "attendance" -> "attendance"
            else -> module
        }
        val url = event.optString("url", "")
        val cmid = cmid(url) ?: event.optLong("instance", 0).takeIf { it > 0 }
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

    private fun callObject(function: String, parameters: Map<String, Any>): JSONObject {
        val endpoint = "${credential.baseUrl}/webservice/rest/server.php"
        val form = linkedMapOf<String, Any>(
            "wstoken" to credential.token,
            "wsfunction" to function,
            "moodlewsrestformat" to "json",
        ).apply { putAll(parameters) }
        val body = form.entries.joinToString("&") { (key, value) ->
            "${encode(key)}=${encode(value.toString())}"
        }.toByteArray(Charsets.UTF_8)
        val connection = URI(endpoint).toURL().openConnection() as HttpURLConnection
        return try {
            connection.requestMethod = "POST"
            connection.connectTimeout = 20_000
            connection.readTimeout = 25_000
            connection.doOutput = true
            connection.setRequestProperty(
                "Content-Type",
                "application/x-www-form-urlencoded; charset=UTF-8",
            )
            connection.outputStream.use { it.write(body) }
            val stream = if (connection.responseCode in 200..299) {
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
    }
}
