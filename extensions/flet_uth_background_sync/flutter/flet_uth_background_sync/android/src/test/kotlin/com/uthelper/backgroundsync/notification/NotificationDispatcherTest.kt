package com.uthelper.backgroundsync.notification

import android.Manifest
import android.app.Application
import android.app.NotificationManager
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.ActivityInfo
import com.uthelper.backgroundsync.data.ActivityEntity
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.Shadows
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [35])
class NotificationDispatcherTest {
    private fun setupContext(): Application {
        val app = RuntimeEnvironment.getApplication()
        Shadows.shadowOf(app).grantPermissions(Manifest.permission.POST_NOTIFICATIONS)
        val launcher = ActivityInfo().apply {
            packageName = app.packageName
            name = "${app.packageName}.MainActivity"
            applicationInfo = app.applicationInfo
        }
        val shadowPm = Shadows.shadowOf(app.packageManager)
        shadowPm.addOrUpdateActivity(launcher)
        shadowPm.addIntentFilterForActivity(
            ComponentName(app.packageName, launcher.name),
            IntentFilter(Intent.ACTION_MAIN).apply {
                addCategory(Intent.CATEGORY_LAUNCHER)
            },
        )
        return app
    }

    private fun sampleActivity(url: String): ActivityEntity = ActivityEntity(
        activityKey = "assign:101",
        activityId = "101",
        courseId = "1",
        courseName = "CNPM",
        title = "Bai tap 1",
        eventType = "assignment",
        deadlineEpochSeconds = (System.currentTimeMillis() / 1000L) + 3600L,
        url = url,
        submissionState = "not_submitted",
        graded = false,
        revision = "rev-1",
        lastSeenGeneration = 1L,
    )

    @Test
    fun trustedHttpsMoodleUrlUsesActionViewWithBrowsable() {
        val app = setupContext()
        val dispatcher = NotificationDispatcher(app) { "https://courses.ut.edu.vn" }
        assertTrue(dispatcher.show(sampleActivity("https://courses.ut.edu.vn/mod/assign/view.php?id=101"), 60))

        val manager = app.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        val notifications = Shadows.shadowOf(manager).allNotifications
        assertEquals(1, notifications.size)
        val savedIntent = Shadows.shadowOf(notifications[0].contentIntent).savedIntent
        assertEquals(Intent.ACTION_VIEW, savedIntent.action)
        assertEquals("https://courses.ut.edu.vn/mod/assign/view.php?id=101", savedIntent.dataString)
        assertTrue(savedIntent.hasCategory(Intent.CATEGORY_BROWSABLE))
    }

    @Test
    fun untrustedUrlsFallBackToAppLaunchIntentInsteadOfActionView() {
        val app = setupContext()
        val dispatcher = NotificationDispatcher(app) { "https://courses.ut.edu.vn" }
        val manager = app.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager

        for ((index, badUrl) in listOf(
            "javascript:alert(1)",
            "intent://evil/#Intent;end",
            "http://courses.ut.edu.vn/mod/assign/view.php?id=101",
            "https://evil.com/mod/assign/view.php?id=101",
            "",
        ).withIndex()) {
            manager.cancelAll()
            assertTrue(dispatcher.show(sampleActivity(badUrl), 60 + index))
            val notifications = Shadows.shadowOf(manager).allNotifications
            assertEquals("Expected notification for $badUrl", 1, notifications.size)
            val pending = notifications[0].contentIntent
            assertNotNull("Expected fallback launch PendingIntent for $badUrl", pending)
            val savedIntent = Shadows.shadowOf(pending).savedIntent
            assertEquals(Intent.ACTION_MAIN, savedIntent.action)
            assertEquals(app.packageName, savedIntent.`package` ?: savedIntent.component?.packageName)
        }
    }
}
