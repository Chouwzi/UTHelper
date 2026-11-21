package com.uthelper.backgroundsync.update

import android.content.Context
import com.uthelper.backgroundsync.UpdateBridgeDelegate
import io.flutter.plugin.common.MethodCall
import io.flutter.plugin.common.MethodChannel

/**
 * GitHub LTS (`apk_lts`) self-update MethodChannel handler.
 *
 * Excluded from `chplay` builds via Gradle sourceSets and build-time staging so no
 * APK downloader, package installer, or `REQUEST_INSTALL_PACKAGES` code enters Play DEX.
 */
internal class ApkLtsUpdateBridgeDelegate(private val context: Context) : UpdateBridgeDelegate {
    private val updateInstaller by lazy { ApkUpdateInstaller(context) }
    private val updateDownloader by lazy { ApkUpdateDownloadManager(context) }

    override fun handleMethodCall(
        call: MethodCall,
        result: MethodChannel.Result,
        execute: (MethodChannel.Result, suspend () -> Any?) -> Unit,
    ): Boolean {
        when (call.method) {
            "install_update" -> execute(result) {
                val update = updateInstaller.verifyLocalAndOpen(
                    localPath = call.argument<String>("local_path").orEmpty(),
                    expectedSha256 = call.argument<String>("sha256").orEmpty(),
                    expectedSize = call.argument<Number>("expected_size")?.toLong() ?: 0L,
                    expectedPackageId = call.argument<String>("expected_package_id").orEmpty(),
                    expectedVersionCode = call.argument<Number>("expected_version_code")?.toLong() ?: 0L,
                    expectedCertificateSha256 = call.argument<String>("expected_certificate_sha256").orEmpty(),
                )
                mapOf(
                    "status" to update.status,
                    "stage" to update.stage,
                    "bytes" to update.bytes,
                    "sha256" to update.sha256,
                    "reason" to update.reason,
                )
            }
            "start_update_download" -> execute(result) {
                updateDownloader.start(updateIdentity(call)).toMap()
            }
            "get_update_download_status" -> execute(result) {
                updateDownloader.status(updateIdentity(call)).toMap()
            }
            "cancel_update_download" -> execute(result) {
                updateDownloader.cancel(updateIdentity(call))
                null
            }
            "cancel_update" -> {
                updateInstaller.cancel()
                result.success(null)
            }
            else -> return false
        }
        return true
    }

    override fun cancel() {
        updateInstaller.cancel()
    }

    private fun updateIdentity(call: MethodCall): UpdateDownloadIdentity =
        UpdateDownloadIdentity(
            url = call.argument<String>("url").orEmpty(),
            sha256 = call.argument<String>("sha256").orEmpty(),
            expectedSize = call.argument<Number>("expected_size")?.toLong() ?: 0L,
            expectedPackageId = call.argument<String>("expected_package_id").orEmpty(),
            expectedVersionCode = call.argument<Number>("expected_version_code")?.toLong() ?: 0L,
            expectedCertificateSha256 =
                call.argument<String>("expected_certificate_sha256").orEmpty(),
        )

    private fun UpdateDownloadResult.toMap(): Map<String, Any?> =
        mapOf(
            "status" to status,
            "download_id" to downloadId,
            "downloaded" to downloaded,
            "total" to total,
            "local_path" to localPath,
            "reason" to reason,
        )
}
