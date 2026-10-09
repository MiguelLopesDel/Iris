package com.iris.app.ui.screens.detail

import com.iris.app.data.model.MediaRecord
import org.junit.Assert.assertEquals
import org.junit.Test

class ViewerSequenceTest {

    private fun server(vararg indices: Int) = indices.map { ViewerItem.Server(it) }

    @Test
    fun `the viewer pages through the grid it was opened from`() {
        ViewerSequence.set(server(7, 3, 9))

        assertEquals(server(7, 3, 9), ViewerSequence.around(ViewerItem.Server(3)))
    }

    @Test
    fun `an item outside the known grid opens alone`() {
        ViewerSequence.set(server(7, 3, 9))

        assertEquals(server(42), ViewerSequence.around(ViewerItem.Server(42)))
    }

    @Test
    fun `an item listed twice appears once`() {
        ViewerSequence.set(server(1, 2, 1))

        assertEquals(server(1, 2), ViewerSequence.around(ViewerItem.Server(1)))
    }

    @Test
    fun `the phone's own photos page along with Iris's, in the grid's order`() {
        ViewerSequence.setRecords(
            listOf(
                MediaRecord(index = 5, arquivo = "IMG_0001.jpg"),
                MediaRecord(index = -1, arquivo = "IMG_0002.jpg", deviceUri = "content://media/photo/2"),
                MediaRecord(index = 6, arquivo = "IMG_0003.jpg"),
            )
        )

        val device = ViewerItem.Device("content://media/photo/2")
        assertEquals(
            listOf(ViewerItem.Server(5), device, ViewerItem.Server(6)),
            ViewerSequence.around(device),
        )
    }
}
