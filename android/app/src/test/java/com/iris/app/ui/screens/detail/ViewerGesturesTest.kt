package com.iris.app.ui.screens.detail

import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ViewerGesturesTest {
    private val screen = Size(1000f, 2000f)

    @Test
    fun `unzoomed the image cannot move`() {
        assertEquals(Offset.Zero, ViewerGestures.clampOffset(Offset(300f, -200f), screen, 1f))
    }

    @Test
    fun `zoomed the image pans only as far as its edges`() {
        // At 2x the image overhangs half a screen in each direction.
        assertEquals(Offset(500f, -1000f), ViewerGestures.clampOffset(Offset(900f, -5000f), screen, 2f))
    }

    @Test
    fun `a double tap keeps the tapped point under the finger`() {
        val tap = Offset(750f, 1000f)
        val offset = ViewerGestures.offsetKeeping(tap, screen, 2f)
        // Scaled around the centre, then translated: the tapped point lands where it was.
        val center = Offset(500f, 1000f)
        val landed = center + (tap - center) * 2f + offset
        assertEquals(tap, landed)
    }

    @Test
    fun `a double tap near a corner stops at the image edge`() {
        assertEquals(Offset(500f, 1000f), ViewerGestures.offsetKeeping(Offset(0f, 0f), screen, 2f))
    }

    @Test
    fun `dragging down far enough closes and up opens the panel`() {
        assertEquals(ViewerGestures.Swipe.CLOSE, ViewerGestures.swipeVerdict(150f, 100f))
        assertEquals(ViewerGestures.Swipe.SHOW_INFO, ViewerGestures.swipeVerdict(-150f, 100f))
        assertEquals(ViewerGestures.Swipe.NONE, ViewerGestures.swipeVerdict(60f, 100f))
    }

    @Test
    fun `a sideways drag is left to the pager`() {
        assertFalse(ViewerGestures.isVertical(Offset(80f, 40f), slop = 10f))
        assertFalse(ViewerGestures.isVertical(Offset(0f, 5f), slop = 10f))
        assertTrue(ViewerGestures.isVertical(Offset(10f, 60f), slop = 10f))
    }
}
