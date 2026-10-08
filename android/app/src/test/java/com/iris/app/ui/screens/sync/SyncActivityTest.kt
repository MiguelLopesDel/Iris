package com.iris.app.ui.screens.sync

import com.iris.app.data.sync.MediaStoreScanner
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class SyncActivityTest {

    @Test
    fun `a scan shows as running even before anything uploads`() {
        val state = SyncUiState(scanProgress = MediaStoreScanner.ScanProgress(120, 6900))

        assertEquals(SyncActivity.SCANNING, SyncActivity.of(state))
        assertTrue(SyncActivity.of(state).isRunning)
    }

    @Test
    fun `a scan while photos are already being sent says both`() {
        val state = SyncUiState(scanProgress = MediaStoreScanner.ScanProgress(120, 6900), isSyncing = true)

        assertEquals(SyncActivity.SCANNING_AND_UPLOADING, SyncActivity.of(state))
        assertTrue(SyncActivity.of(state).isRunning)
    }

    @Test
    fun `a pending retry leaves the button available`() {
        val state = SyncUiState(retryPending = true)

        assertEquals(SyncActivity.RETRY_PENDING, SyncActivity.of(state))
        assertFalse(SyncActivity.of(state).isRunning)
    }

    @Test
    fun `a running upload wins over a pending retry`() {
        assertEquals(SyncActivity.UPLOADING, SyncActivity.of(SyncUiState(isSyncing = true, retryPending = true)))
    }

    @Test
    fun `nothing happening is idle`() {
        assertEquals(SyncActivity.IDLE, SyncActivity.of(SyncUiState()))
    }
}
