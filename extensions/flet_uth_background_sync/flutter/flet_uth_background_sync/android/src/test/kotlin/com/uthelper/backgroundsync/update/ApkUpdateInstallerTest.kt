package com.uthelper.backgroundsync.update

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
}
