package com.iris.app.ui.screens.search

import com.iris.app.data.repository.SearchPreferences
import org.junit.Assert.assertEquals
import org.junit.Test

class SearchPreferencesTest {

    @Test
    fun `the saved settings replace the defaults the search sends`() {
        val state = SearchUiState(query = "praia").withPreferences(
            SearchPreferences(balance = 0.8f, threshold = 0.3f, textBonus = 1.5f),
        )

        assertEquals(0.8f, state.balance)
        assertEquals(0.3f, state.threshold)
        assertEquals(1.5f, state.textBonus)
        assertEquals("praia", state.query)
    }

    @Test
    fun `the defaults match what the server assumes`() {
        val defaults = SearchUiState()

        assertEquals(SearchPreferences.DEFAULT, SearchPreferences(defaults.balance, defaults.threshold, defaults.textBonus))
    }
}
