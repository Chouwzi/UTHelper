package com.uthelper.backgroundsync.notification

import com.uthelper.backgroundsync.data.ActivityEntity
import com.uthelper.backgroundsync.data.ForegroundActivityImporter
import com.uthelper.backgroundsync.data.NativeSettings
import com.uthelper.backgroundsync.network.MoodleWsClient
import com.uthelper.backgroundsync.security.MoodleCredential
import com.uthelper.backgroundsync.worker.MoodleSyncWorker
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [35])
class SubmittedAndThnnNotificationTest {
    private val settings = NativeSettings(
        enabled = true,
        intervalMinutes = 60,
        fetchMonths = 1,
        notifyTypes = emptySet(),
        mutedCourses = emptySet(),
        ignoreSubmitted = true,
        countdownMinutes = listOf(180, 30),
        dndEnabled = false,
        dndStartHour = 22,
        dndEndHour = 7,
    )

    @Test
    fun importerIsolatesThnnKeysFromCoursesAndNormalizesStatuses() {
        val importer = ForegroundActivityImporter()
        val imported = importer.parse(
            values = listOf(
                mapOf(
                    "id" to "105",
                    "title" to "Courses Assign",
                    "type" to "assignment",
                    "deadline" to "2026-12-01 12:00:00",
                    "url" to "https://courses.ut.edu.vn/mod/assign/view.php?id=42",
                    "submission_status" to "Submitted for grading",
                    "site_identity" to "courses",
                ),
                mapOf(
                    "id" to "105",
                    "title" to "THNN Assign Same CMID",
                    "type" to "assignment",
                    "deadline" to "2026-12-01 12:00:00",
                    "url" to "https://thnn.ut.edu.vn/mod/assign/view.php?id=42",
                    "submission_status" to "unknown",
                    "site_identity" to "thnn",
                ),
                mapOf(
                    "id" to "200",
                    "title" to "Courses Released Assign",
                    "type" to "assignment",
                    "deadline" to "2026-12-01 14:00:00",
                    "url" to "https://courses.ut.edu.vn/mod/assign/view.php?id=99",
                    "submission_status" to "new",
                    "details" to mapOf("status_data" to mapOf("Chấm điểm" to "released")),
                ),
            ),
            generation = 100L,
            previous = emptyMap(),
        )

        assertEquals(3, imported.size)
        val byTitle = imported.associateBy { it.title }
        assertEquals("assign:42", byTitle.getValue("Courses Assign").activityKey)
        assertEquals("submitted", byTitle.getValue("Courses Assign").submissionState)

        assertEquals("thnn:assign:42", byTitle.getValue("THNN Assign Same CMID").activityKey)
        assertEquals("unknown", byTitle.getValue("THNN Assign Same CMID").submissionState)

        assertEquals("graded", byTitle.getValue("Courses Released Assign").submissionState)
        assertTrue(byTitle.getValue("Courses Released Assign").graded)
    }

    @Test
    fun moodleWsClientResolvesTeamSubmissionDirectGradingAndMissingAttemptSafely() {
        val client = MoodleWsClient(MoodleCredential("https://courses.ut.edu.vn", "tok"))

        val teamSubmitted = JSONObject().put(
            "lastattempt",
            JSONObject()
                .put("submission", JSONObject().put("status", "new"))
                .put("teamsubmission", JSONObject().put("status", "submitted"))
                .put("gradingstatus", "notgraded"),
        )
        assertEquals("submitted", client.resolveAssignmentState(teamSubmitted))

        val directGraded = JSONObject().put(
            "lastattempt",
            JSONObject()
                .put("submission", JSONObject().put("status", "new"))
                .put("gradingstatus", "released"),
        )
        assertEquals("graded", client.resolveAssignmentState(directGraded))

        val feedbackGraded = JSONObject()
            .put("lastattempt", JSONObject().put("submission", JSONObject().put("status", "new")))
            .put("feedback", JSONObject().put("grade", JSONObject().put("grade", "8.50000")))
        assertEquals("graded", client.resolveAssignmentState(feedbackGraded))

        val negativeSentinel = JSONObject()
            .put("lastattempt", JSONObject().put("submission", JSONObject().put("status", "new")))
            .put("feedback", JSONObject().put("grade", JSONObject().put("grade", "-1.00000")))
        assertEquals("not_submitted", client.resolveAssignmentState(negativeSentinel))

        val missingLastAttempt = JSONObject().put("warnings", JSONArray())
        assertEquals("unknown", client.resolveAssignmentState(missingLastAttempt))
        assertEquals("unknown", client.resolveQuizState(JSONObject()))
    }

    @Test
    fun notificationPolicyBlocksCoursesUnknownAndCompletedWhileAcceptingThnnUnknown() {
        val policy = NotificationPolicy(settings)

        fun entity(
            key: String,
            url: String,
            type: String,
            state: String,
            graded: Boolean = false,
        ) = ActivityEntity(
            activityKey = key,
            activityId = key,
            courseId = "1",
            courseName = "Course",
            title = "Title",
            eventType = type,
            deadlineEpochSeconds = 1_900_000_000L,
            url = url,
            submissionState = state,
            graded = graded,
            revision = "rev",
            lastSeenGeneration = 1L,
        )

        // Courses unknown assignment/quiz -> blocked
        assertFalse(
            policy.accepts(
                entity(
                    "assign:101",
                    "https://courses.ut.edu.vn/mod/assign/view.php?id=101",
                    "assignment",
                    "unknown",
                )
            )
        )
        assertFalse(
            policy.accepts(
                entity("quiz:102", "", "quiz", "unknown")
            )
        )
        // Courses SCORM and Lesson mapped to eventType="quiz" with unknown state -> ACCEPTED!
        assertTrue(
            policy.accepts(
                entity(
                    "scorm:910",
                    "https://courses.ut.edu.vn/mod/scorm/view.php?id=910",
                    "quiz",
                    "unknown",
                )
            )
        )
        assertTrue(
            policy.accepts(
                entity("lesson:911", "", "quiz", "unknown")
            )
        )
        // Courses verified not_submitted -> accepted
        assertTrue(
            policy.accepts(
                entity(
                    "assign:101",
                    "https://courses.ut.edu.vn/mod/assign/view.php?id=101",
                    "assignment",
                    "not_submitted",
                )
            )
        )
        // THNN unknown -> accepted!
        assertTrue(
            policy.accepts(
                entity(
                    "thnn:quiz:55",
                    "https://thnn.ut.edu.vn/mod/quiz/view.php?id=55",
                    "quiz",
                    "unknown",
                )
            )
        )
        // THNN submitted / released -> blocked!
        assertFalse(
            policy.accepts(
                entity(
                    "thnn:quiz:55",
                    "https://thnn.ut.edu.vn/mod/quiz/view.php?id=55",
                    "quiz",
                    "đã hoàn thành",
                )
            )
        )
        assertFalse(
            policy.accepts(
                entity(
                    "assign:103",
                    "https://courses.ut.edu.vn/mod/assign/view.php?id=103",
                    "assignment",
                    "released",
                )
            )
        )
    }

    @Test
    fun moodleSyncWorkerPreservesUnexpiredForeignThnnActivitiesAcrossCoursesSync() {
        val now = 1_800_000_000L
        val coursesItem = ActivityEntity(
            activityKey = "assign:101",
            activityId = "101",
            courseId = "10",
            courseName = "CNPM",
            title = "Lab 1",
            eventType = "assignment",
            deadlineEpochSeconds = now + 7200,
            url = "https://courses.ut.edu.vn/mod/assign/view.php?id=101",
            submissionState = "not_submitted",
            graded = false,
            revision = "r1",
            lastSeenGeneration = 200L,
        )
        val thnnFuture = ActivityEntity(
            activityKey = "thnn:quiz:55",
            activityId = "55",
            courseId = "0",
            courseName = "Anh văn 1",
            title = "THNN Quiz",
            eventType = "quiz",
            deadlineEpochSeconds = now + 3600,
            url = "https://thnn.ut.edu.vn/mod/quiz/view.php?id=55",
            submissionState = "unknown",
            graded = false,
            revision = "r2",
            lastSeenGeneration = 100L,
        )
        val thnnExpired = thnnFuture.copy(
            activityKey = "thnn:quiz:56",
            deadlineEpochSeconds = now - 60,
        )
        val staleCourses = coursesItem.copy(
            activityKey = "assign:999",
            url = "https://courses.ut.edu.vn/mod/assign/view.php?id=999",
            lastSeenGeneration = 100L,
        )

        val merged = MoodleSyncWorker.mergeWithForeignSiteActivities(
            mergedActivities = listOf(coursesItem),
            latestPrevious = mapOf(
                thnnFuture.activityKey to thnnFuture,
                thnnExpired.activityKey to thnnExpired,
                staleCourses.activityKey to staleCourses,
            ),
            syncedBaseUrl = "https://courses.ut.edu.vn",
            startedAtEpochSeconds = now,
            generation = 200L,
        )

        assertEquals(setOf("assign:101", "thnn:quiz:55"), merged.map { it.activityKey }.toSet())
        assertEquals(200L, merged.first { it.activityKey == "thnn:quiz:55" }.lastSeenGeneration)
    }

    @Test
    fun failedStatusVerificationDoesNotResurrectStaleNotSubmittedWhilePreservingCompleted() {
        val policy = NotificationPolicy(settings)
        val importer = ForegroundActivityImporter()

        val oldNotSubmitted = ActivityEntity(
            activityKey = "assign:501",
            activityId = "501",
            courseId = "10",
            courseName = "CNPM",
            title = "Assignment 501",
            eventType = "assignment",
            deadlineEpochSeconds = 1_900_000_000L,
            url = "https://courses.ut.edu.vn/mod/assign/view.php?id=501",
            submissionState = "not_submitted",
            graded = false,
            revision = "r0",
            lastSeenGeneration = 100L,
        )
        val oldSubmitted = oldNotSubmitted.copy(
            activityKey = "assign:502",
            activityId = "502",
            url = "https://courses.ut.edu.vn/mod/assign/view.php?id=502",
            submissionState = "submitted",
        )
        val previousMap = mapOf(
            oldNotSubmitted.activityKey to oldNotSubmitted,
            oldSubmitted.activityKey to oldSubmitted,
        )

        // 1. ForegroundActivityImporter: T0 not_submitted -> T1 submitted on web -> T2 WS failed ("unknown")
        val reimported = importer.parse(
            values = listOf(
                mapOf(
                    "id" to "501",
                    "title" to "Assignment 501",
                    "type" to "assignment",
                    "deadline" to "2030-03-15 12:00:00",
                    "url" to "https://courses.ut.edu.vn/mod/assign/view.php?id=501",
                    "submission_status" to "unknown",
                    "site_identity" to "courses",
                ),
                mapOf(
                    "id" to "502",
                    "title" to "Assignment 502",
                    "type" to "assignment",
                    "deadline" to "2030-03-15 12:00:00",
                    "url" to "https://courses.ut.edu.vn/mod/assign/view.php?id=502",
                    "submission_status" to "unknown",
                    "site_identity" to "courses",
                ),
            ),
            generation = 200L,
            previous = previousMap,
        ).associateBy { it.activityKey }

        assertEquals("unknown", reimported.getValue("assign:501").submissionState)
        assertFalse(policy.accepts(reimported.getValue("assign:501")))
        assertEquals("submitted", reimported.getValue("assign:502").submissionState)
        assertFalse(policy.accepts(reimported.getValue("assign:502")))

        // 2. MoodleSyncWorker: discovered Courses snapshot with WS status timeout ("unknown")
        val discoveredUnknown = listOf(
            oldNotSubmitted.copy(submissionState = "unknown", lastSeenGeneration = 200L),
            oldSubmitted.copy(submissionState = "unknown", lastSeenGeneration = 200L),
        )
        val workerMerged = MoodleSyncWorker.mergeCoursesSnapshotActivities(
            discovered = discoveredUnknown,
            latestPrevious = previousMap,
        ).associateBy { it.activityKey }

        assertEquals("unknown", workerMerged.getValue("assign:501").submissionState)
        assertFalse(policy.accepts(workerMerged.getValue("assign:501")))
        assertEquals("submitted", workerMerged.getValue("assign:502").submissionState)
        assertFalse(policy.accepts(workerMerged.getValue("assign:502")))
    }
}
