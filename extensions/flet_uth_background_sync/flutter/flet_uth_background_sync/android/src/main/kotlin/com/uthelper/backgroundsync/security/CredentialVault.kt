package com.uthelper.backgroundsync.security

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import org.json.JSONObject
import java.io.File
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

data class MoodleCredential(val baseUrl: String, val token: String)

internal interface VaultKeyProvider {
    fun getExistingKey(alias: String): SecretKey?
    fun getOrCreateKey(alias: String): SecretKey
    fun deleteKey(alias: String)
}

internal object AndroidKeyStoreVaultKeyProvider : VaultKeyProvider {
    private const val ANDROID_KEYSTORE = "AndroidKeyStore"

    override fun getExistingKey(alias: String): SecretKey? =
        keyStore().getKey(alias, null) as? SecretKey

    override fun getOrCreateKey(alias: String): SecretKey {
        getExistingKey(alias)?.let { return it }
        return KeyGenerator.getInstance(
            KeyProperties.KEY_ALGORITHM_AES,
            ANDROID_KEYSTORE,
        ).apply {
            init(
                KeyGenParameterSpec.Builder(
                    alias,
                    KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT,
                )
                    .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                    .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                    .setKeySize(256)
                    .setRandomizedEncryptionRequired(true)
                    .build()
            )
        }.generateKey()
    }

    override fun deleteKey(alias: String) {
        keyStore().deleteEntry(alias)
    }

    private fun keyStore(): KeyStore = KeyStore.getInstance(ANDROID_KEYSTORE).apply {
        load(null)
    }
}

class CredentialVault internal constructor(
    context: Context,
    private val keyProvider: VaultKeyProvider,
) {
    constructor(context: Context) : this(context, AndroidKeyStoreVaultKeyProvider)

    private val credentialFile = File(context.noBackupFilesDir, "uth_ws_credential.json")

    @Synchronized
    fun write(baseUrl: String, token: String) {
        val origin = TrustedMoodleUri.normalizeTrustedOrigin(baseUrl)
            ?: throw IllegalArgumentException("Untrusted Moodle credential origin")
        require(token.isNotBlank())
        val cipher = Cipher.getInstance(TRANSFORMATION).apply {
            init(Cipher.ENCRYPT_MODE, keyProvider.getOrCreateKey(KEY_ALIAS))
        }
        val encrypted = cipher.doFinal(token.toByteArray(Charsets.UTF_8))
        val payload = JSONObject()
            .put("base_url", origin)
            .put("iv", Base64.encodeToString(cipher.iv, Base64.NO_WRAP))
            .put("ciphertext", Base64.encodeToString(encrypted, Base64.NO_WRAP))
        writeAtomically(credentialFile, payload.toString())
    }

    @Synchronized
    fun read(): MoodleCredential? = runCatching {
        if (!credentialFile.exists()) return null
        val payload = JSONObject(credentialFile.readText(Charsets.UTF_8))
        val origin = TrustedMoodleUri.normalizeTrustedOrigin(payload.getString("base_url"))
            ?: return null
        val key = keyProvider.getExistingKey(KEY_ALIAS) ?: return null
        val cipher = Cipher.getInstance(TRANSFORMATION).apply {
            init(
                Cipher.DECRYPT_MODE,
                key,
                GCMParameterSpec(
                    128,
                    Base64.decode(payload.getString("iv"), Base64.NO_WRAP),
                ),
            )
        }
        val token = cipher.doFinal(
            Base64.decode(payload.getString("ciphertext"), Base64.NO_WRAP)
        ).toString(Charsets.UTF_8)
        if (token.isBlank()) return null
        MoodleCredential(origin, token)
    }.getOrNull()

    @Synchronized
    fun clear() {
        credentialFile.delete()
        File(credentialFile.parentFile, "${credentialFile.name}.tmp").delete()
        runCatching { keyProvider.deleteKey(KEY_ALIAS) }
    }

    companion object {
        private const val KEY_ALIAS = "uthelper_moodle_ws_token_v1"
        private const val TRANSFORMATION = "AES/GCM/NoPadding"
    }
}

class AppSecretVault internal constructor(
    context: Context,
    private val keyProvider: VaultKeyProvider,
) {
    constructor(context: Context) : this(context, AndroidKeyStoreVaultKeyProvider)

    private val secretsFile = File(context.noBackupFilesDir, "uth_app_secrets.json")

    @Synchronized
    fun readAll(): Map<String, String> {
        if (!secretsFile.exists()) return emptyMap()
        return try {
            val envelope = JSONObject(secretsFile.readText(Charsets.UTF_8))
            val key = keyProvider.getExistingKey(KEY_ALIAS) ?: run {
                secretsFile.delete()
                return emptyMap()
            }
            val cipher = Cipher.getInstance(TRANSFORMATION).apply {
                init(
                    Cipher.DECRYPT_MODE,
                    key,
                    GCMParameterSpec(
                        128,
                        Base64.decode(envelope.getString("iv"), Base64.NO_WRAP),
                    ),
                )
            }
            val jsonText = cipher.doFinal(
                Base64.decode(envelope.getString("ciphertext"), Base64.NO_WRAP)
            ).toString(Charsets.UTF_8)
            val secretsJson = JSONObject(jsonText)
            val result = linkedMapOf<String, String>()
            val keys = secretsJson.keys()
            while (keys.hasNext()) {
                val entryKey = keys.next()
                val rawValue = secretsJson.opt(entryKey)
                if (entryKey.isNotBlank() && rawValue is String && rawValue.isNotBlank()) {
                    result[entryKey] = rawValue
                }
            }
            result
        } catch (_: Exception) {
            // Fail closed on keystore invalidation, AEAD tag mismatch, or corrupt JSON.
            secretsFile.delete()
            emptyMap()
        }
    }

    @Synchronized
    fun writeAll(secrets: Map<String, String>) {
        val sanitized = linkedMapOf<String, String>()
        for ((k, v) in secrets) {
            if (k.isNotBlank() && v.isNotBlank()) {
                sanitized[k] = v
            }
        }
        if (sanitized.isEmpty()) {
            clear()
            return
        }
        val plaintextJson = JSONObject()
        for ((k, v) in sanitized) {
            plaintextJson.put(k, v)
        }
        val cipher = Cipher.getInstance(TRANSFORMATION).apply {
            init(Cipher.ENCRYPT_MODE, keyProvider.getOrCreateKey(KEY_ALIAS))
        }
        val encrypted = cipher.doFinal(plaintextJson.toString().toByteArray(Charsets.UTF_8))
        val envelope = JSONObject()
            .put("iv", Base64.encodeToString(cipher.iv, Base64.NO_WRAP))
            .put("ciphertext", Base64.encodeToString(encrypted, Base64.NO_WRAP))
        writeAtomically(secretsFile, envelope.toString())
    }

    @Synchronized
    fun clear() {
        secretsFile.delete()
        File(secretsFile.parentFile, "${secretsFile.name}.tmp").delete()
        runCatching { keyProvider.deleteKey(KEY_ALIAS) }
    }

    companion object {
        private const val KEY_ALIAS = "uthelper_app_secrets_v1"
        private const val TRANSFORMATION = "AES/GCM/NoPadding"
    }
}

private fun writeAtomically(target: File, content: String) {
    target.parentFile?.mkdirs()
    val temporary = File(target.parentFile, "${target.name}.tmp")
    temporary.writeText(content, Charsets.UTF_8)
    check(temporary.renameTo(target) || run {
        temporary.copyTo(target, overwrite = true)
        temporary.delete()
    })
}
