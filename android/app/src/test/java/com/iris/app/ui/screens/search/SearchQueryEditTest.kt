package com.iris.app.ui.screens.search

import com.iris.app.data.model.MediaRecord
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class SearchQueryEditTest {
    private val searching = SearchUiState(query = "gato", isSearching = true, hasSearched = true)

    @Test
    fun `clearing the field while a search runs stops the spinner and the results`() {
        val state = searching.withQuery("")

        assertFalse(state.isSearching)
        assertFalse(state.hasSearched)
        assertEquals(emptyList<MediaRecord>(), state.results)
    }

    @Test
    fun `a query too short to search stops the spinner of the cancelled search`() {
        assertFalse(searching.withQuery("g").isSearching)
    }

    @Test
    fun `a longer query keeps the running state until its own search replaces it`() {
        val state = searching.withQuery("gatos")

        assertEquals("gatos", state.query)
        assertTrue(state.isSearching)
    }
}
