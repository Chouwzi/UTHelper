package com.uthelper.backgroundsync.update

import android.app.DownloadManager
import android.content.Context
import android.net.Uri
import java.io.File
import java.security.MessageDigest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.Shadows
import org.robolectric.annotation.Implementation
import org.robolectric.annotation.Config
import org.robolectric.annotation.Implements

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [35], shadows = [PendingShadowDownloadManager::class])
class ApkUpdateDownloadManagerTest {
    private val url =
        "https://github.com/Chouwzi/UTHelper/releases/download/v2.5.1/UTHelper-2.5.1.apk"
    private val packageBytes = "verified update package".toByteArray()
    private val packageSha256 = sha256(packageBytes)

    @Test
    fun startReusesPersistedRequestAndReportsProgressAfterManagerRecreation() {
        val context: Context = RuntimeEnvironment.getApplication()
        val identity = identity(context)
        val manager = context.getSystemService(Context.DOWNLOAD_SERVICE) as DownloadManager
        val shadowManager = Shadows.shadowOf(manager)
        val firstDownloader = ApkUpdateDownloadManager(context)

        val started = firstDownloader.start(identity)
        assertEquals("pending", started.status)
        assertEquals(1, shadowManager.requestCount)
        val request = shadowManager.getRequest(requireNotNull(started.downloadId))
        val shadowRequest = Shadows.shadowOf(request)
        assertEquals(DownloadManager.Request.VISIBILITY_VISIBLE, shadowRequest.notificationVisibility)
        assertEquals("Đang tải bản cập nhật", shadowRequest.description.toString())
        val persistedRow = manager.query(DownloadManager.Query()).use { cursor ->
            assertTrue(cursor.moveToFirst())
            cursor.getString(cursor.getColumnIndexOrThrow(DownloadManager.COLUMN_LOCAL_URI))
        }
        assertEquals(
            "expected request destination URI",
            shadowRequest.destination.toString(),
            persistedRow,
        )

        context.getSharedPreferences("uth_update_downloads", Context.MODE_PRIVATE)
            .edit()
            .clear()
            .commit()

        val restoredDownloader = ApkUpdateDownloadManager(context)
        val restored = restoredDownloader.status(identity)
        assertEquals("status=${restored.status}, reason=${restored.reason}", "pending", restored.status)
        assertEquals(started.downloadId, restored.downloadId)

        val reused = restoredDownloader.start(identity)
        assertEquals("pending", reused.status)
        assertEquals(1, shadowManager.requestCount)

        shadowRequest.setBytesSoFar(packageBytes.size.toLong() / 2)
        shadowRequest.setStatus(DownloadManager.STATUS_RUNNING)
        val progress = ApkUpdateDownloadManager(context).status(identity)
        assertEquals("running", progress.status)
        assertEquals(packageBytes.size.toLong() / 2, progress.downloaded)
        assertEquals(packageBytes.size.toLong(), progress.total)
        assertNull(progress.localPath)
    }

    @Test
    fun completedRequestReturnsOnlyChecksumVerifiedAppSpecificFileAndCanBeCancelled() {
        val context: Context = RuntimeEnvironment.getApplication()
        val identity = identity(context)
        val downloadManager = context.getSystemService(Context.DOWNLOAD_SERVICE) as DownloadManager
        val shadowManager = Shadows.shadowOf(downloadManager)
        val downloader = ApkUpdateDownloadManager(context)
        val started = downloader.start(identity)
        val request = shadowManager.getRequest(requireNotNull(started.downloadId))
        val shadowRequest = Shadows.shadowOf(request)
        val destinationUri = shadowRequest.destination
        val destination = File(requireNotNull(destinationUri.path))
        assertTrue(destination.parentFile?.mkdirs() == true || destination.parentFile?.isDirectory == true)
        destination.writeBytes(packageBytes)
        shadowRequest.setBytesSoFar(packageBytes.size.toLong())
        shadowRequest.setStatus(DownloadManager.STATUS_SUCCESSFUL)

        val completed = ApkUpdateDownloadManager(context).status(identity)
        assertEquals("completed", completed.status)
        assertEquals(packageBytes.size.toLong(), completed.downloaded)
        assertEquals(destination.absolutePath, completed.localPath)
        assertEquals(packageSha256, sha256(File(requireNotNull(completed.localPath)).readBytes()))
        assertTrue(destination.exists())

        val reused = ApkUpdateDownloadManager(context).start(identity)
        assertEquals("completed", reused.status)
        assertEquals(1, shadowManager.requestCount)

        ApkUpdateDownloadManager(context).cancel(identity)
        assertFalse(destination.exists())
        assertEquals("missing", ApkUpdateDownloadManager(context).status(identity).status)
    }

    @Test
    fun rejectsUnapprovedUrlAndWrongPinnedIdentity() {
        assertTrue(ApkUpdateDownloadManager.isApprovedReleaseUrl(url))
        assertFalse(ApkUpdateDownloadManager.isApprovedReleaseUrl(
            "http://github.com/Chouwzi/UTHelper/releases/download/v2.5.1/app.apk"
        ))
        assertFalse(ApkUpdateDownloadManager.isApprovedReleaseUrl(
            "https://github.com.attacker.invalid/Chouwzi/UTHelper/releases/download/v2.5.1/app.apk"
        ))
        assertFalse(ApkUpdateDownloadManager.isApprovedReleaseUrl(
            "https://github.com/Other/UTHelper/releases/download/v2.5.1/app.apk"
        ))

        val context: Context = RuntimeEnvironment.getApplication()
        val invalid = identity(context).copy(expectedPackageId = "example.attacker")
        val result = ApkUpdateDownloadManager(context).let { downloader ->
            runCatching { downloader.start(invalid) }
        }
        assertTrue(result.isFailure)
    }

    private fun identity(context: Context) = UpdateDownloadIdentity(
        url = url,
        sha256 = packageSha256,
        expectedSize = packageBytes.size.toLong(),
        expectedPackageId = context.packageName,
        expectedVersionCode = 2_005_001L,
        expectedCertificateSha256 = "AB".repeat(32),
    )

    private fun sha256(bytes: ByteArray): String = MessageDigest.getInstance("SHA-256")
        .digest(bytes)
        .joinToString("") { "%02x".format(it) }
}

/** Robolectric leaves a newly enqueued request's status at Java-default 0; Android reports pending. */
@Implements(DownloadManager::class)
class PendingShadowDownloadManager : org.robolectric.shadows.ShadowDownloadManager() {
    @Implementation
    override fun enqueue(request: DownloadManager.Request): Long =
        super.enqueue(request).also {
            Shadows.shadowOf(request).setStatus(DownloadManager.STATUS_PENDING)
        }
}
