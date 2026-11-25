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
            "io.github.chouwzi.uthelper",
        )
        assertNotNull(sanitized)
        assertEquals(Intent.ACTION_VIEW, sanitized!!.action)
        assertEquals("https://meet.google.com/abc-defg-hij", sanitized.dataString)
        assertEquals("com.google.android.apps.tachyon", sanitized.`package`)
        assertEquals(setOf(Intent.CATEGORY_BROWSABLE), sanitized.categories)
        assertNull(sanitized.type)
        assertNull(sanitized.identifier)
        assertNull(sanitized.component)
        assertNull(sanitized.selector)
        assertNull(sanitized.extras)
    }

    @Test
    fun sanitizesCustomSchemeAndStripsComponentSelectorExtrasTypeCategoriesAndBlocksUnsafeTargets() {
        val ownPkg = "io.github.chouwzi.uthelper"
        val zalo = buildSafeExternalIntent(
            "zalo://zaloapp.com/qr/g/nhom_lop_a",
            ownPkg,
        )
        assertNotNull(zalo)
        assertEquals("zalo", zalo!!.data?.scheme)
        assertEquals(setOf(Intent.CATEGORY_BROWSABLE), zalo.categories)
        assertEquals(Intent.FLAG_ACTIVITY_NEW_TASK, zalo.flags)

        val marketDetails = buildSafeExternalIntent(
            "market://details?id=com.zing.zalo",
            ownPkg,
        )
        assertNotNull(marketDetails)
        assertEquals("market", marketDetails!!.data?.scheme)

        val strippedRedirection = buildSafeExternalIntent(
            "intent://zoom.us/join?confno=123#Intent;scheme=zoomus;type=text/html;category=android.intent.category.HOME;identifier=evil_id;i.num=42;component=com.evil/.Hijack;S.secret=leak;end",
            ownPkg,
        )
        assertNotNull(strippedRedirection)
        assertNull(strippedRedirection!!.type)
        assertEquals(setOf(Intent.CATEGORY_BROWSABLE), strippedRedirection.categories)
        assertNull(strippedRedirection.identifier)
        assertNull(strippedRedirection.component)
        assertNull(strippedRedirection.selector)
        assertNull(strippedRedirection.extras)

        for (blocked in listOf(
            "",
            "javascript:alert(1)",
            "file:///data/data/io.github.chouwzi.uthelper/files/settings.json",
            "content://io.github.chouwzi.uthelper.provider/x",
            "data:text/html,<script>",
            "http://example.com/cleartext",
            "intent://127.0.0.1:54321/pdf-viewer/tok/#Intent;scheme=http;package=com.android.chrome;end",
            "intent://example.com/#Intent;scheme=http;end",
            "intent://pay#Intent;scheme=momo;end",
            "momo://pay",
            "oem-internal-debug://secret",
            "market://search?q=phishing",
            "https://127.0.0.1/admin",
            "https://localhost:8443/secret",
            "https://10.0.0.1/internal",
            "https://192.168.1.1/",
            "https://169.254.169.254/latest/meta-data/",
            "https://172.16.0.1/",
            "https://[::1]/admin",
            "https://user:pass@docs.google.com/spreadsheets/d/1",
            "intent://127.0.0.1/admin#Intent;scheme=https;package=com.android.chrome;end",
            "intent://docs.google.com/spreadsheets/d/1/edit#Intent;scheme=https;package=com.android.chrome;end",
            "intent://docs.google.com/spreadsheets/d/1/edit#Intent;scheme=https;package=com.sec.android.app.sbrowser;end",
            "intent://meet.app.goo.gl/?link=https://meet.google.com/abc&apn=com.android.chrome#Intent;package=com.google.android.gms;scheme=https;end",
            "intent://user@docs.google.com/doc#Intent;scheme=https;end",
            "intent://docs.google.com/doc#Intent;scheme=https;package=invalid..pkg;end",
            "intent://meet.app.goo.gl/?link=https://127.0.0.1/evil&apn=com.google.android.apps.tachyon#Intent;package=com.google.android.gms;scheme=https;end",
            "intent://meet.app.goo.gl/?link=https://user@meet.google.com/abc&apn=com.google.android.apps.tachyon#Intent;package=com.google.android.gms;scheme=https;end",
            "intent://meet.app.goo.gl/?link=https://meet.google.com/abc&apn=../bad_pkg#Intent;package=com.google.android.gms;scheme=https;end",
            "intent://x#Intent;scheme=https;package=io.github.chouwzi.uthelper;end",
            "intent://x#Intent;scheme=file;path=/etc/hosts;end",
        )) {
            assertNull(
                blocked,
                buildSafeExternalIntent(blocked, ownPkg),
            )
        }
    }
}
