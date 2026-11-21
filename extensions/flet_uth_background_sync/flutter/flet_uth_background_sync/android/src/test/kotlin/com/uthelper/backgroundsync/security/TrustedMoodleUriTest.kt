package com.uthelper.backgroundsync.security

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [35])
class TrustedMoodleUriTest {
    @Test
    fun normalizeTrustedOriginAcceptsAllowedHttpsOriginsOnly() {
        assertEquals(
            "https://courses.ut.edu.vn",
            TrustedMoodleUri.normalizeTrustedOrigin("https://courses.ut.edu.vn"),
        )
        assertEquals(
            "https://thnn.ut.edu.vn",
            TrustedMoodleUri.normalizeTrustedOrigin("HTTPS://THNN.UT.EDU.VN/"),
        )
        assertNull(TrustedMoodleUri.normalizeTrustedOrigin("http://courses.ut.edu.vn"))
        assertNull(TrustedMoodleUri.normalizeTrustedOrigin("https://user@courses.ut.edu.vn"))
        assertNull(TrustedMoodleUri.normalizeTrustedOrigin("https://courses.ut.edu.vn:8443"))
        assertNull(TrustedMoodleUri.normalizeTrustedOrigin("https://courses.ut.edu.vn/extra"))
        assertNull(TrustedMoodleUri.normalizeTrustedOrigin("https://evil.example"))
        assertNull(TrustedMoodleUri.normalizeTrustedOrigin(" https://courses.ut.edu.vn"))
    }

    @Test
    fun validateActivityUriEnforcesHttpsHostPinningAndRejectsTricks() {
        val valid = TrustedMoodleUri.validateActivityUri(
            "https://courses.ut.edu.vn/mod/assign/view.php?id=123",
        )
        assertNotNull(valid)
        assertEquals("https", valid!!.scheme)
        assertEquals("courses.ut.edu.vn", valid.host)

        val validThnn = TrustedMoodleUri.validateActivityUri(
            "HTTPS://THNN.UT.EDU.VN/mod/quiz/view.php?id=42",
        )
        assertNotNull(validThnn)
        assertEquals("https", validThnn!!.scheme)
        assertEquals("thnn.ut.edu.vn", validThnn.host)

        for (bad in listOf(
            "",
            "   ",
            " https://courses.ut.edu.vn/mod/assign/view.php?id=1",
            "http://courses.ut.edu.vn/mod/assign/view.php?id=1",
            "javascript:alert(1)",
            "intent://courses.ut.edu.vn/#Intent;scheme=https;end",
            "file:///etc/passwd",
            "https://evil.com/mod/assign/view.php?id=1",
            "https://courses.ut.edu.vn.evil.com/mod/assign/view.php?id=1",
            "https://user:pass@courses.ut.edu.vn/mod/assign/view.php?id=1",
            "https://courses.ut.edu.vn@evil.com/mod/assign/view.php?id=1",
            "https://courses.ut.edu.vn:443/mod/assign/view.php?id=1",
            "https://courses.ut.edu.vn\\@evil.com/mod/assign/view.php?id=1",
            "https://courses.ut.edu.vn/mod\n/view.php?id=1",
        )) {
            assertNull("Expected rejection for $bad", TrustedMoodleUri.validateActivityUri(bad))
        }
    }
}
