package com.uthelper.backgroundsync.security

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import org.json.JSONException
import org.json.JSONObject
import java.io.File
import java.io.FileOutputStream
import java.io.IOException
import java.security.GeneralSecurityException
import java.security.KeyStore
import java.util.UUID
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

    fun write(baseUrl: String, token: String) = synchronized(VAULT_LOCK) {
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

    fun read(): MoodleCredential? = synchronized(VAULT_LOCK) {
        runCatching {
            if (!credentialFile.exists()) return@synchronized null
            val payload = JSONObject(credentialFile.readText(Charsets.UTF_8))
            val origin = TrustedMoodleUri.normalizeTrustedOrigin(payload.getString("base_url"))
                ?: return@synchronized null
            val key = keyProvider.getExistingKey(KEY_ALIAS) ?: return@synchronized null
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
            if (token.isBlank()) return@synchronized null
            MoodleCredential(origin, token)
        }.getOrNull()
    }

    fun clear() = synchronized(VAULT_LOCK) {
        credentialFile.delete()
        cleanupTempFiles(credentialFile)
        runCatching { keyProvider.deleteKey(KEY_ALIAS) }
    }

    companion object {
        private const val KEY_ALIAS = "uthelper_moodle_ws_token_v1"
        private const val TRANSFORMATION = "AES/GCM/NoPadding"
        private val VAULT_LOCK = Any()
    }
}

class AppSecretVault internal constructor(
    context: Context,
    private val keyProvider: VaultKeyProvider,
) {
    constructor(context: Context) : this(context, AndroidKeyStoreVaultKeyProvider)

    private val secretsFile = File(context.noBackupFilesDir, "uth_app_secrets.json")

    fun readAll(): Map<String, String> = synchronized(VAULT_LOCK) {
        if (!secretsFile.exists()) return@synchronized emptyMap()
        val rawText = try {
            secretsFile.readText(Charsets.UTF_8)
        } catch (ioError: IOException) {
            // Never delete the vault file on transient filesystem I/O failures.
            throw ioError
        }
        try {
            val envelope = JSONObject(rawText)
            val key = keyProvider.getExistingKey(KEY_ALIAS) ?: run {
                secretsFile.delete()
                return@synchronized emptyMap()
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
        } catch (ioError: IOException) {
            throw ioError
        } catch (_: GeneralSecurityException) {
            // Permanent keystore invalidation or AEAD tag mismatch: fail closed, drop corrupt blob,
            // and evict the potentially corrupted AndroidKeyStore key alias before next getOrCreateKey().
            secretsFile.delete()
            runCatching { keyProvider.deleteKey(KEY_ALIAS) }
            emptyMap()
        } catch (_: JSONException) {
            secretsFile.delete()
            emptyMap()
        } catch (_: IllegalArgumentException) {
            secretsFile.delete()
            emptyMap()
        }
    }

    fun writeAll(secrets: Map<String, String>) = synchronized(VAULT_LOCK) {
        val sanitized = linkedMapOf<String, String>()
        for ((k, v) in secrets) {
            if (k.isNotBlank() && v.isNotBlank()) {
                sanitized[k] = v
            }
        }
        if (sanitized.isEmpty()) {
            clear()
            return@synchronized
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

    fun clear() = synchronized(VAULT_LOCK) {
        secretsFile.delete()
        cleanupTempFiles(secretsFile)
        runCatching { keyProvider.deleteKey(KEY_ALIAS) }
    }

    companion object {
        private const val KEY_ALIAS = "uthelper_app_secrets_v1"
        private const val TRANSFORMATION = "AES/GCM/NoPadding"
        private val VAULT_LOCK = Any()
    }
}

private fun cleanupTempFiles(target: File) {
    val parent = target.parentFile ?: return
    val prefix = "${target.name}."
    parent.listFiles()?.forEach { candidate ->
        if (candidate.isFile && candidate.name.startsWith(prefix) && candidate.name.endsWith(".tmp")) {
            candidate.delete()
        }
    }
}

private fun writeAtomically(target: File, content: String) {
    target.parentFile?.mkdirs()
    val temporary = File(target.parentFile, "${target.name}.${UUID.randomUUID()}.tmp")
    try {
        FileOutputStream(temporary).use { stream ->
            stream.write(content.toByteArray(Charsets.UTF_8))
            stream.flush()
            runCatching { stream.fd.sync() }
        }
        check(temporary.renameTo(target) || run {
            temporary.copyTo(target, overwrite = true)
            temporary.delete()
        })
    } catch (error: Throwable) {
        temporary.delete()
        throw error
    }
}
