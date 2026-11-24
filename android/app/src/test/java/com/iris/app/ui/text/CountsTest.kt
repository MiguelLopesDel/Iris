package com.iris.app.ui.text

import org.junit.Assert.assertEquals
import org.junit.Test

class CountsTest {

    @Test
    fun `one is singular, everything else plural`() {
        assertEquals("1 item", countOf(1, "item", "itens"))
        assertEquals("2 itens", countOf(2, "item", "itens"))
        assertEquals("0 mídias", countOf(0, "mídia", "mídias"))
    }
}
