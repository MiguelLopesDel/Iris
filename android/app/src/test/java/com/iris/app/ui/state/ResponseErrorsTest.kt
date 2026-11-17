package com.iris.app.ui.state

import kotlinx.serialization.json.Json
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.IOException

class ResponseErrorsTest {

    @Test
    fun `an html page where json was expected is recognised`() {
        // What the converter does with a login page served where the API should answer.
        val failure = runCatching { Json.decodeFromString<Map<String, Int>>("<html><body>Login</body></html>") }
            .exceptionOrNull()!!

        assertTrue(isUnreadableResponse(failure))
        assertTrue(isUnreadableResponse(IllegalStateException("wrapped", failure)))
    }

    @Test
    fun `a network failure is not mistaken for one`() {
        assertFalse(isUnreadableResponse(IOException("Connection refused")))
    }
}
