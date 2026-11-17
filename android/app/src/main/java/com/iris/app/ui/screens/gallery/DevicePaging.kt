package com.iris.app.ui.screens.gallery

/**
 * Which pages count as loaded after a page of phone media lands. A first page
 * replaces the phone records (a refresh, a change), so the pages after it are
 * gone and must be fetched again: counted as still loaded, the next request
 * skipped them, and their items vanished until the filter changed.
 */
internal object DevicePaging {
    fun devicePageAfter(loadedDevicePage: Int, landed: Int): Int =
        if (landed == 1) 1 else maxOf(loadedDevicePage, landed)

    /** The grid's page; with server records loaded, the server's paging decides it. */
    fun pageAfter(loadedPage: Int, landed: Int, hasServerRecords: Boolean): Int =
        if (landed == 1 && !hasServerRecords) 1 else maxOf(loadedPage, landed)
}
