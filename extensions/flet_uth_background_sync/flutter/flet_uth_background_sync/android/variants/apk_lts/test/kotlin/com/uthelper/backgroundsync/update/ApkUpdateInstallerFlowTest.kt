package com.uthelper.backgroundsync.update

import android.content.Context
import android.content.ContextWrapper
import android.content.ComponentName
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.ActivityInfo
import android.content.pm.PackageManager
import android.content.pm.PackageInfo
import android.content.pm.ProviderInfo
import android.content.pm.Signature
import android.net.Uri
import android.os.Bundle
import android.provider.Settings
import java.io.File
import java.security.MessageDigest
import com.uthelper.backgroundsync.R
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.Shadows
import org.robolectric.annotation.Config
import org.robolectric.annotation.Implementation
import org.robolectric.annotation.Implements
import org.robolectric.shadow.api.Shadow
import org.robolectric.util.ReflectionHelpers.ClassParameter
import androidx.core.content.FileProvider

/**
 * AndroidX FileProvider 1.17.0 `belongsToRoot` hardcodes POSIX `/` after `File.getCanonicalPath()`,
 * which rejects valid child files on Windows JVMs (`\`). Delegate directly on POSIX/CI (`/`), and
 * mirror the exact `@xml/uth_update_paths` `cache-path` containment check on Windows hosts.
 */
@Implements(FileProvider::class)
class PosixSafeFileProviderShadow {
    companion object {
        @JvmStatic
        @Implementation
        fun getUriForFile(context: Context, authority: String, file: File): Uri {
            if (File.separatorChar == '/') {
                return Shadow.directlyOn(
                    FileProvider::class.java,
                    "getUriForFile",
                    ClassParameter.from(Context::class.java, context),
                    ClassParameter.from(String::class.java, authority),
                    ClassParameter.from(File::class.java, file),
                )
            }
            val resolved = context.packageManager.resolveContentProvider(
                authority,
                PackageManager.GET_META_DATA,
            ) ?: throw IllegalArgumentException("Missing provider for $authority")
            val root = File(context.cacheDir, "verified_updates").canonicalFile
            val target = file.canonicalFile
            val rootPrefix = root.path.trimEnd('\\', '/') + File.separator
            require(target.path.startsWith(rootPrefix)) {
                "Failed to find configured root that contains $target"
            }
            val rel = target.path.substring(rootPrefix.length).replace('\\', '/')
            return Uri.parse("content://${resolved.authority}/verified_updates/$rel")
        }
    }
}

/** Robolectric simulates Android framework metadata and activity handoff, not APK parsing/install. */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [27], shadows = [PosixSafeFileProviderShadow::class])
class ApkUpdateInstallerFlowTest {
    private val archiveBytes = "platform handoff fixture".toByteArray()
    private val signerBytes = "UTHelper signer fixture".toByteArray()

    @Test
    fun validatesPinnedReleaseThenOpensPackageInstallerIntent() {
        val app = RuntimeEnvironment.getApplication()
        val context: Context = app
        val packageFile = fixturePackage(context)
        val identity = installIdentity(context, packageFile)
        registerInstallerProvider(context)
        val installer = ApkUpdateInstaller(context)
        Shadows.shadowOf(context.packageManager).setCanRequestPackageInstalls(true)

        val result = installer.verifyLocalAndOpen(
            localPath = packageFile.absolutePath,
            expectedSha256 = identity.sha256,
            expectedSize = archiveBytes.size.toLong(),
            expectedPackageId = context.packageName,
            expectedVersionCode = identity.versionCode,
            expectedCertificateSha256 = identity.certificateSha256,
        )

        assertEquals("stage=${result.stage}, reason=${result.reason}", "installer_opened", result.status)
        assertEquals("installer_handoff", result.stage)
        assertEquals(archiveBytes.size.toLong(), result.bytes)
        assertEquals(identity.sha256, result.sha256)
        val stagedPartial = File(
            File(context.cacheDir, "verified_updates"),
            "update-${identity.sha256}.apk.part",
        )
        assertFalse(stagedPartial.exists())
        val launch = Shadows.shadowOf(app).getNextStartedActivity()
        assertNotNull(launch)
        assertEquals(Intent.ACTION_VIEW, launch!!.action)
        assertEquals("application/vnd.android.package-archive", launch.type)
        assertTrue(launch.data.toString().startsWith("content://"))
        assertTrue(
            launch.flags and Intent.FLAG_GRANT_READ_URI_PERMISSION != 0
        )
        assertTrue(
            launch.flags and Intent.FLAG_ACTIVITY_NEW_TASK != 0
        )
    }

    @Test
    fun permissionReturnRetriesTheSameVerifiedCacheAfterSourceIsGone() {
        val app = RuntimeEnvironment.getApplication()
        val context: Context = app
        val packageFile = fixturePackage(context)
        val identity = installIdentity(context, packageFile)
        registerInstallerProvider(context)
        val installer = ApkUpdateInstaller(context)
        val shadowPackageManager = Shadows.shadowOf(context.packageManager)
        shadowPackageManager.setCanRequestPackageInstalls(false)

        val permission = installer.verifyLocalAndOpen(
            localPath = packageFile.absolutePath,
            expectedSha256 = identity.sha256,
            expectedSize = archiveBytes.size.toLong(),
            expectedPackageId = context.packageName,
            expectedVersionCode = identity.versionCode,
            expectedCertificateSha256 = identity.certificateSha256,
        )

        assertEquals("permission_required", permission.status)
        assertEquals("install_permission", permission.stage)
        val settingsIntent = Shadows.shadowOf(app).getNextStartedActivity()
        assertNotNull(settingsIntent)
        assertEquals(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, settingsIntent!!.action)
        assertEquals(Uri.parse("package:${context.packageName}"), settingsIntent.data)

        val staged = File(
            File(context.cacheDir, "verified_updates"),
            "update-${identity.sha256}.apk",
        )
        assertTrue(staged.isFile)
        packageFile.delete()
        shadowPackageManager.setCanRequestPackageInstalls(true)

        val retried = installer.verifyLocalAndOpen(
            localPath = "",
            expectedSha256 = identity.sha256,
            expectedSize = archiveBytes.size.toLong(),
            expectedPackageId = context.packageName,
            expectedVersionCode = identity.versionCode,
            expectedCertificateSha256 = identity.certificateSha256,
        )

        assertEquals("stage=${retried.stage}, reason=${retried.reason}", "installer_opened", retried.status)
        assertEquals("installer_handoff", retried.stage)
        assertEquals(identity.sha256, retried.sha256)
        val installerIntent = Shadows.shadowOf(app).getNextStartedActivity()
        assertNotNull(installerIntent)
        assertEquals(Intent.ACTION_VIEW, installerIntent!!.action)
        assertEquals("application/vnd.android.package-archive", installerIntent.type)
    }

    @Test
    fun rejectsInvalidArchiveMetadataAndDeletesStagedApk() {
        val app = RuntimeEnvironment.getApplication()
        val context: Context = app
        val packageFile = fixturePackage(context)
        val identity = installIdentity(context, packageFile)
        registerInstallerProvider(context)
        val cache = File(context.cacheDir, "verified_updates")
        val stagedApk = File(cache, "update-${identity.sha256}.apk")
        val stagedPartial = File(cache, "update-${identity.sha256}.apk.part")
        val wrongArchive = PackageInfo().apply {
            packageName = "com.example.wrong"
            versionCode = identity.versionCode.toInt()
            signatures = arrayOf(Signature(signerBytes))
        }
        Shadows.shadowOf(context.packageManager)
            .setPackageArchiveInfo(stagedApk.canonicalPath, wrongArchive)

        val installer = ApkUpdateInstaller(context)
        val result = installer.verifyLocalAndOpen(
            localPath = packageFile.absolutePath,
            expectedSha256 = identity.sha256,
            expectedSize = archiveBytes.size.toLong(),
            expectedPackageId = context.packageName,
            expectedVersionCode = identity.versionCode,
            expectedCertificateSha256 = identity.certificateSha256,
        )

        assertEquals("failed", result.status)
        assertEquals("archive_metadata_validation", result.stage)
        assertEquals("package_mismatch", result.reason)
        assertFalse(stagedApk.exists())
        assertFalse(stagedPartial.exists())
    }

    @Test
    fun cancellationDuringExistingCacheVerificationPreservesPreExistingCachedApk() {
        val context: Context = RuntimeEnvironment.getApplication()
        val packageFile = fixturePackage(context)
        val identity = installIdentity(context, packageFile)
        val cache = File(context.cacheDir, "verified_updates").apply { mkdirs() }
        val stagedApk = File(cache, "update-${identity.sha256}.apk").apply {
            writeBytes(archiveBytes)
        }

        val installer = ApkUpdateInstaller(context)
        installer.onBeforeCancelCheckForTest = { stage ->
            if (stage == "downloaded_package_verification") {
                installer.cancel()
            }
        }
        val result = installer.verifyLocalAndOpen(
            localPath = "",
            expectedSha256 = identity.sha256,
            expectedSize = archiveBytes.size.toLong(),
            expectedPackageId = context.packageName,
            expectedVersionCode = identity.versionCode,
            expectedCertificateSha256 = identity.certificateSha256,
        )

        assertEquals("cancelled", result.status)
        assertEquals("downloaded_package_verification", result.stage)
        assertTrue(stagedApk.isFile)
    }

    @Test
    fun cleansUpOlderStaleApksWhilePreservingActiveDownloadsAndCurrentCache() {
        val context: Context = RuntimeEnvironment.getApplication()
        val packageFile = fixturePackage(context)
        val identity = installIdentity(context, packageFile)
        Shadows.shadowOf(context.packageManager).setCanRequestPackageInstalls(false)

        val cache = File(context.cacheDir, "verified_updates").apply { mkdirs() }
        val staleCachedApk = File(cache, "update-old000.apk").apply { writeBytes(byteArrayOf(1, 2, 3)) }
        val staleCachedPart = File(cache, "update-old000.apk.part").apply { writeBytes(byteArrayOf(4, 5)) }

        val externalDownloads = context.getExternalFilesDir(android.os.Environment.DIRECTORY_DOWNLOADS)!!
        externalDownloads.mkdirs()
        val staleExternalApk = File(externalDownloads, "update-old000.apk").apply {
            writeBytes(byteArrayOf(6, 7, 8))
        }
        val activeExternalApk = File(externalDownloads, "update-active111.apk").apply {
            writeBytes(byteArrayOf(9, 10, 11))
        }
        val manager = context.getSystemService(Context.DOWNLOAD_SERVICE) as android.app.DownloadManager
        val activeReq = android.app.DownloadManager.Request(
            Uri.parse("https://github.com/Chouwzi/UTHelper/releases/download/v2.5.2/UTHelper.apk")
        ).apply {
            setDestinationInExternalFilesDir(
                context,
                android.os.Environment.DIRECTORY_DOWNLOADS,
                "update-active111.apk",
            )
        }
        manager.enqueue(activeReq)
        Shadows.shadowOf(activeReq).setStatus(android.app.DownloadManager.STATUS_RUNNING)

        val result = ApkUpdateInstaller(context).verifyLocalAndOpen(
            localPath = packageFile.absolutePath,
            expectedSha256 = identity.sha256,
            expectedSize = archiveBytes.size.toLong(),
            expectedPackageId = context.packageName,
            expectedVersionCode = identity.versionCode,
            expectedCertificateSha256 = identity.certificateSha256,
        )

        assertEquals("permission_required", result.status)
        assertFalse(staleCachedApk.exists())
        assertFalse(staleCachedPart.exists())
        assertFalse(staleExternalApk.exists())
        assertTrue(activeExternalApk.exists())
        assertTrue(File(cache, "update-${identity.sha256}.apk").isFile)
    }

    @Test
    @Config(shadows = [NullCursorShadowDownloadManager::class])
    fun preservesExternalUpdateApksWhenDownloadManagerQueryReturnsNull() {
        val context: Context = RuntimeEnvironment.getApplication()
        val packageFile = fixturePackage(context)
        val identity = installIdentity(context, packageFile)
        Shadows.shadowOf(context.packageManager).setCanRequestPackageInstalls(false)

        val externalDownloads = context.getExternalFilesDir(android.os.Environment.DIRECTORY_DOWNLOADS)!!
        externalDownloads.mkdirs()
        val activeExternalApk = File(externalDownloads, "update-active111.apk").apply {
            writeBytes(byteArrayOf(9, 10, 11))
        }
        val otherExternalApk = File(externalDownloads, "update-other222.apk").apply {
            writeBytes(byteArrayOf(12, 13, 14))
        }

        val result = ApkUpdateInstaller(context).verifyLocalAndOpen(
            localPath = packageFile.absolutePath,
            expectedSha256 = identity.sha256,
            expectedSize = archiveBytes.size.toLong(),
            expectedPackageId = context.packageName,
            expectedVersionCode = identity.versionCode,
            expectedCertificateSha256 = identity.certificateSha256,
        )

        assertEquals("permission_required", result.status)
        assertTrue(activeExternalApk.exists())
        assertTrue(otherExternalApk.exists())
    }

    @Test
    fun preservesExternalUpdateApksWhenDownloadManagerServiceIsNull() {
        val baseContext: Context = RuntimeEnvironment.getApplication()
        val packageFile = fixturePackage(baseContext)
        val identity = installIdentity(baseContext, packageFile)
        Shadows.shadowOf(baseContext.packageManager).setCanRequestPackageInstalls(false)

        val externalDownloads = baseContext.getExternalFilesDir(android.os.Environment.DIRECTORY_DOWNLOADS)!!
        externalDownloads.mkdirs()
        val activeExternalApk = File(externalDownloads, "update-active111.apk").apply {
            writeBytes(byteArrayOf(9, 10, 11))
        }
        val nullDownloadManagerContext = object : ContextWrapper(baseContext) {
            override fun getSystemService(name: String): Any? =
                if (name == Context.DOWNLOAD_SERVICE) null else super.getSystemService(name)
        }

        val result = ApkUpdateInstaller(nullDownloadManagerContext).verifyLocalAndOpen(
            localPath = packageFile.absolutePath,
            expectedSha256 = identity.sha256,
            expectedSize = archiveBytes.size.toLong(),
            expectedPackageId = baseContext.packageName,
            expectedVersionCode = identity.versionCode,
            expectedCertificateSha256 = identity.certificateSha256,
        )

        assertEquals("permission_required", result.status)
        assertTrue(activeExternalApk.exists())
    }

    private fun fixturePackage(context: Context): File =
        File(context.cacheDir, "test-release.apk").apply {
            writeBytes(archiveBytes)
        }.canonicalFile

    private fun registerInstallerProvider(context: Context) {
        val authority = "${context.packageName}.uthupdates"
        val provider = ProviderInfo().apply {
            packageName = context.packageName
            name = UthUpdateFileProvider::class.java.name
            this.authority = authority
            exported = false
            grantUriPermissions = true
            applicationInfo = context.applicationInfo
            metaData = Bundle().apply {
                putInt("android.support.FILE_PROVIDER_PATHS", R.xml.uth_update_paths)
            }
        }
        val shadowPackageManager = Shadows.shadowOf(context.packageManager)
        // Standalone library tests have no consumer applicationId to resolve this manifest value.
        shadowPackageManager.addOrUpdateProvider(provider)
        // Mirror Android provider initialization; FileProvider.attachInfo clears its static
        // per-authority path cache before resolving this test Context's cache directory.
        UthUpdateFileProvider().attachInfo(context, provider)
        val installerActivity = ActivityInfo().apply {
            packageName = context.packageName
            name = "${context.packageName}.TestPackageInstallerActivity"
            applicationInfo = context.applicationInfo
        }
        val installerComponent = ComponentName(context.packageName, installerActivity.name)
        shadowPackageManager.addOrUpdateActivity(installerActivity)
        shadowPackageManager.addIntentFilterForActivity(
            installerComponent,
            IntentFilter(Intent.ACTION_VIEW).apply {
                addCategory(Intent.CATEGORY_DEFAULT)
                addDataScheme("content")
                addDataType("application/vnd.android.package-archive")
            },
        )
        val resolved = context.packageManager.resolveContentProvider(
            authority,
            PackageManager.GET_META_DATA,
        )
        assertNotNull("unit-test provider metadata for $authority", resolved)
        assertEquals(
            R.xml.uth_update_paths,
            resolved!!.metaData.getInt("android.support.FILE_PROVIDER_PATHS"),
        )
        assertNotNull(
            "test provider XML for ${resolved.authority}",
            resolved.loadXmlMetaData(
                context.packageManager,
                "android.support.FILE_PROVIDER_PATHS",
            ),
        )
    }

    private fun installIdentity(context: Context, packageFile: File): InstallIdentity {
        val signature = Signature(signerBytes)
        val installed = PackageInfo().apply {
            packageName = context.packageName
            versionCode = 2_005_000
            signatures = arrayOf(signature)
        }
        val archive = PackageInfo().apply {
            packageName = context.packageName
            versionCode = 2_005_001
            signatures = arrayOf(signature)
        }
        val packageManager = Shadows.shadowOf(context.packageManager)
        packageManager.installPackage(installed)
        val cache = File(context.cacheDir, "verified_updates")
        val releaseKey = sha256(archiveBytes)
        packageManager.setPackageArchiveInfo(
            File(cache, "update-$releaseKey.apk").canonicalPath,
            archive,
        )
        return InstallIdentity(
            sha256(archiveBytes),
            archive.versionCode.toLong(),
            sha256(signature.toByteArray()),
        )
    }

    private fun sha256(bytes: ByteArray): String = MessageDigest.getInstance("SHA-256")
        .digest(bytes)
        .joinToString("") { "%02x".format(it) }

    private data class InstallIdentity(
        val sha256: String,
        val versionCode: Long,
        val certificateSha256: String,
    )
}
