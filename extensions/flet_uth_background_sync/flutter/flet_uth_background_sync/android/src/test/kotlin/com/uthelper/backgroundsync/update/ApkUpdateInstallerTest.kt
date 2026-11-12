package com.uthelper.backgroundsync.update

import java.nio.file.Files
import java.util.concurrent.CancellationException
import java.util.concurrent.atomic.AtomicBoolean
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assert.assertThrows
import org.junit.Test

class ApkUpdateInstallerTest {
    private val trusted = "AB".repeat(32)
    private val valid = ArchiveMetadata(
        packageName = "com.uthelper.uthelper",
        versionCode = 2_002_000,
        signerSha256 = setOf(trusted),
    )
    private val installed = InstalledMetadata(
        versionCode = 2_001_000,
        signerSha256 = setOf(trusted),
    )

    @Test
    fun acceptsExactIdentityNewerVersionAndInstalledSigner() {
        validateArchiveMetadata(
            valid,
            "com.uthelper.uthelper",
            2_002_000,
            trusted,
            installed,
        )
    }

    @Test
    fun rejectsWrongPackageOldVersionAndUnexpectedSigner() {
        assertThrows(IllegalArgumentException::class.java) {
            validateArchiveMetadata(
                valid.copy(packageName = "example.attacker"),
                "com.uthelper.uthelper",
                2_002_000,
                trusted,
                installed,
            )
        }
        assertThrows(IllegalArgumentException::class.java) {
            validateArchiveMetadata(
                valid.copy(versionCode = 2_001_000),
                "com.uthelper.uthelper",
                2_002_000,
                trusted,
                installed,
            )
        }
        assertThrows(IllegalArgumentException::class.java) {
            validateArchiveMetadata(
                valid.copy(signerSha256 = setOf("CD".repeat(32))),
                "com.uthelper.uthelper",
                2_002_000,
                trusted,
                installed,
            )
        }
    }

    @Test
    fun tamperedManifestAndAttackerArchiveCannotRedefineInstalledTrust() {
        val attacker = "CD".repeat(32)
        val attackerArchive = ArchiveMetadata(
            "com.uthelper.uthelper",
            2_002_000,
            setOf(attacker),
        )
        assertThrows(IllegalArgumentException::class.java) {
            validateArchiveMetadata(
                attackerArchive,
                "com.uthelper.uthelper",
                2_002_000,
                attacker,
                installed,
            )
        }
    }

    @Test
    fun acceptsAndroidSigningLineageContainingInstalledAndManifestSigners() {
        val next = "CD".repeat(32)
        validateArchiveMetadata(
            valid.copy(signerSha256 = setOf(trusted, next)),
            "com.uthelper.uthelper",
            2_002_000,
            next,
            installed,
        )
    }

    @Test
    fun copiesAndVerifiesOneLocalApkWithoutNetworkTransport() {
        val directory = Files.createTempDirectory("uthelper-local-apk-test").toFile()
        try {
            val source = directory.resolve("source.apk").apply {
                writeBytes("verified apk".toByteArray())
            }
            val staged = directory.resolve("staged.apk.part")

            val verified = copyAndVerifyLocalPackage(
                source = source,
                destination = staged,
                expectedSha256 =
                    "97bd5a88552319b46e3d5ed350451b18a7558170aa11d7947452c62365c75403",
                expectedSize = 12,
            )

            assertEquals(12L, verified.bytes)
            assertEquals(
                "97bd5a88552319b46e3d5ed350451b18a7558170aa11d7947452c62365c75403",
                verified.sha256,
            )
            assertEquals("verified apk", staged.readText())
        } finally {
            directory.deleteRecursively()
        }
    }

    @Test
    fun rejectsTamperedLocalApkAndRemovesStagedBytes() {
        val directory = Files.createTempDirectory("uthelper-local-apk-test").toFile()
        try {
            val source = directory.resolve("source.apk").apply {
                writeBytes("tampered".toByteArray())
            }
            val staged = directory.resolve("staged.apk.part")

            assertThrows(IllegalArgumentException::class.java) {
                copyAndVerifyLocalPackage(
                    source = source,
                    destination = staged,
                    expectedSha256 = "0".repeat(64),
                    expectedSize = 8,
                )
            }
            assertEquals(false, staged.exists())
        } finally {
            directory.deleteRecursively()
        }
    }

    @Test
    fun permissionRetryCanReuseSameVerifiedCacheAfterSourceDisappears() {
        val directory = Files.createTempDirectory("uthelper-permission-retry-test").toFile()
        try {
            val contents = "verified permission retry".toByteArray()
            val expectedSha256 = sha256(contents)
            val source = directory.resolve("download-manager.apk").apply {
                writeBytes(contents)
            }
            val cached = directory.resolve("update-$expectedSha256.apk")
            val partial = directory.resolve("update-$expectedSha256.apk.part")

            val first = prepareVerifiedUpdatePackage(
                localPath = source.absolutePath,
                cachedApk = cached,
                partialApk = partial,
                expectedSha256 = expectedSha256,
                expectedSize = contents.size.toLong(),
            )
            assertFalse(first.reusedCache)
            assertEquals(partial, first.file)
            assertTrue(partial.renameTo(cached))
            source.delete()

            val retry = prepareVerifiedUpdatePackage(
                localPath = "",
                cachedApk = cached,
                partialApk = partial,
                expectedSha256 = expectedSha256,
                expectedSize = contents.size.toLong(),
            )
            assertTrue(retry.reusedCache)
            assertEquals(cached, retry.file)
            assertEquals(contents.size.toLong(), retry.verification.bytes)
            assertEquals(expectedSha256, retry.verification.sha256)
        } finally {
            directory.deleteRecursively()
        }
    }

    @Test
    fun permissionRetryRejectsTamperedCacheWhenOriginalSourceIsGone() {
        val directory = Files.createTempDirectory("uthelper-permission-retry-test").toFile()
        try {
            val contents = "verified permission retry".toByteArray()
            val expectedSha256 = sha256(contents)
            val cached = directory.resolve("update-$expectedSha256.apk").apply {
                writeBytes(contents)
            }
            val partial = directory.resolve("update-$expectedSha256.apk.part")
            cached.writeBytes("tampered package contents".toByteArray())

            assertThrows(IllegalArgumentException::class.java) {
                prepareVerifiedUpdatePackage(
                    localPath = "",
                    cachedApk = cached,
                    partialApk = partial,
                    expectedSha256 = expectedSha256,
                    expectedSize = contents.size.toLong(),
                )
            }
            assertFalse(cached.exists())
            assertFalse(partial.exists())
        } finally {
            directory.deleteRecursively()
        }
    }

    @Test
    fun cancellationDuringCachedVerificationPreservesValidCache() {
        val directory = Files.createTempDirectory("uthelper-cancel-cache-test").toFile()
        try {
            val contents = "verified permission retry".toByteArray()
            val expectedSha256 = sha256(contents)
            val cached = directory.resolve("update-$expectedSha256.apk").apply {
                writeBytes(contents)
            }
            val partial = directory.resolve("update-$expectedSha256.apk.part")

            assertThrows(CancellationException::class.java) {
                prepareVerifiedUpdatePackage(
                    localPath = "",
                    cachedApk = cached,
                    partialApk = partial,
                    expectedSha256 = expectedSha256,
                    expectedSize = contents.size.toLong(),
                    cancelled = AtomicBoolean(true),
                )
            }
            assertTrue(cached.exists())
            assertFalse(partial.exists())
        } finally {
            directory.deleteRecursively()
        }
    }

    private fun sha256(bytes: ByteArray): String = java.security.MessageDigest.getInstance("SHA-256")
        .digest(bytes)
        .joinToString("") { "%02x".format(it) }
}
