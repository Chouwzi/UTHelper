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

class CredentialVault(context: Context) {
    private val credentialFile = File(context.noBackupFilesDir, "uth_ws_credential.json")

    @Synchronized
    fun write(baseUrl: String, token: String) {
        require(baseUrl.startsWith("https://") || baseUrl.startsWith("http://"))
        require(token.isNotBlank())
        val cipher = Cipher.getInstance(TRANSFORMATION).apply {
            init(Cipher.ENCRYPT_MODE, getOrCreateKey())
        }
        val encrypted = cipher.doFinal(token.toByteArray(Charsets.UTF_8))
        val payload = JSONObject()
            .put("base_url", baseUrl.trimEnd('/'))
            .put("iv", Base64.encodeToString(cipher.iv, Base64.NO_WRAP))
            .put("ciphertext", Base64.encodeToString(encrypted, Base64.NO_WRAP))
        val temporary = File(credentialFile.parentFile, "${credentialFile.name}.tmp")
        temporary.writeText(payload.toString(), Charsets.UTF_8)
        check(temporary.renameTo(credentialFile) || run {
            temporary.copyTo(credentialFile, overwrite = true)
            temporary.delete()
        })
    }

    @Synchronized
    fun read(): MoodleCredential? = runCatching {
        if (!credentialFile.exists()) return null
        val payload = JSONObject(credentialFile.readText(Charsets.UTF_8))
        val cipher = Cipher.getInstance(TRANSFORMATION).apply {
            init(
                Cipher.DECRYPT_MODE,
                keyStore().getKey(KEY_ALIAS, null) as SecretKey,
                GCMParameterSpec(
                    128,
                    Base64.decode(payload.getString("iv"), Base64.NO_WRAP),
                ),
            )
        }
        val token = cipher.doFinal(
            Base64.decode(payload.getString("ciphertext"), Base64.NO_WRAP)
        ).toString(Charsets.UTF_8)
        MoodleCredential(payload.getString("base_url"), token)
    }.getOrNull()

    @Synchronized
    fun clear() {
        credentialFile.delete()
        keyStore().deleteEntry(KEY_ALIAS)
    }

    private fun getOrCreateKey(): SecretKey {
        val existing = keyStore().getKey(KEY_ALIAS, null) as? SecretKey
        if (existing != null) return existing
        return KeyGenerator.getInstance(
            KeyProperties.KEY_ALGORITHM_AES,
            ANDROID_KEYSTORE,
        ).apply {
            init(
                KeyGenParameterSpec.Builder(
                    KEY_ALIAS,
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

    private fun keyStore(): KeyStore = KeyStore.getInstance(ANDROID_KEYSTORE).apply {
        load(null)
    }

    companion object {
        private const val ANDROID_KEYSTORE = "AndroidKeyStore"
        private const val KEY_ALIAS = "uthelper_moodle_ws_token_v1"
        private const val TRANSFORMATION = "AES/GCM/NoPadding"
    }
}
