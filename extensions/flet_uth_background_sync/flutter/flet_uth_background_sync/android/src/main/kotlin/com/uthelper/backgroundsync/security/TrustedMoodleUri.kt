package com.uthelper.backgroundsync.security

import android.net.Uri
import java.net.URI

object TrustedMoodleUri {
    val TRUSTED_HOSTS: Set<String> = setOf("courses.ut.edu.vn", "thnn.ut.edu.vn")

    fun normalizeTrustedOrigin(rawUrl: String): String? {
        if (hasUnsafeChars(rawUrl)) return null
        val androidUri = runCatching { Uri.parse(rawUrl) }.getOrNull() ?: return null
        val javaUri = runCatching { URI(rawUrl) }.getOrNull() ?: return null
        val host = extractVerifiedHttpsHost(androidUri, javaUri) ?: return null
        if (host !in TRUSTED_HOSTS) return null
        val path = javaUri.rawPath
        if (!path.isNullOrEmpty() && path != "/") return null
        if (javaUri.rawQuery != null || javaUri.rawFragment != null) return null
        return "https://$host"
    }

    fun validateActivityUri(rawUrl: String, savedBaseUrl: String? = null): Uri? {
        if (hasUnsafeChars(rawUrl)) return null
        val androidUri = runCatching { Uri.parse(rawUrl) }.getOrNull() ?: return null
        val javaUri = runCatching { URI(rawUrl) }.getOrNull() ?: return null
        val host = extractVerifiedHttpsHost(androidUri, javaUri) ?: return null

        val savedHost = savedBaseUrl
            ?.let(::normalizeTrustedOrigin)
            ?.let { origin -> runCatching { URI(origin).host?.lowercase()?.trimEnd('.') }.getOrNull() }
        val allowedHosts = if (savedHost != null) TRUSTED_HOSTS + savedHost else TRUSTED_HOSTS
        if (host !in allowedHosts) return null

        return androidUri.buildUpon()
            .scheme("https")
            .encodedAuthority(host)
            .build()
    }

    private fun hasUnsafeChars(rawUrl: String): Boolean {
        if (rawUrl.isBlank() || rawUrl != rawUrl.trim()) return true
        return rawUrl.any { ch -> ch == '\\' || ch.code < 0x20 || ch.code == 0x7F }
    }

    private fun extractVerifiedHttpsHost(androidUri: Uri, javaUri: URI): String? {
        val javaScheme = javaUri.scheme?.lowercase() ?: return null
        val androidScheme = androidUri.scheme?.lowercase() ?: return null
        if (javaScheme != "https" || androidScheme != "https") return null

        val encodedAuthority = androidUri.encodedAuthority ?: return null
        if (javaUri.rawUserInfo != null || androidUri.userInfo != null || '@' in encodedAuthority) {
            return null
        }
        if (javaUri.port != -1 || androidUri.port != -1) {
            return null
        }

        val javaHost = javaUri.host?.lowercase()?.trimEnd('.').orEmpty()
        val androidHost = androidUri.host?.lowercase()?.trimEnd('.').orEmpty()
        if (javaHost.isEmpty() || javaHost != androidHost) {
            return null
        }
        return javaHost
    }
}
