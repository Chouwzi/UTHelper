package com.uthelper.backgroundsync.update

import android.content.Context
import android.content.Intent
import android.content.pm.PackageInfo
import android.content.pm.PackageManager
import android.content.pm.Signature
import android.net.Uri
import android.os.Build
import android.provider.Settings
import androidx.core.content.FileProvider
import androidx.core.content.pm.PackageInfoCompat
import java.io.File
import java.security.MessageDigest
import java.util.concurrent.CancellationException
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicReference

data class InstallUpdateResult(
    val status: String,
    val stage: String = "",
    val bytes: Long = 0,
    val sha256: String = "",
    val reason: String? = null,
)

data class VerifiedLocalPackage(
    val bytes: Long,
    val sha256: String,
)

data class PreparedUpdatePackage(
    val file: File,
    val verification: VerifiedLocalPackage,
    val reusedCache: Boolean,
)

data class ArchiveMetadata(
    val packageName: String,
    val versionCode: Long,
    val signerSha256: Set<String>,
)

data class InstalledMetadata(
    val versionCode: Long,
    val signerSha256: Set<String>,
)

internal fun normalizedFingerprint(value: String): String {
    val normalized = value.replace(":", "").replace(" ", "").uppercase()
    require(normalized.matches(Regex("[0-9A-F]{64}"))) {
        "Certificate fingerprint must be SHA-256"
    }
    return normalized
}

fun validateArchiveMetadata(
    metadata: ArchiveMetadata,
    expectedPackageId: String,
    expectedVersionCode: Long,
    manifestCertificateSha256: String,
    installed: InstalledMetadata,
) {
    require(metadata.packageName == expectedPackageId) { "APK package ID mismatch" }
    require(metadata.versionCode == expectedVersionCode) { "APK versionCode mismatch" }
    require(metadata.versionCode > installed.versionCode) { "APK is not a newer version" }

    val archiveSigners = metadata.signerSha256.map(::normalizedFingerprint).toSet()
    val installedSigners = installed.signerSha256.map(::normalizedFingerprint).toSet()
    val manifestSigner = normalizedFingerprint(manifestCertificateSha256)
    require(manifestSigner in archiveSigners) { "APK manifest signer mismatch" }
    require(archiveSigners.intersect(installedSigners).isNotEmpty()) {
        "APK signer does not match the installed application"
    }
}

fun copyAndVerifyLocalPackage(
    source: File,
    destination: File,
    expectedSha256: String,
    expectedSize: Long,
    cancelled: AtomicBoolean = AtomicBoolean(false),
): VerifiedLocalPackage {
    require(source.isFile) { "Local update package is unavailable" }
    require(expectedSha256.matches(Regex("[0-9a-fA-F]{64}"))) {
        "A release SHA-256 checksum is required"
    }
    require(expectedSize > 0L) { "A positive release size is required" }
    require(source.length() == expectedSize) { "Update size mismatch" }
    require(source.canonicalFile != destination.canonicalFile) {
        "Local update source overlaps native staging"
    }
    check(destination.parentFile?.mkdirs() != false || destination.parentFile?.isDirectory == true) {
        "Cannot prepare update cache"
    }
    destination.delete()
    try {
        val digest = MessageDigest.getInstance("SHA-256")
        var copied = 0L
        val started = System.nanoTime()
        source.inputStream().use { input ->
            destination.outputStream().use { output ->
                val buffer = ByteArray(64 * 1024)
                while (true) {
                    checkNotCancelled(cancelled)
                    require((System.nanoTime() - started) / 1_000_000 <= LOCAL_COPY_TIMEOUT_MILLIS) {
                        "Local update copy exceeded total deadline"
                    }
                    val count = input.read(buffer)
                    if (count < 0) break
                    copied += count
                    require(copied <= expectedSize) { "Update exceeds manifest size" }
                    output.write(buffer, 0, count)
                    digest.update(buffer, 0, count)
                }
                output.flush()
                output.fd.sync()
            }
        }
        checkNotCancelled(cancelled)
        val actualSha256 = digest.digest().joinToString("") { "%02x".format(it) }
        require(copied == expectedSize) { "Update size mismatch" }
        require(actualSha256.equals(expectedSha256, ignoreCase = true)) {
            "Update SHA-256 mismatch"
        }
        return VerifiedLocalPackage(copied, actualSha256)
    } catch (exception: Exception) {
        destination.delete()
        throw exception
    }
}

fun prepareVerifiedUpdatePackage(
    localPath: String,
    cachedApk: File,
    partialApk: File,
    expectedSha256: String,
    expectedSize: Long,
    cancelled: AtomicBoolean = AtomicBoolean(false),
): PreparedUpdatePackage {
    if (cachedApk.isFile) {
        try {
            return PreparedUpdatePackage(
                cachedApk,
                verifyExistingPackage(cachedApk, expectedSha256, expectedSize, cancelled),
                reusedCache = true,
            )
        } catch (exception: CancellationException) {
            throw exception
        } catch (_: Exception) {
            cachedApk.delete()
        }
    }

    partialApk.delete()
    val verification = copyAndVerifyLocalPackage(
        source = File(localPath).canonicalFile,
        destination = partialApk,
        expectedSha256 = expectedSha256,
        expectedSize = expectedSize,
        cancelled = cancelled,
    )
    return PreparedUpdatePackage(partialApk, verification, reusedCache = false)
}

private fun verifyExistingPackage(
    file: File,
    expectedSha256: String,
    expectedSize: Long,
    cancelled: AtomicBoolean,
): VerifiedLocalPackage {
    require(file.isFile) { "Local update package is unavailable" }
    require(file.length() == expectedSize) { "Update size mismatch" }
    val digest = MessageDigest.getInstance("SHA-256")
    var copied = 0L
    val started = System.nanoTime()
    file.inputStream().use { input ->
        val buffer = ByteArray(64 * 1024)
        while (true) {
            checkNotCancelled(cancelled)
            require((System.nanoTime() - started) / 1_000_000 <= LOCAL_COPY_TIMEOUT_MILLIS) {
                "Local update verification exceeded total deadline"
            }
            val count = input.read(buffer)
            if (count < 0) break
            copied += count
            require(copied <= expectedSize) { "Update exceeds manifest size" }
            digest.update(buffer, 0, count)
        }
    }
    val actualSha256 = digest.digest().joinToString("") { "%02x".format(it) }
    require(copied == expectedSize) { "Update size mismatch" }
    require(actualSha256.equals(expectedSha256, ignoreCase = true)) {
        "Update SHA-256 mismatch"
    }
    return VerifiedLocalPackage(copied, actualSha256)
}

class ApkUpdateInstaller(private val context: Context) {
    private val currentCancellation = AtomicReference<AtomicBoolean?>(null)

    fun cancel() {
        currentCancellation.get()?.set(true)
    }

    fun verifyLocalAndOpen(
        localPath: String,
        expectedSha256: String,
        expectedSize: Long,
        expectedPackageId: String,
        expectedVersionCode: Long,
        expectedCertificateSha256: String,
    ): InstallUpdateResult {
        var stage = "release_metadata_validation"
        var keepCachedApk = false
        var apk: File? = null
        var partial: File? = null
        val cancelled = AtomicBoolean(false)
        if (!currentCancellation.compareAndSet(null, cancelled)) {
            return InstallUpdateResult(
                status = "failed",
                stage = "installer_handoff",
                reason = "installer_busy",
            )
        }

        try {
            require(expectedSha256.matches(Regex("[0-9a-fA-F]{64}"))) {
                "A release SHA-256 checksum is required"
            }
            require(expectedSize > 0L) { "A positive release size is required" }
            require(expectedPackageId == context.packageName) { "Unexpected package identity" }
            require(expectedVersionCode > 0L) { "A positive versionCode is required" }
            normalizedFingerprint(expectedCertificateSha256)

            val directory = File(context.cacheDir, "verified_updates").apply {
                check(mkdirs() || isDirectory) { "Cannot prepare update cache" }
            }
            val releaseKey = expectedSha256.lowercase()
            val stagedApk = File(directory, "update-$releaseKey.apk")
            val stagedPartial = File(directory, "update-$releaseKey.apk.part")
            apk = stagedApk
            partial = stagedPartial
            checkNotCancelled(cancelled)

            stage = "downloaded_package_verification"
            val prepared = prepareVerifiedUpdatePackage(
                localPath = localPath,
                cachedApk = stagedApk,
                partialApk = stagedPartial,
                expectedSha256 = expectedSha256,
                expectedSize = expectedSize,
                cancelled = cancelled,
            )
            stage = if (prepared.reusedCache) {
                "cached_package_verification"
            } else {
                "downloaded_package_verification"
            }
            val verified = prepared.verification
            val archivePath = prepared.file

            stage = "archive_metadata_validation"
            val archive = readArchiveMetadata(archivePath)
            val installed = readInstalledMetadata()
            validateArchiveMetadata(
                archive,
                expectedPackageId,
                expectedVersionCode,
                expectedCertificateSha256,
                installed,
            )
            checkNotCancelled(cancelled)
            if (archivePath == stagedPartial) {
                stagedApk.delete()
                check(stagedPartial.renameTo(stagedApk)) { "Cannot finalize verified APK" }
            }
            keepCachedApk = true
            val packageBytes = requireNotNull(verified).bytes
            val packageSha256 = requireNotNull(verified).sha256

            stage = "install_permission"
            if (
                Build.VERSION.SDK_INT >= Build.VERSION_CODES.O &&
                !context.packageManager.canRequestPackageInstalls()
            ) {
                try {
                    context.startActivity(
                        Intent(
                            Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                            Uri.parse("package:${context.packageName}"),
                        ).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                    )
                } catch (_: Exception) {
                    return InstallUpdateResult(
                        status = "failed",
                        stage = stage,
                        bytes = packageBytes,
                        sha256 = packageSha256,
                        reason = "permission_settings_unavailable",
                    )
                }
                return InstallUpdateResult(
                    status = "permission_required",
                    stage = stage,
                    bytes = packageBytes,
                    sha256 = packageSha256,
                    reason = "unknown_sources_permission_required",
                )
            }

            stage = "installer_handoff"
            val uri = FileProvider.getUriForFile(
                context,
                "${context.packageName}.uthupdates",
                stagedApk,
            )
            try {
                context.startActivity(
                    Intent(Intent.ACTION_VIEW).apply {
                        setDataAndType(uri, "application/vnd.android.package-archive")
                        addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                        addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                    }
                )
            } catch (_: Exception) {
                return InstallUpdateResult(
                    status = "failed",
                    stage = stage,
                    bytes = packageBytes,
                    sha256 = packageSha256,
                    reason = "package_installer_unavailable",
                )
            }
            return InstallUpdateResult(
                status = "installer_opened",
                stage = stage,
                bytes = packageBytes,
                sha256 = packageSha256,
            )
        } catch (exception: CancellationException) {
            return InstallUpdateResult(
                status = "cancelled",
                stage = stage,
                reason = "cancelled",
            )
        } catch (exception: Exception) {
            return InstallUpdateResult(
                status = "failed",
                stage = stage,
                reason = installFailureReason(stage, exception),
            )
        } finally {
            partial?.delete()
            if (!keepCachedApk) apk?.delete()
            currentCancellation.compareAndSet(cancelled, null)
        }
    }

    private fun readArchiveMetadata(path: File): ArchiveMetadata {
        val info = packageArchiveInfo(path)
        return ArchiveMetadata(
            packageName = info.packageName.orEmpty(),
            versionCode = PackageInfoCompat.getLongVersionCode(info),
            signerSha256 = signerFingerprints(info),
        )
    }

    private fun installFailureReason(stage: String, exception: Exception): String {
        val message = exception.message.orEmpty()
        return when (stage) {
            "release_metadata_validation" -> "release_metadata_invalid"
            "downloaded_package_verification" -> when {
                message == "Local update package is unavailable" -> "source_unavailable"
                message.contains("size mismatch", ignoreCase = true) -> "size_mismatch"
                message.contains("SHA-256 mismatch", ignoreCase = true) -> "sha256_mismatch"
                message.contains("deadline", ignoreCase = true) -> "verification_timeout"
                else -> "package_staging_failed"
            }
            "archive_metadata_validation" -> when (message) {
                "Cannot parse downloaded APK" -> "invalid_apk"
                "APK package ID mismatch" -> "package_mismatch"
                "APK versionCode mismatch" -> "release_version_mismatch"
                "APK is not a newer version" -> "already_installed"
                "APK manifest signer mismatch" -> "manifest_signer_mismatch"
                "APK signer does not match the installed application" -> "installed_signer_mismatch"
                "APK signing data missing", "APK signer missing" -> "apk_signature_invalid"
                else -> "archive_identity_unavailable"
            }
            "installer_handoff" -> "package_installer_unavailable"
            else -> "update_verification_failed"
        }
    }

    private fun readInstalledMetadata(): InstalledMetadata {
        val info = installedPackageInfo(context.packageName)
        return InstalledMetadata(
            versionCode = PackageInfoCompat.getLongVersionCode(info),
            signerSha256 = signerFingerprints(info),
        )
    }

    private fun packageArchiveInfo(path: File): PackageInfo {
        val manager = context.packageManager
        val info = if (Build.VERSION.SDK_INT >= 33) {
            manager.getPackageArchiveInfo(
                path.absolutePath,
                PackageManager.PackageInfoFlags.of(PackageManager.GET_SIGNING_CERTIFICATES.toLong()),
            )
        } else {
            @Suppress("DEPRECATION")
            manager.getPackageArchiveInfo(path.absolutePath, signingFlags())
        }
        return requireNotNull(info) { "Cannot parse downloaded APK" }
    }

    private fun installedPackageInfo(packageName: String): PackageInfo {
        val manager = context.packageManager
        return if (Build.VERSION.SDK_INT >= 33) {
            manager.getPackageInfo(
                packageName,
                PackageManager.PackageInfoFlags.of(PackageManager.GET_SIGNING_CERTIFICATES.toLong()),
            )
        } else {
            @Suppress("DEPRECATION")
            manager.getPackageInfo(packageName, signingFlags())
        }
    }

    @Suppress("DEPRECATION")
    private fun signingFlags(): Int = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
        PackageManager.GET_SIGNING_CERTIFICATES
    } else {
        PackageManager.GET_SIGNATURES
    }

    @Suppress("DEPRECATION")
    private fun signerFingerprints(info: PackageInfo): Set<String> {
        val signatures: Array<Signature> = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            val signingInfo = requireNotNull(info.signingInfo) { "APK signing data missing" }
            if (signingInfo.hasMultipleSigners()) {
                signingInfo.apkContentsSigners
            } else {
                signingInfo.signingCertificateHistory
            }
        } else {
            info.signatures ?: emptyArray()
        }
        require(signatures.isNotEmpty()) { "APK signer missing" }
        return signatures.map { signature ->
            MessageDigest.getInstance("SHA-256")
                .digest(signature.toByteArray())
                .joinToString("") { byte -> "%02X".format(byte) }
        }.toSet()
    }

}

private const val LOCAL_COPY_TIMEOUT_MILLIS = 180_000L

private fun checkNotCancelled(cancelled: AtomicBoolean) {
    if (cancelled.get()) {
        throw CancellationException("Update installation handoff cancelled")
    }
}
