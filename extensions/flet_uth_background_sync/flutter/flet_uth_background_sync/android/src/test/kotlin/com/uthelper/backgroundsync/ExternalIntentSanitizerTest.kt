package com.uthelper.backgroundsync

import android.content.Intent
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [35])
class ExternalIntentSanitizerTest {
    @Test
    fun unwrapsMeetFirebaseDynamicLinkDirectlyToTachyonBrowsableIntent() {
        val meetIntentUri =
            "intent://meet.app.goo.gl/?link=https://meet.google.com/abc-defg-hij&apn=com.google.android.apps.tachyon&isi=1096918571&ibi=com.google.Tachyon" +
                "#Intent;package=com.google.android.gms;action=com.google.firebase.dynamiclinks.VIEW_DYNAMIC_LINK;scheme=https;" +
                "S.browser_fallback_url=https://play.google.com/store/apps/details%3Fid%3Dcom.google.android.apps.tachyon;end;"

        val sanitized = buildSafeExternalIntent(
            meetIntentUri,
            "com.uthelper.uthelper",
        )
        assertNotNull(sanitized)
        assertEquals(Intent.ACTION_VIEW, sanitized!!.action)
        assertEquals("https://meet.google.com/abc-defg-hij", sanitized.dataString)
        assertEquals("com.google.android.apps.tachyon", sanitized.`package`)
        assertTrue(sanitized.categories.contains(Intent.CATEGORY_BROWSABLE))
        assertNull(sanitized.component)
        assertNull(sanitized.selector)
        assertNull(sanitized.extras)
    }

    @Test
    fun sanitizesCustomSchemeAndStripsComponentSelectorExtrasAndBlocksSelfOrFile() {
        val zalo = buildSafeExternalIntent(
            "zalo://zaloapp.com/qr/g/nhom_lop_a",
            "com.uthelper.uthelper",
        )
        assertNotNull(zalo)
        assertEquals("zalo", zalo!!.data?.scheme)
        assertTrue(zalo.categories.contains(Intent.CATEGORY_BROWSABLE))
        assertEquals(Intent.FLAG_ACTIVITY_NEW_TASK, zalo.flags)

        val strippedRedirection = buildSafeExternalIntent(
            "intent://zoom.us/join?confno=123#Intent;scheme=zoomus;component=com.evil/.Hijack;S.secret=leak;end",
            "com.uthelper.uthelper",
        )
        assertNotNull(strippedRedirection)
        assertNull(strippedRedirection!!.component)
        assertNull(strippedRedirection.selector)
        assertNull(strippedRedirection.extras)

        for (blocked in listOf(
            "",
            "javascript:alert(1)",
            "file:///data/data/com.uthelper.uthelper/files/settings.json",
            "content://com.uthelper.uthelper.provider/x",
            "data:text/html,<script>",
            "intent://x#Intent;scheme=https;package=com.uthelper.uthelper;end",
            "intent://x#Intent;scheme=file;path=/etc/hosts;end",
        )) {
            assertNull(
                blocked,
                buildSafeExternalIntent(blocked, "com.uthelper.uthelper"),
            )
        }
    }
}
