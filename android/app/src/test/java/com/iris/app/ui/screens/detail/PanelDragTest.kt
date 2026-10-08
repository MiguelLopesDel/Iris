package com.iris.app.ui.screens.detail

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class PanelDragTest {
    private fun panel() = PanelDrag(maxExpansion = 400f, closeDistance = 100f)

    @Test
    fun `dragging up grows the panel until it nearly covers the photo, then the content scrolls`() {
        val drag = panel()

        assertEquals(-300f, drag.beforeScroll(-300f), 0f)
        assertEquals(300f, drag.expansion, 0f)
        // Only 100 more fit; the other 100 are left for the content to scroll.
        assertEquals(-100f, drag.beforeScroll(-200f), 0f)
        assertEquals(400f, drag.expansion, 0f)
        assertEquals(0f, drag.beforeScroll(-50f), 0f)
    }

    @Test
    fun `dragging down shrinks the panel first and then pulls it away`() {
        val drag = panel()
        drag.beforeScroll(-150f)

        drag.afterScroll(200f)

        assertEquals(0f, drag.expansion, 0f)
        assertEquals(50f, drag.pull, 0f)
    }

    @Test
    fun `a long pull closes the panel and a short one springs back`() {
        val drag = panel()
        drag.afterScroll(60f)
        assertFalse(drag.release(velocity = 0f))

        drag.afterScroll(60f)
        assertTrue(drag.release(velocity = 0f))
    }

    @Test
    fun `a quick flick down closes even a short pull`() {
        val drag = panel()
        drag.afterScroll(20f)

        assertTrue(drag.release(velocity = 3_000f))
    }

    @Test
    fun `a release without any pull never closes`() {
        assertFalse(panel().release(velocity = 5_000f))
    }

    @Test
    fun `dragging back up takes the pull back before growing the panel`() {
        val drag = panel()
        drag.afterScroll(80f)

        drag.beforeScroll(-100f)

        assertEquals(0f, drag.pull, 0f)
        assertEquals(20f, drag.expansion, 0f)
    }
}
