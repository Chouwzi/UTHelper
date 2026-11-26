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
import java.security.ProviderException
import java.security.UnrecoverableKeyException
import java.util.concurrent.Callable
import java.util.concurrent.Executors
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.SecretKeySpec

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

    @Test
    fun appSecretVaultHandlesProviderExceptionOnReadAndRecoversCleanly() {
        val context: Context = RuntimeEnvironment.getApplication()
        val deletedAliases = mutableListOf<String>()
        var failReadWithProviderException = false
        var failDeleteWithProviderException = false

        val keyProvider = object : VaultKeyProvider {
            private val keys = linkedMapOf<String, SecretKey>()

            override fun getExistingKey(alias: String): SecretKey? {
                if (failReadWithProviderException) {
                    throw ProviderException("KeyMint HAL unavailable for $alias")
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
                if (failDeleteWithProviderException) {
                    throw ProviderException("KeyStore deleteEntry HAL failure")
                }
                failReadWithProviderException = false
                keys.remove(alias)
            }
        }

        val vault = AppSecretVault(context, keyProvider)
        vault.clear()
        deletedAliases.clear()

        vault.writeAll(mapOf("UTH_PASSWORD" to "before-provider-crash"))
        val secretsFile = File(context.noBackupFilesDir, "uth_app_secrets.json")
        assertTrue(secretsFile.exists())

        // ProviderException extends RuntimeException, NOT GeneralSecurityException.
        failReadWithProviderException = true
        assertEquals(emptyMap<String, String>(), vault.readAll())
        assertFalse(secretsFile.exists())
        assertEquals(listOf("uthelper_app_secrets_v1"), deletedAliases)

        // Next write must recover cleanly with a fresh key.
        vault.writeAll(mapOf("UTH_PASSWORD" to "after-provider-recovery"))
        assertEquals(mapOf("UTH_PASSWORD" to "after-provider-recovery"), vault.readAll())

        // Even if deleteKey() also throws ProviderException, readAll() must fail closed cleanly.
        deletedAliases.clear()
        failReadWithProviderException = true
        failDeleteWithProviderException = true
        assertEquals(emptyMap<String, String>(), vault.readAll())
        assertFalse(secretsFile.exists())
        assertEquals(listOf("uthelper_app_secrets_v1"), deletedAliases)

        // ProviderException thrown during Cipher.init(DECRYPT_MODE, ...) must also be caught cleanly.
        failReadWithProviderException = false
        failDeleteWithProviderException = false
        vault.writeAll(mapOf("UTH_PASSWORD" to "before-cipher-init-provider-err"))
        deletedAliases.clear()
        val cipherInitFailProvider = object : VaultKeyProvider {
            override fun getExistingKey(alias: String): SecretKey = object : SecretKey {
                override fun getAlgorithm(): String = "AES"
                override fun getFormat(): String = "RAW"
                override fun getEncoded(): ByteArray {
                    throw ProviderException("KeyMint decrypt init failure")
                }
            }
            override fun getOrCreateKey(alias: String): SecretKey =
                KeyGenerator.getInstance("AES").apply { init(256) }.generateKey()
            override fun deleteKey(alias: String) {
                deletedAliases.add(alias)
            }
        }
        assertEquals(emptyMap<String, String>(), AppSecretVault(context, cipherInitFailProvider).readAll())
        assertFalse(secretsFile.exists())
        assertEquals(listOf("uthelper_app_secrets_v1"), deletedAliases)

        vault.clear()
    }

    @Test
    fun appSecretVaultEvictsKeyAliasOnJsonAndIllegalArgumentCorruption() {
        val context: Context = RuntimeEnvironment.getApplication()
        val deletedAliases = mutableListOf<String>()
        val keyProvider = object : VaultKeyProvider {
            private val keys = linkedMapOf<String, SecretKey>()
            override fun getExistingKey(alias: String): SecretKey? = keys[alias]
            override fun getOrCreateKey(alias: String): SecretKey =
                keys.getOrPut(alias) {
                    KeyGenerator.getInstance("AES").apply { init(256) }.generateKey()
                }
            override fun deleteKey(alias: String) {
                deletedAliases.add(alias)
                keys.remove(alias)
            }
        }

        val vault = AppSecretVault(context, keyProvider)
        vault.clear()
        deletedAliases.clear()

        val secretsFile = File(context.noBackupFilesDir, "uth_app_secrets.json")
        vault.writeAll(mapOf("UTH_PASSWORD" to "secret-1"))

        // 1. Corrupt JSON envelope -> wipes file AND evicts key alias.
        secretsFile.writeText("{corrupt-json")
        assertEquals(emptyMap<String, String>(), vault.readAll())
        assertFalse(secretsFile.exists())
        assertEquals(listOf("uthelper_app_secrets_v1"), deletedAliases)

        // 2. Invalid Base64 / empty IV (IllegalArgumentException) -> wipes file AND evicts key alias.
        deletedAliases.clear()
        vault.writeAll(mapOf("UTH_PASSWORD" to "secret-2"))
        val badIv = JSONObject(secretsFile.readText()).put("iv", "")
        secretsFile.writeText(badIv.toString())
        assertEquals(emptyMap<String, String>(), vault.readAll())
        assertFalse(secretsFile.exists())
        assertEquals(listOf("uthelper_app_secrets_v1"), deletedAliases)

        vault.writeAll(mapOf("UTH_PASSWORD" to "secret-3"))
        assertEquals(mapOf("UTH_PASSWORD" to "secret-3"), vault.readAll())
        vault.clear()
    }

    @Test
    fun credentialVaultReadDeletesFileAndKeyAliasOnSecurityAndProviderExceptions() {
        val context: Context = RuntimeEnvironment.getApplication()
        val deletedAliases = mutableListOf<String>()
        var readFailure: Throwable? = null

        val keyProvider = object : VaultKeyProvider {
            private val keys = linkedMapOf<String, SecretKey>()

            override fun getExistingKey(alias: String): SecretKey? {
                readFailure?.let { throw it }
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
                readFailure = null
                keys.remove(alias)
            }
        }

        val vault = CredentialVault(context, keyProvider)
        vault.clear()
        deletedAliases.clear()

        val storageFile = File(context.noBackupFilesDir, "uth_ws_credential.json")

        // 1. GeneralSecurityException during decrypt -> delete file + deleteKey(KEY_ALIAS)
        vault.write("https://courses.ut.edu.vn", "ws-tok-1")
        assertTrue(storageFile.exists())
        readFailure = UnrecoverableKeyException("Corrupt keystore key")
        assertNull(vault.read())
        assertFalse(storageFile.exists())
        assertEquals(listOf("uthelper_moodle_ws_token_v1"), deletedAliases)

        // Subsequent write recovers cleanly
        vault.write("https://courses.ut.edu.vn", "ws-tok-2")
        assertEquals("ws-tok-2", vault.read()?.token)

        // 2. ProviderException during decrypt -> delete file + deleteKey(KEY_ALIAS)
        deletedAliases.clear()
        readFailure = ProviderException("Keystore binder died")
        assertNull(vault.read())
        assertFalse(storageFile.exists())
        assertEquals(listOf("uthelper_moodle_ws_token_v1"), deletedAliases)

        vault.write("https://courses.ut.edu.vn", "ws-tok-3")
        assertEquals("ws-tok-3", vault.read()?.token)

        // 3. AEADBadTagException on tampered ciphertext -> delete file + deleteKey(KEY_ALIAS)
        deletedAliases.clear()
        val tampered = JSONObject(storageFile.readText()).put("ciphertext", "AAAA")
        storageFile.writeText(tampered.toString())
        assertNull(vault.read())
        assertFalse(storageFile.exists())
        assertEquals(listOf("uthelper_moodle_ws_token_v1"), deletedAliases)

        // 4. Transient IOException must NOT delete storage path or evict key
        deletedAliases.clear()
        assertTrue(storageFile.mkdirs())
        try {
            assertNull(vault.read())
            assertTrue(storageFile.exists())
            assertTrue(deletedAliases.isEmpty())
        } finally {
            storageFile.delete()
        }

        vault.clear()
    }

    @Test
    fun credentialVaultAndAppSecretVaultWriteRetriesOnceOnKeystoreAndCipherFailures() {
        val context: Context = RuntimeEnvironment.getApplication()
        val deletedAliases = mutableListOf<String>()
        var getOrCreateCalls = 0
        var failuresRemaining = 0
        var mode = "provider_on_get"

        val poisonedProviderKey = object : SecretKey {
            override fun getAlgorithm(): String = "AES"
            override fun getFormat(): String = "RAW"
            override fun getEncoded(): ByteArray {
                throw ProviderException("KeyMint rejected key material during Cipher.init")
            }
        }

        val keyProvider = object : VaultKeyProvider {
            private val keys = linkedMapOf<String, SecretKey>()

            override fun getExistingKey(alias: String): SecretKey? = keys[alias]

            override fun getOrCreateKey(alias: String): SecretKey {
                getOrCreateCalls++
                if (failuresRemaining > 0) {
                    failuresRemaining--
                    when (mode) {
                        "provider_on_get" -> throw ProviderException("Poisoned keystore alias")
                        "gse_on_get" -> throw UnrecoverableKeyException("Key permanently invalidated")
                        "invalid_key_cipher_init" -> return SecretKeySpec(byteArrayOf(1, 2, 3), "AES")
                        "provider_on_cipher_init" -> return poisonedProviderKey
                    }
                }
                return keys.getOrPut(alias) {
                    KeyGenerator.getInstance("AES").apply { init(256) }.generateKey()
                }
            }

            override fun deleteKey(alias: String) {
                deletedAliases.add(alias)
                keys.remove(alias)
            }
        }

        val credVault = CredentialVault(context, keyProvider)
        val secretVault = AppSecretVault(context, keyProvider)
        credVault.clear()
        secretVault.clear()

        for (failureMode in listOf(
            "provider_on_get",
            "gse_on_get",
            "invalid_key_cipher_init",
            "provider_on_cipher_init",
        )) {
            deletedAliases.clear()
            getOrCreateCalls = 0
            mode = failureMode
            failuresRemaining = 1
            credVault.write("https://courses.ut.edu.vn", "tok-$failureMode")
            assertEquals(listOf("uthelper_moodle_ws_token_v1"), deletedAliases)
            assertEquals(2, getOrCreateCalls)
            assertEquals("tok-$failureMode", credVault.read()?.token)

            deletedAliases.clear()
            getOrCreateCalls = 0
            failuresRemaining = 1
            secretVault.writeAll(mapOf("UTH_PASSWORD" to "pw-$failureMode"))
            assertEquals(listOf("uthelper_app_secrets_v1"), deletedAliases)
            assertEquals(2, getOrCreateCalls)
            assertEquals(mapOf("UTH_PASSWORD" to "pw-$failureMode"), secretVault.readAll())
        }

        // Persistent failure (initial + retry both fail) must only retry ONCE and propagate error.
        deletedAliases.clear()
        getOrCreateCalls = 0
        mode = "provider_on_get"
        failuresRemaining = 5
        assertThrows(ProviderException::class.java) {
            credVault.write("https://courses.ut.edu.vn", "should-fail")
        }
        assertEquals(listOf("uthelper_moodle_ws_token_v1"), deletedAliases)
        assertEquals(2, getOrCreateCalls)

        deletedAliases.clear()
        getOrCreateCalls = 0
        failuresRemaining = 5
        assertThrows(ProviderException::class.java) {
            secretVault.writeAll(mapOf("UTH_PASSWORD" to "should-fail"))
        }
        assertEquals(listOf("uthelper_app_secrets_v1"), deletedAliases)
        assertEquals(2, getOrCreateCalls)

        // Non-crypto validation errors must NOT evict keys or retry.
        deletedAliases.clear()
        getOrCreateCalls = 0
        failuresRemaining = 0
        assertThrows(IllegalArgumentException::class.java) {
            credVault.write("https://evil.example", "tok")
        }
        assertTrue(deletedAliases.isEmpty())
        assertEquals(0, getOrCreateCalls)

        credVault.clear()
        secretVault.clear()
    }
}
