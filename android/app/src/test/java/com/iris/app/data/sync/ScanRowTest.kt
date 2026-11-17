package com.iris.app.data.sync

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Test
import java.io.FileNotFoundException

class ScanRowTest {

    @Test
    fun `a file that is gone is deferred instead of failing the scan`() = runTest {
        val outcome = ScanRow.readOrDefer { throw FileNotFoundException("content://media/external/images/media/1") }

        assertEquals(SyncUploadManager.ScanOutcome.DEFERRED, outcome)
    }

    @Test
    fun `a readable row keeps its own outcome`() = runTest {
        assertEquals(
            SyncUploadManager.ScanOutcome.QUEUED_NEW,
            ScanRow.readOrDefer { SyncUploadManager.ScanOutcome.QUEUED_NEW },
        )
    }

    @Test
    fun `cancellation still stops the scan`() {
        assertThrows(CancellationException::class.java) {
            kotlinx.coroutines.runBlocking { ScanRow.readOrDefer { throw CancellationException("stopped") } }
        }
    }
}
