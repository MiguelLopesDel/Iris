package com.iris.app

import com.iris.app.ui.screens.gallery.signedOutMessage
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class SignedOutMessageTest {

    @Test
    fun `the text follows the server once its first account exists`() {
        // Signed out by a reinstall: first the server has no account, then it has one.
        val beforeSetup = signedOutMessage(serverReplaced = true, serverNeedsSetup = true)
        val afterSetup = signedOutMessage(serverReplaced = true, serverNeedsSetup = false)

        assertTrue(beforeSetup!!.contains("ainda não tem contas"))
        assertNotEquals(beforeSetup, afterSetup)
        assertTrue(!afterSetup!!.contains("ainda não tem contas"))
    }

    @Test
    fun `a server without accounts is named even without a known reinstall`() {
        // After a restart the reinstall is forgotten, but the server state is not.
        assertTrue(signedOutMessage(serverReplaced = false, serverNeedsSetup = true)!!.contains("ainda não tem contas"))
    }

    @Test
    fun `an ordinary logout keeps the generic login text`() {
        assertNull(signedOutMessage(serverReplaced = false, serverNeedsSetup = false))
    }

    @Test
    fun `texts tell the three cases apart`() {
        val texts = listOf(true to true, false to true, true to false).map { (r, s) -> signedOutMessage(r, s) }
        assertEquals(3, texts.toSet().size)
    }
}
