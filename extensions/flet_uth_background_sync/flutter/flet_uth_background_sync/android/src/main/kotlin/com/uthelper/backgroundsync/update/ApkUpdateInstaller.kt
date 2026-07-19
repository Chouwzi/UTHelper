package com.uthelper.backgroundsync.update

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.provider.Settings
import androidx.core.content.FileProvider
import java.io.File
import java.net.HttpURLConnection
import java.net.URI
import java.security.MessageDigest

data class InstallUpdateResult(
    val status: String,
    val bytes: Long = 0,
    val sha256: String = "",
)

class ApkUpdateInstaller(private val context: Context) {
    fun downloadVerifyAndOpen(
        url: String,
        expectedSha256: String,
        expectedSize: Long,
    ): InstallUpdateResult {
        require(url.startsWith("https://")) { "Update URL must use HTTPS" }
        require(expectedSha256.matches(Regex("[0-9a-fA-F]{64}"))) {
            "A release SHA-256 checksum is required"
        }
        val directory = File(context.cacheDir, "verified_updates").apply { mkdirs() }
        val partial = File(directory, "UTHelper-update.apk.part")
        val apk = File(directory, "UTHelper-update.apk")
        partial.delete()
        apk.delete()

        val digest = MessageDigest.getInstance("SHA-256")
        var downloaded = 0L
        val connection = URI(url).toURL().openConnection() as HttpURLConnection
        try {
            connection.instanceFollowRedirects = true
            connection.connectTimeout = 20_000
            connection.readTimeout = 120_000
            connection.setRequestProperty("User-Agent", "UTHelper-Android-Updater/2.2")
            val response = connection.responseCode
            require(response in 200..299) { "Update download failed with HTTP $response" }
            connection.inputStream.use { input ->
                partial.outputStream().use { output ->
                    val buffer = ByteArray(64 * 1024)
                    while (true) {
                        val count = input.read(buffer)
                        if (count < 0) break
                        output.write(buffer, 0, count)
                        digest.update(buffer, 0, count)
                        downloaded += count
                    }
                    output.flush()
                    output.fd.sync()
                }
            }
        } finally {
            connection.disconnect()
        }

        val actualSha256 = digest.digest().joinToString("") { "%02x".format(it) }
        if (expectedSize > 0 && downloaded != expectedSize) {
            partial.delete()
            error("Update size mismatch")
        }
        if (!actualSha256.equals(expectedSha256, ignoreCase = true)) {
            partial.delete()
            error("Update SHA-256 mismatch")
        }
        check(partial.renameTo(apk)) { "Cannot finalize verified APK" }

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O &&
            !context.packageManager.canRequestPackageInstalls()
        ) {
            context.startActivity(
                Intent(
                    Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                    Uri.parse("package:${context.packageName}"),
                ).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            )
            return InstallUpdateResult("permission_required", downloaded, actualSha256)
        }

        val uri = FileProvider.getUriForFile(
            context,
            "${context.packageName}.uthupdates",
            apk,
        )
        context.startActivity(
            Intent(Intent.ACTION_VIEW).apply {
                setDataAndType(uri, "application/vnd.android.package-archive")
                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
            }
        )
        return InstallUpdateResult("installer_opened", downloaded, actualSha256)
    }
}
