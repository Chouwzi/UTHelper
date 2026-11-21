package com.uthelper.backgroundsync

import android.content.Context
import com.uthelper.backgroundsync.update.ApkLtsUpdateBridgeDelegate

internal object UpdateBridgeFactory {
    fun create(context: Context): UpdateBridgeDelegate = ApkLtsUpdateBridgeDelegate(context)
}
