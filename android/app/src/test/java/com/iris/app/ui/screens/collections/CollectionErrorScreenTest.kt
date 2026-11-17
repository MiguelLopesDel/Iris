package com.iris.app.ui.screens.collections

import com.iris.app.data.model.MediaRecord
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class CollectionErrorScreenTest {

    @Test
    fun `a failed later page keeps the loaded photos on screen`() {
        val loaded = CollectionMediaUiState(
            collectionId = 1, collectionName = "Férias", members = listOf(MediaRecord(index = 1)), page = 1,
        )

        assertFalse(loaded.copy(error = "O servidor respondeu 500").showsErrorScreen)
    }

    @Test
    fun `a collection that could not load at all shows the error`() {
        assertTrue(CollectionMediaUiState(collectionId = 1, collectionName = "Férias", error = "offline").showsErrorScreen)
    }
}
