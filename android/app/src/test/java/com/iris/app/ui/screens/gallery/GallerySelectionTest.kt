package com.iris.app.ui.screens.gallery

import org.junit.Assert.assertEquals
import org.junit.Test

class GallerySelectionTest {
    @Test
    fun `a tap adds an item and a second tap removes it`() {
        val one = GallerySelection.toggled(emptySet(), 4)
        assertEquals(setOf(4), one)
        assertEquals(setOf(4, 9), GallerySelection.toggled(one, 9))
        // Removing the last one ends the selection.
        assertEquals(emptySet<Int>(), GallerySelection.toggled(one, 4))
    }

    @Test
    fun `a summary says what reached the group and what did not`() {
        assertEquals("3 enviadas para Família", GallerySelection.addedSummary("Família", 3, 0, 0))
        assertEquals(
            "1 enviada para Família; 2 já estavam lá; 1 não pôde ser enviada",
            GallerySelection.addedSummary("Família", 1, 2, 1),
        )
    }
}
