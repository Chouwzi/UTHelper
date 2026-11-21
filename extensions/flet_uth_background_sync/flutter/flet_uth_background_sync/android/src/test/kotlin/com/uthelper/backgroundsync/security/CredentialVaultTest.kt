package com.uthelper.backgroundsync.security

import android.content.Context
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config
import java.io.File
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [35])
class CredentialVaultTest {
    private class InMemoryVaultKeyProvider : VaultKeyProvider {
        private val keys = linkedMapOf<String, SecretKey>()

        override fun getExistingKey(alias: String): SecretKey? = keys[alias]

        override fun getOrCreateKey(alias: String): SecretKey =
            keys.getOrPut(alias) {
                KeyGenerator.getInstance("AES").apply { init(256) }.generateKey()
            }

        override fun deleteKey(alias: String) {
            keys.remove(alias)
        }
    }

    @Test
    fun credentialVaultRoundTripsValidOriginAndRejectsCleartextOrUntrusted() {
        val context: Context = RuntimeEnvironment.getApplication()
        val vault = CredentialVault(context, InMemoryVaultKeyProvider())
        vault.clear()

        assertThrows(IllegalArgumentException::class.java) {
            vault.write("http://courses.ut.edu.vn", "tok-1")
        }
        assertThrows(IllegalArgumentException::class.java) {
            vault.write("https://user@courses.ut.edu.vn", "tok-1")
        }
        assertThrows(IllegalArgumentException::class.java) {
            vault.write("https://evil.example", "tok-1")
        }

        vault.write("https://courses.ut.edu.vn/", "ws-secret-token")
        val read = vault.read()
        assertNotNull(read)
        assertEquals("https://courses.ut.edu.vn", read!!.baseUrl)
        assertEquals("ws-secret-token", read.token)

        val storageFile = File(context.noBackupFilesDir, "uth_ws_credential.json")
        assertTrue(storageFile.exists())
        assertFalse(storageFile.readText().contains("ws-secret-token"))

        // Tamper stored base_url to legacy http:// -> read() must fail closed (null)
        val payload = JSONObject(storageFile.readText()).put("base_url", "http://courses.ut.edu.vn")
        storageFile.writeText(payload.toString())
        assertNull(vault.read())

        vault.clear()
        assertFalse(storageFile.exists())
    }

    @Test
    fun appSecretVaultEncryptsRoundTripsFailsClosedAndClears() {
        val context: Context = RuntimeEnvironment.getApplication()
        val keyProvider = InMemoryVaultKeyProvider()
        val vault = AppSecretVault(context, keyProvider)
        vault.clear()

        assertEquals(emptyMap<String, String>(), vault.readAll())

        vault.writeAll(
            mapOf(
                "UTH_PASSWORD" to "super-secret-pass",
                "TELEGRAM_BOT_TOKEN" to "123456:ABC-DEF",
                "EMPTY_IGNORED" to "",
                "BLANK_IGNORED" to "   ",
            ),
        )
        val read = vault.readAll()
        assertEquals(
            mapOf(
                "UTH_PASSWORD" to "super-secret-pass",
                "TELEGRAM_BOT_TOKEN" to "123456:ABC-DEF",
            ),
            read,
        )

        val secretsFile = File(context.noBackupFilesDir, "uth_app_secrets.json")
        assertTrue(secretsFile.exists())
        val rawText = secretsFile.readText()
        assertFalse(rawText.contains("super-secret-pass"))
        assertFalse(rawText.contains("123456:ABC-DEF"))

        // Corrupt ciphertext -> fail closed (emptyMap + deletes corrupt file)
        val corrupted = JSONObject(rawText).put("ciphertext", "AAAA")
        secretsFile.writeText(corrupted.toString())
        assertEquals(emptyMap<String, String>(), vault.readAll())
        assertFalse(secretsFile.exists())

        // Write again then clear -> file and key deleted
        vault.writeAll(mapOf("DISCORD_WEBHOOK_URL" to "https://discord.com/api/webhooks/1/abc"))
        assertTrue(secretsFile.exists())
        vault.clear()
        assertFalse(secretsFile.exists())
        assertEquals(emptyMap<String, String>(), vault.readAll())
    }
}
