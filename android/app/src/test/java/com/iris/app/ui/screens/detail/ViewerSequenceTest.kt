package com.iris.app.ui.screens.detail

import org.junit.Assert.assertEquals
import org.junit.Test

class ViewerSequenceTest {

    @Test
    fun `the viewer pages through the grid it was opened from`() {
        ViewerSequence.set(listOf(7, 3, 9))

        assertEquals(listOf(7, 3, 9), ViewerSequence.around(3))
    }

    @Test
    fun `an item outside the known grid opens alone`() {
        ViewerSequence.set(listOf(7, 3, 9))

        assertEquals(listOf(42), ViewerSequence.around(42))
    }

    @Test
    fun `an item listed twice appears once`() {
        ViewerSequence.set(listOf(1, 2, 1))

        assertEquals(listOf(1, 2), ViewerSequence.around(1))
    }
}
