package com.iris.app.ui.screens.gallery

import org.junit.Assert.assertEquals
import org.junit.Test

class DevicePagingTest {

    @Test
    fun `refreshing the phone media starts its paging over`() {
        // Scrolled to page 3 offline, then pulled to refresh: page 1 replaced everything.
        assertEquals(1, DevicePaging.devicePageAfter(loadedDevicePage = 3, landed = 1))
        assertEquals(1, DevicePaging.pageAfter(loadedPage = 3, landed = 1, hasServerRecords = false))
    }

    @Test
    fun `a later page adds to the loaded ones`() {
        assertEquals(4, DevicePaging.devicePageAfter(loadedDevicePage = 3, landed = 4))
    }

    @Test
    fun `with the library loaded, the server's pages keep the grid's page`() {
        assertEquals(3, DevicePaging.pageAfter(loadedPage = 3, landed = 1, hasServerRecords = true))
    }
}
