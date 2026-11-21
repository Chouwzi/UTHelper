package com.uthelper.backgroundsync

import android.content.Context
import io.flutter.plugin.common.MethodCall
import io.flutter.plugin.common.MethodChannel

internal object UpdateBridgeFactory {
    fun create(context: Context): UpdateBridgeDelegate = NoOpUpdateBridgeDelegate
}

private object NoOpUpdateBridgeDelegate : UpdateBridgeDelegate {
    override fun handleMethodCall(
        call: MethodCall,
        result: MethodChannel.Result,
        execute: (MethodChannel.Result, suspend () -> Any?) -> Unit,
    ): Boolean = false

    override fun cancel() = Unit
}
