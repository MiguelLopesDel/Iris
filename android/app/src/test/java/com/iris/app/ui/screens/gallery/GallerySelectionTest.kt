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
}
