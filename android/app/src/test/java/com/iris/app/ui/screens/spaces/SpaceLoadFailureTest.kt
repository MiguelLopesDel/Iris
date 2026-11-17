package com.iris.app.ui.screens.spaces

import com.iris.app.data.model.SpaceItem
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Test

class SpaceLoadFailureTest {

    @Test
    fun `a failed search shows its error instead of the previous filter's photos`() {
        val library = SpaceUiState(spaceId = 1, items = listOf(SpaceItem(id = 1), SpaceItem(id = 2)), nextBefore = 2)
        val searching = library.copy(query = "praia", isLoading = true, isLoadingMore = true)

        val failed = searching.failedToLoad("O servidor respondeu 500")

        assertEquals(emptyList<SpaceItem>(), failed.items)
        assertNull(failed.nextBefore)
        assertFalse(failed.isLoading)
        assertFalse(failed.isLoadingMore)
        assertEquals("O servidor respondeu 500", failed.error)
        assertEquals("praia", failed.query)
    }
}
