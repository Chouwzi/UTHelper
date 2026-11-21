package com.uthelper.backgroundsync.update

import android.app.DownloadManager
import android.content.Context
import android.net.Uri
import android.os.Environment
import java.io.File
import java.net.URI
import java.security.MessageDigest
import java.util.concurrent.ConcurrentHashMap

/** Release metadata that pins both the downloaded bytes and their Android identity. */
data class UpdateDownloadIdentity(
    val url: String,
    val sha256: String,
    val expectedSize: Long,
    val expectedPackageId: String,
    val expectedVersionCode: Long,
    val expectedCertificateSha256: String,
) {
    fun validate(installedPackageId: String) {
        require(ApkUpdateDownloadManager.isApprovedReleaseUrl(url)) {
            "Update URL is not an approved GitHub APK release"
        }
        require(sha256.matches(Regex("[0-9a-fA-F]{64}"))) {
            "A release SHA-256 checksum is required"
        }
        require(expectedSize in 1..MAX_UPDATE_SIZE_BYTES) { "Release size is invalid" }
        require(expectedPackageId == installedPackageId) { "Unexpected package identity" }
        require(expectedVersionCode > 0L) { "A positive versionCode is required" }
        normalizedFingerprint(expectedCertificateSha256)
    }

    val key: String
        get() = sha256Text(
            listOf(
                url,
                sha256.lowercase(),
                expectedSize.toString(),
                expectedPackageId,
                expectedVersionCode.toString(),
                normalizedFingerprint(expectedCertificateSha256),
            ).joinToString("\n").toByteArray()
        )
}

data class UpdateDownloadResult(
    val status: String,
    val downloadId: Long? = null,
    val downloaded: Long = 0L,
    val total: Long = 0L,
    val localPath: String? = null,
    val reason: String? = null,
)

/**
 * Owns durable APK downloads through Android's system DownloadManager. The request id is
 * persisted by the app, and the app-specific destination encodes the full release identity
 * so an enqueue interrupted before that preference write can be recovered without a duplicate.
 */
class ApkUpdateDownloadManager(private val context: Context) {
    private val preferences =
        context.getSharedPreferences(PREFERENCES_NAME, Context.MODE_PRIVATE)
    private val verifiedFiles = ConcurrentHashMap<String, Pair<Long, Long>>()

    fun start(identity: UpdateDownloadIdentity): UpdateDownloadResult {
        identity.validate(context.packageName)
        val manager = downloadManager()
        val destination = destinationFor(identity)

        val existingId = readDownloadId(identity) ?: findRequestId(manager, identity)
        if (existingId != null) {
            writeDownloadId(identity, existingId)
            val current = query(manager, identity, existingId)
            if (current.status in ACTIVE_OR_COMPLETE_STATUSES) {
                return current
            }
            manager.remove(existingId)
        }

        destination.delete()
        val request = DownloadManager.Request(Uri.parse(identity.url)).apply {
            setTitle("UTHelper update")
            setDescription("Đang tải bản cập nhật")
            setMimeType(APK_MIME_TYPE)
            setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE)
            setDestinationInExternalFilesDir(
                context,
                Environment.DIRECTORY_DOWNLOADS,
                "update-${identity.key}.apk",
            )
        }
        val id = manager.enqueue(request)
        check(writeDownloadId(identity, id)) { "Could not persist Android download identity" }
        return query(manager, identity, id)
    }

    fun status(identity: UpdateDownloadIdentity): UpdateDownloadResult {
        identity.validate(context.packageName)
        val manager = downloadManager()
        val id = readDownloadId(identity) ?: findRequestId(manager, identity)
            ?: return UpdateDownloadResult("missing", total = identity.expectedSize)
        writeDownloadId(identity, id)
        return query(manager, identity, id)
    }

    fun cancel(identity: UpdateDownloadIdentity) {
        identity.validate(context.packageName)
        val manager = downloadManager()
        val id = readDownloadId(identity) ?: findRequestId(manager, identity)
        if (id != null) manager.remove(id)
        destinationFor(identity).delete()
        verifiedFiles.remove(identity.key)
        preferences.edit().remove(downloadIdPreferenceKey(identity)).commit()
    }

    private fun query(
        manager: DownloadManager,
        identity: UpdateDownloadIdentity,
        id: Long,
    ): UpdateDownloadResult {
        manager.query(DownloadManager.Query().setFilterById(id)).use { cursor ->
            if (!cursor.moveToFirst()) {
                preferences.edit().remove(downloadIdPreferenceKey(identity)).commit()
                verifiedFiles.remove(identity.key)
                return UpdateDownloadResult("missing", total = identity.expectedSize)
            }

            val requestUrl = cursor.getString(cursor.getColumnIndexOrThrow(DownloadManager.COLUMN_URI))
            if (requestUrl != identity.url) {
                manager.remove(id)
                preferences.edit().remove(downloadIdPreferenceKey(identity)).commit()
                verifiedFiles.remove(identity.key)
                return UpdateDownloadResult(
                    "failed",
                    downloadId = id,
                    total = identity.expectedSize,
                    reason = "identity_mismatch",
                )
            }

            val downloaded = cursor.getLong(
                cursor.getColumnIndexOrThrow(DownloadManager.COLUMN_BYTES_DOWNLOADED_SO_FAR)
            ).coerceAtLeast(0L)
            val total = identity.expectedSize
            return when (cursor.getInt(cursor.getColumnIndexOrThrow(DownloadManager.COLUMN_STATUS))) {
                DownloadManager.STATUS_PENDING ->
                    UpdateDownloadResult("pending", id, downloaded, total)
                DownloadManager.STATUS_RUNNING ->
                    UpdateDownloadResult("running", id, downloaded, total)
                DownloadManager.STATUS_PAUSED ->
                    UpdateDownloadResult("paused", id, downloaded, total)
                DownloadManager.STATUS_SUCCESSFUL -> verifyCompletedDownload(
                    manager,
                    identity,
                    id,
                    downloaded,
                )
                DownloadManager.STATUS_FAILED -> UpdateDownloadResult(
                    status = "failed",
                    downloadId = id,
                    downloaded = downloaded,
                    total = total,
                    reason = failureReason(
                        cursor.getInt(cursor.getColumnIndexOrThrow(DownloadManager.COLUMN_REASON))
                    ),
                )
                else -> UpdateDownloadResult(
                    status = "failed",
                    downloadId = id,
                    downloaded = downloaded,
                    total = total,
                    reason = "unknown_download_status",
                )
            }
        }
    }

    private fun verifyCompletedDownload(
        manager: DownloadManager,
        identity: UpdateDownloadIdentity,
        id: Long,
        downloaded: Long,
    ): UpdateDownloadResult {
        val file = destinationFor(identity)
        if (!file.isFile || file.length() != identity.expectedSize) {
            manager.remove(id)
            file.delete()
            verifiedFiles.remove(identity.key)
            preferences.edit().remove(downloadIdPreferenceKey(identity)).commit()
            return UpdateDownloadResult(
                "failed",
                id,
                downloaded,
                identity.expectedSize,
                reason = "download_size_mismatch",
            )
        }

        val fileStamp = file.length() to file.lastModified()
        if (verifiedFiles[identity.key] != fileStamp) {
            val actualSha256 = try {
                sha256(file)
            } catch (_: Exception) {
                manager.remove(id)
                file.delete()
                verifiedFiles.remove(identity.key)
                preferences.edit().remove(downloadIdPreferenceKey(identity)).commit()
                return UpdateDownloadResult(
                    "failed",
                    id,
                    downloaded,
                    identity.expectedSize,
                    reason = "download_read_failed",
                )
            }
            if (!actualSha256.equals(identity.sha256, ignoreCase = true)) {
                manager.remove(id)
                file.delete()
                verifiedFiles.remove(identity.key)
                preferences.edit().remove(downloadIdPreferenceKey(identity)).commit()
                return UpdateDownloadResult(
                    "failed",
                    id,
                    downloaded,
                    identity.expectedSize,
                    reason = "download_sha256_mismatch",
                )
            }
            verifiedFiles[identity.key] = fileStamp
        }

        return UpdateDownloadResult(
            status = "completed",
            downloadId = id,
            downloaded = identity.expectedSize,
            total = identity.expectedSize,
            localPath = file.absolutePath,
        )
    }

    private fun findRequestId(
        manager: DownloadManager,
        identity: UpdateDownloadIdentity,
    ): Long? {
        val expectedDestination = destinationFor(identity)
        manager.query(DownloadManager.Query()).use { cursor ->
            val idColumn = cursor.getColumnIndexOrThrow(DownloadManager.COLUMN_ID)
            val localUriColumn = cursor.getColumnIndexOrThrow(DownloadManager.COLUMN_LOCAL_URI)
            val uriColumn = cursor.getColumnIndexOrThrow(DownloadManager.COLUMN_URI)
            while (cursor.moveToNext()) {
                val localUri = cursor.getString(localUriColumn)
                if (
                    cursor.getString(uriColumn) == identity.url &&
                    isExpectedDestination(localUri, expectedDestination)
                ) {
                    return cursor.getLong(idColumn)
                }
            }
        }
        return null
    }

    private fun isExpectedDestination(localUri: String?, expectedDestination: File): Boolean {
        if (localUri.isNullOrBlank()) return false
        val uri = Uri.parse(localUri)
        if (uri.scheme != "file") return false
        val downloadedFile = uri.path ?: return false
        return try {
            File(downloadedFile).canonicalFile == expectedDestination.canonicalFile
        } catch (_: Exception) {
            false
        }
    }

    private fun readDownloadId(identity: UpdateDownloadIdentity): Long? {
        val value = preferences.getLong(downloadIdPreferenceKey(identity), NO_DOWNLOAD_ID)
        return value.takeIf { it != NO_DOWNLOAD_ID }
    }

    private fun writeDownloadId(identity: UpdateDownloadIdentity, id: Long): Boolean =
        preferences.edit().putLong(downloadIdPreferenceKey(identity), id).commit()

    private fun downloadIdPreferenceKey(identity: UpdateDownloadIdentity) =
        "download_id_${identity.key}"

    private fun destinationFor(identity: UpdateDownloadIdentity): File {
        val downloads = requireNotNull(context.getExternalFilesDir(Environment.DIRECTORY_DOWNLOADS)) {
            "App-specific update storage is unavailable"
        }.canonicalFile
        check(downloads.isDirectory || downloads.mkdirs()) { "Cannot prepare update storage" }
        return File(downloads, "update-${identity.key}.apk")
    }

    private fun downloadManager(): DownloadManager =
        requireNotNull(context.getSystemService(Context.DOWNLOAD_SERVICE) as? DownloadManager) {
            "Android DownloadManager is unavailable"
        }

    private fun failureReason(reason: Int): String = when (reason) {
        DownloadManager.ERROR_INSUFFICIENT_SPACE -> "insufficient_space"
        DownloadManager.ERROR_FILE_ALREADY_EXISTS -> "destination_exists"
        DownloadManager.ERROR_DEVICE_NOT_FOUND -> "storage_unavailable"
        DownloadManager.ERROR_TOO_MANY_REDIRECTS -> "too_many_redirects"
        DownloadManager.ERROR_UNHANDLED_HTTP_CODE,
        DownloadManager.ERROR_HTTP_DATA_ERROR -> "http_error"
        DownloadManager.ERROR_CANNOT_RESUME,
        DownloadManager.ERROR_FILE_ERROR -> "download_io_error"
        else -> "download_failed"
    }

    companion object {
        private const val PREFERENCES_NAME = "uth_update_downloads"
        private const val APK_MIME_TYPE = "application/vnd.android.package-archive"
        private const val NO_DOWNLOAD_ID = -1L
        private val ACTIVE_OR_COMPLETE_STATUSES = setOf(
            "pending",
            "running",
            "paused",
            "completed",
        )

        fun isApprovedReleaseUrl(value: String): Boolean {
            val uri = try {
                URI(value)
            } catch (_: Exception) {
                return false
            }
            return uri.scheme.equals("https", ignoreCase = true) &&
                uri.host?.equals("github.com", ignoreCase = true) == true &&
                uri.port in setOf(-1, 443) &&
                uri.rawUserInfo == null &&
                uri.rawQuery == null &&
                uri.rawFragment == null &&
                APPROVED_RELEASE_PATH.matches(uri.rawPath.orEmpty())
        }

        private val APPROVED_RELEASE_PATH = Regex(
            "/Chouwzi/UTHelper/releases/download/v[0-9]+\\.[0-9]+\\.[0-9]+/" +
                "[A-Za-z0-9][A-Za-z0-9._-]{0,126}\\.apk"
        )
    }
}

private const val MAX_UPDATE_SIZE_BYTES = 8L * 1024L * 1024L * 1024L

private fun sha256(file: File): String {
    val digest = MessageDigest.getInstance("SHA-256")
    val started = System.nanoTime()
    file.inputStream().use { input ->
        val buffer = ByteArray(64 * 1024)
        while (true) {
            require((System.nanoTime() - started) / 1_000_000 <= 20_000L) {
                "Completed update hash exceeded its deadline"
            }
            val count = input.read(buffer)
            if (count < 0) break
            digest.update(buffer, 0, count)
        }
    }
    return digest.digest().joinToString("") { "%02x".format(it) }
}

private fun sha256Text(bytes: ByteArray): String = MessageDigest.getInstance("SHA-256")
    .digest(bytes)
    .joinToString("") { "%02x".format(it) }
