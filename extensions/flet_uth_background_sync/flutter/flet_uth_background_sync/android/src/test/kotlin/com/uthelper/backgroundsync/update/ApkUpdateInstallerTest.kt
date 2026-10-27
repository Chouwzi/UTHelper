package com.uthelper.backgroundsync.update

import java.nio.file.Files
import org.junit.Assert.assertEquals
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
}
