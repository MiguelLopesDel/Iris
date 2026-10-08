package com.iris.app.ui.screens.detail

import org.junit.Assert.assertEquals
import org.junit.Test

class DetailFormatTest {

    @Test
    fun `megapixels are rounded to one decimal, as a gallery shows them`() {
        assertEquals("1,2 MP", megapixels(1080, 1077))
        assertEquals("12,2 MP", megapixels(4032, 3024))
    }

    @Test
    fun `sizes use the unit people read`() {
        assertEquals("401 kB", formatBytes(410_624))
        assertEquals("3,4 MB", formatBytes(3_565_158))
        assertEquals("512 B", formatBytes(512))
    }
}
