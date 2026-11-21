package com.uthelper.backgroundsync.network

import com.uthelper.backgroundsync.security.MoodleCredential
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertThrows
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.io.ByteArrayOutputStream
import java.io.InputStream
import java.io.OutputStream
import java.net.HttpURLConnection
import java.net.URL

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [35])
class MoodleWsClientTest {
    @Test
    fun parseEventExtractsCmidThenStripsUntrustedUrl() {
        val client = MoodleWsClient(MoodleCredential("https://courses.ut.edu.vn", "tok"))

        val trustedEvent = JSONObject(
            mapOf(
                "id" to "10",
                "timesort" to 1_900_000_000L,
                "modulename" to "assign",
                "name" to "Lab 1",
                "url" to "https://courses.ut.edu.vn/mod/assign/view.php?id=555",
            ),
        )
        val parsedTrusted = client.parseEvent(trustedEvent, 1L)
        assertNotNull(parsedTrusted)
        assertEquals("assign:555", parsedTrusted!!.activityKey)
        assertEquals("https://courses.ut.edu.vn/mod/assign/view.php?id=555", parsedTrusted.url)

        val evilEvent = JSONObject(
            mapOf(
                "id" to "11",
                "timesort" to 1_900_000_000L,
                "modulename" to "quiz",
                "name" to "Quiz 1",
                "url" to "https://evil.example/mod/quiz/view.php?id=777",
            ),
        )
        val parsedEvil = client.parseEvent(evilEvent, 1L)
        assertNotNull(parsedEvil)
        assertEquals("quiz:777", parsedEvil!!.activityKey)
        assertEquals("", parsedEvil.url)
    }

    @Test
    fun callObjectRejectsCleartextAndRedirects() {
        val httpClient = MoodleWsClient(MoodleCredential("http://courses.ut.edu.vn", "tok"))
        assertThrows(MoodleWsException::class.java) {
            httpClient.callObject("core_calendar_get_action_events_by_timesort", emptyMap())
        }

        var observedFollowRedirects: Boolean? = null
        val redirectClient = MoodleWsClient(MoodleCredential("https://courses.ut.edu.vn", "tok")) { endpoint ->
            object : HttpURLConnection(URL(endpoint)) {
                override fun connect() = Unit
                override fun disconnect() = Unit
                override fun usingProxy(): Boolean = false
                override fun getOutputStream(): OutputStream = ByteArrayOutputStream()
                override fun getResponseCode(): Int {
                    observedFollowRedirects = instanceFollowRedirects
                    return 302
                }
                override fun getInputStream(): InputStream = throw AssertionError("Should not read body")
            }
        }
        assertThrows(MoodleWsException::class.java) {
            redirectClient.callObject("core_calendar_get_action_events_by_timesort", emptyMap())
        }
        assertFalse(observedFollowRedirects!!)
    }
}
