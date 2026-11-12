package com.uthelper.backgroundsync.update

import android.content.Context
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
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.Shadows
import org.robolectric.annotation.Config

/** Robolectric simulates Android framework metadata and activity handoff, not APK parsing/install. */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [27])
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
        packageManager.setPackageArchiveInfo(packageFile.absolutePath, archive)
        val cache = File(context.cacheDir, "verified_updates")
        val releaseKey = sha256(archiveBytes)
        packageManager.setPackageArchiveInfo(
            File(cache, "update-$releaseKey.apk.part").canonicalPath,
            archive,
        )
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
