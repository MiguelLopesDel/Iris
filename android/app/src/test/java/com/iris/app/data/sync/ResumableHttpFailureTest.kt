package com.iris.app.data.sync

import com.iris.app.data.sync.ResumableUploadTransfer.Companion.httpFailureAction
import org.junit.Assert.assertEquals
import org.junit.Test

class ResumableHttpFailureTest {

    @Test
    fun `a passing server refusal sends the item again instead of failing it`() {
        for (code in listOf(408, 429, 500, 503)) {
            assertEquals("HTTP $code", HttpFailureAction.SEND_AGAIN, httpFailureAction(code, remoteUploadStarted = false))
        }
    }

    @Test
    fun `an upload the server forgot is reserved again`() {
        assertEquals(HttpFailureAction.RESERVE_AGAIN, httpFailureAction(404, remoteUploadStarted = true))
    }

    @Test
    fun `a refusal of the item itself stays final`() {
        assertEquals(HttpFailureAction.FAIL, httpFailureAction(413, remoteUploadStarted = false))
        assertEquals(HttpFailureAction.FAIL, httpFailureAction(404, remoteUploadStarted = false))
    }
}
