package com.iris.app

import com.iris.app.data.model.UploadJobState
import com.iris.app.data.model.UploadQueueSummary
import org.junit.Assert.assertEquals
import org.junit.Test

class UploadQueueSummaryTest {

    @Test
    fun `a duplicate counts as finished because the server already holds it`() {
        val summary = UploadQueueSummary.from(
            mapOf(UploadJobState.READY to 3, UploadJobState.DUPLICATE to 2)
        )

        assertEquals(5, summary.finished)
        assertEquals(0, summary.failed)
    }

    @Test
    fun `accepted but not yet indexed is neither sending nor done`() {
        val summary = UploadQueueSummary.from(
            mapOf(UploadJobState.PENDING_PROCESSING to 4, UploadJobState.PROCESSING to 1)
        )

        assertEquals(5, summary.processing)
        assertEquals(0, summary.uploading)
        assertEquals(0, summary.finished)
    }

    @Test
    fun `both failure states land in the same bucket`() {
        val summary = UploadQueueSummary.from(
            mapOf(UploadJobState.FAILED to 2, UploadJobState.FAILED_PROCESSING to 3)
        )

        assertEquals(5, summary.failed)
    }

    @Test
    fun `every job state is accounted for exactly once`() {
        // Guards the real risk: a new state added to the enum and silently
        // dropped from the summary, so the numbers stop adding up to the queue.
        val counts = UploadJobState.entries.associateWith { 1 }

        val summary = UploadQueueSummary.from(counts)

        assertEquals(UploadJobState.entries.size, summary.total)
    }

    @Test
    fun `an empty queue summarises to zero`() {
        val summary = UploadQueueSummary.from(emptyMap())

        assertEquals(0, summary.total)
    }
}
