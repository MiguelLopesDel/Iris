package com.iris.app.ui.screens.detail

import org.junit.Assert.assertEquals
import org.junit.Test
import java.time.LocalDate
import java.time.ZoneOffset

class ViewerTitleTest {
    private val today = LocalDate.of(2026, 10, 8)

    @Test
    fun `the capture date and place name the photo`() {
        val title = ViewerTitle.of("2025-03-12T14:32:05", null, "Lisboa, PT", "IMG_0001.jpg", today)

        assertEquals("12 de março de 2025", title.headline)
        assertEquals("14:32 · Lisboa, PT", title.subline)
    }

    @Test
    fun `recent photos say today and yesterday`() {
        assertEquals("Hoje", ViewerTitle.of("2026-10-08T09:00:00", null, null, "a.jpg", today).headline)
        assertEquals("Ontem", ViewerTitle.of("2026-10-07T23:59:00", null, null, "a.jpg", today).headline)
    }

    @Test
    fun `the file time stands in for a missing capture date`() {
        // 2025-01-02T03:04:00Z
        val title = ViewerTitle.of("", 1_735_787_040.0, null, "a.jpg", today, ZoneOffset.UTC)

        assertEquals("2 de janeiro de 2025", title.headline)
        assertEquals("03:04", title.subline)
    }

    @Test
    fun `with no date at all the file name is the title`() {
        val title = ViewerTitle.of(null, null, null, "IMG_0001.jpg", today)

        assertEquals("IMG_0001.jpg", title.headline)
        assertEquals(null, title.subline)
    }
}
