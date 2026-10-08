package com.iris.app.ui.screens.search

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class PendingSearchTest {

    @Test
    fun `the text of a photo becomes one search line, taken once`() {
        PendingSearch.set("  FELIZ\n\n ANIVERSÁRIO  ")

        assertEquals("FELIZ ANIVERSÁRIO", PendingSearch.take())
        assertNull(PendingSearch.take())
    }

    @Test
    fun `a long text is cut to the start that fits the search field`() {
        PendingSearch.set("a".repeat(500))

        assertEquals(120, PendingSearch.take()!!.length)
    }

    @Test
    fun `blank text hands nothing over`() {
        PendingSearch.set(" \n ")

        assertNull(PendingSearch.take())
    }
}
