package com.iris.app.ui.screens.gallery

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Test

class GalleryLoadStateTest {

    @Test
    fun `an unreachable server ends a pull to refresh`() {
        val refreshing = GalleryUiState(isRefreshing = true, isServerChecking = true)

        val state = refreshing.serverUnreachable("O servidor não respondeu")

        assertFalse(state.isRefreshing)
        assertFalse(state.isServerChecking)
        assertEquals(false, state.isServerOnline)
        assertEquals("SERVER_OFFLINE", state.error)
        assertEquals("O servidor não respondeu", state.offlineReason)
    }

    @Test
    fun `a next page request keeps the error, so a failing server is not asked again at once`() {
        val failed = GalleryUiState(page = 3, error = "SERVER_OFFLINE")

        // The grid re-asks whenever the error changes: it must stay the same value.
        assertEquals("SERVER_OFFLINE", failed.loadingPage(page = 4, isRefresh = false).error)
    }

    @Test
    fun `a first page clears the previous error`() {
        val failed = GalleryUiState(error = "SERVER_OFFLINE")

        assertNull(failed.loadingPage(page = 1, isRefresh = true).error)
        assertEquals(true, failed.loadingPage(page = 1, isRefresh = true).isRefreshing)
    }
}
