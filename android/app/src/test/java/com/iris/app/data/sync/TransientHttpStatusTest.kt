package com.iris.app.data.sync

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class TransientHttpStatusTest {

    @Test
    fun `server errors, rate limiting and timeouts are retried`() {
        listOf(408, 429, 500, 502, 503, 504).forEach {
            assertTrue("$it should be transient", ResumableUploadTransfer.isTransientHttpStatus(it))
        }
    }

    @Test
    fun `a request the server rejects for good stays final`() {
        listOf(400, 403, 413, 415, 422).forEach {
            assertFalse("$it should be final", ResumableUploadTransfer.isTransientHttpStatus(it))
        }
    }
}
