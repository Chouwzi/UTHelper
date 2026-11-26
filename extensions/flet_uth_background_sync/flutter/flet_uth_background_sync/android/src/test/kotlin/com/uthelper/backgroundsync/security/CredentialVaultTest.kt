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
import java.io.IOException
import java.security.KeyStoreException
import java.security.UnrecoverableKeyException
import java.util.concurrent.Callable
import java.util.concurrent.Executors
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

    @Test
    fun appSecretVaultPreservesFileOnTransientIoException() {
        val context: Context = RuntimeEnvironment.getApplication()
        val keyProvider = InMemoryVaultKeyProvider()
        val vault = AppSecretVault(context, keyProvider)
        vault.clear()

        val secretsFile = File(context.noBackupFilesDir, "uth_app_secrets.json")
        // Replace target with a directory so readText() throws IOException without deleting it.
        assertTrue(secretsFile.mkdirs())
        try {
            assertThrows(IOException::class.java) {
                vault.readAll()
            }
            assertTrue(secretsFile.exists())
        } finally {
            secretsFile.delete()
        }
    }

    @Test
    fun multiInstanceVaultWritesAreSerializedProcessWideWithoutTempFileRaces() {
        val context: Context = RuntimeEnvironment.getApplication()
        val keyProvider = InMemoryVaultKeyProvider()
        AppSecretVault(context, keyProvider).clear()
        CredentialVault(context, keyProvider).clear()

        val pool = Executors.newFixedThreadPool(6)
        try {
            val tasks = (1..24).map { idx ->
                Callable {
                    val secretVault = AppSecretVault(context, keyProvider)
                    secretVault.writeAll(mapOf("UTH_PASSWORD" to "pw-$idx"))
                    val readSecrets = secretVault.readAll()
                    assertTrue(readSecrets["UTH_PASSWORD"]?.startsWith("pw-") == true)

                    val credVault = CredentialVault(context, keyProvider)
                    credVault.write("https://courses.ut.edu.vn", "tok-$idx")
                    val readCred = credVault.read()
                    assertNotNull(readCred)
                    assertTrue(readCred!!.token.startsWith("tok-"))
                }
            }
            pool.invokeAll(tasks).forEach { it.get() }
        } finally {
            pool.shutdownNow()
        }

        val leftovers = context.noBackupFilesDir
            .listFiles()
            ?.filter { it.name.endsWith(".tmp") }
            .orEmpty()
        assertTrue(leftovers.isEmpty())
    }

    @Test
    fun appSecretVaultEvictsCorruptedKeystoreAliasOnGeneralSecurityExceptionAndSurvivesDeleteKeyFailure() {
        val context: Context = RuntimeEnvironment.getApplication()
        val deletedAliases = mutableListOf<String>()
        var corruptedAlias: String? = null
        var throwOnDelete = false

        val keyProvider = object : VaultKeyProvider {
            private val keys = linkedMapOf<String, SecretKey>()

            override fun getExistingKey(alias: String): SecretKey? {
                if (alias == corruptedAlias) {
                    throw UnrecoverableKeyException("Hardware AndroidKeyStore alias invalidated")
                }
                return keys[alias]
            }

            override fun getOrCreateKey(alias: String): SecretKey {
                getExistingKey(alias)?.let { return it }
                return KeyGenerator.getInstance("AES").apply { init(256) }.generateKey().also {
                    keys[alias] = it
                }
            }

            override fun deleteKey(alias: String) {
                deletedAliases.add(alias)
                if (throwOnDelete) {
                    throw KeyStoreException("KeyStore deleteEntry failed")
                }
                if (corruptedAlias == alias) {
                    corruptedAlias = null
                }
                keys.remove(alias)
            }
        }

        val vault = AppSecretVault(context, keyProvider)
        vault.clear()
        deletedAliases.clear()

        vault.writeAll(mapOf("UTH_PASSWORD" to "before-corruption"))
        assertEquals(mapOf("UTH_PASSWORD" to "before-corruption"), vault.readAll())

        // 1. Hardware AndroidKeyStore invalidates alias -> readAll() must delete file AND evict alias
        //    so the very next writeAll() -> getOrCreateKey() recovers cleanly instead of failing.
        val secretsFile = File(context.noBackupFilesDir, "uth_app_secrets.json")
        assertTrue(secretsFile.exists())
        corruptedAlias = "uthelper_app_secrets_v1"

        assertEquals(emptyMap<String, String>(), vault.readAll())
        assertFalse(secretsFile.exists())
        assertEquals(listOf("uthelper_app_secrets_v1"), deletedAliases)

        vault.writeAll(mapOf("UTH_PASSWORD" to "recovered-after-eviction"))
        assertEquals(mapOf("UTH_PASSWORD" to "recovered-after-eviction"), vault.readAll())

        // 2. Even if deleteKey() itself throws KeyStoreException during AEAD/GeneralSecurityException
        //    recovery, readAll() must remain exception-safe and fail closed with emptyMap().
        deletedAliases.clear()
        throwOnDelete = true
        val tampered = JSONObject(secretsFile.readText()).put("ciphertext", "AAAA")
        secretsFile.writeText(tampered.toString())

        assertEquals(emptyMap<String, String>(), vault.readAll())
        assertFalse(secretsFile.exists())
        assertEquals(listOf("uthelper_app_secrets_v1"), deletedAliases)

        throwOnDelete = false
        vault.clear()
    }
}
