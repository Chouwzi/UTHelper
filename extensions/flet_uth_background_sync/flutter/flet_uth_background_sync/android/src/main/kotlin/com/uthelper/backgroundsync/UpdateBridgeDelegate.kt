package com.uthelper.backgroundsync

import io.flutter.plugin.common.MethodCall
import io.flutter.plugin.common.MethodChannel

/**
 * Channel-specific self-update MethodChannel delegate.
 *
 * `apk_lts` builds wire `ApkLtsUpdateBridgeDelegate` (DownloadManager + FileProvider + APK installer).
 * `chplay` builds exclude `com.uthelper.backgroundsync.update.*` completely and wire a no-op delegate.
 */
internal interface UpdateBridgeDelegate {
    fun handleMethodCall(
        call: MethodCall,
        result: MethodChannel.Result,
        execute: (MethodChannel.Result, suspend () -> Any?) -> Unit,
    ): Boolean

    fun cancel()
}
