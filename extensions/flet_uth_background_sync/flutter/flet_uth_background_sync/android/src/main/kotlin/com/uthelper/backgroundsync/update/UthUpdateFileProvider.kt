package com.uthelper.backgroundsync.update

import androidx.core.content.FileProvider

/** Dedicated provider identity avoids colliding with Flet's bundled provider. */
class UthUpdateFileProvider : FileProvider()
