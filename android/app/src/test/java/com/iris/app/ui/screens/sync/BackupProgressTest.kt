package com.iris.app.ui.screens.sync

import com.iris.app.data.model.UploadJobState
import com.iris.app.data.model.UploadQueueSummary
import com.iris.app.data.sync.MediaStoreScanner
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class BackupProgressTest {

    private fun summary(vararg counts: Pair<UploadJobState, Int>) = UploadQueueSummary.from(counts.toMap())

    @Test
    fun `the bar is saved over total while photos are still being sent`() {
        val queue = summary(UploadJobState.READY to 300, UploadJobState.QUEUED to 700)

        // The scan finished: the bar keeps following the backup, not the scan.
        assertEquals(0.3f, BackupProgress.of(queue, scan = null, running = true)!!, 0.0001f)
    }

    @Test
    fun `a running scan does not take over the bar once photos are queued`() {
        val queue = summary(UploadJobState.READY to 100, UploadJobState.QUEUED to 100)
        val scan = MediaStoreScanner.ScanProgress(900, 1000)

        assertEquals(0.5f, BackupProgress.of(queue, scan, running = true)!!, 0.0001f)
    }

    @Test
    fun `before anything is queued the scan is what there is to show`() {
        val scan = MediaStoreScanner.ScanProgress(250, 1000)

        assertEquals(0.25f, BackupProgress.of(summary(), scan, running = true)!!, 0.0001f)
    }

    @Test
    fun `a finished backup with nothing running shows no bar`() {
        assertNull(BackupProgress.of(summary(UploadJobState.READY to 10), scan = null, running = false))
    }
}
