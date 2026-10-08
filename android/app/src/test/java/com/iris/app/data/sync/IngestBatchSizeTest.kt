package com.iris.app.data.sync

import com.iris.app.data.model.IngestLimits
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class IngestBatchSizeTest {
    @Test
    fun aBatchFollowsTheServersSuggestionWithinItsLimit() {
        val size = IngestBatchSize.from(IngestLimits(maxItems = 256, maxBytes = 64L shl 20, suggestedItems = 128))!!

        assertEquals(128, size.maxItems)
        assertEquals(64L shl 20, size.maxBytes)
        assertEquals(IngestBatchSize.MAX_FILE_BYTES, size.maxFileBytes)
        assertEquals(64, IngestBatchSize.from(IngestLimits(maxItems = 64, maxBytes = 1L shl 30, suggestedItems = 500))!!.maxItems)
    }

    @Test
    fun anOlderServerWithoutLimitsKeepsTheResumablePath() {
        assertNull(IngestBatchSize.from(null))
        assertNull(IngestBatchSize.from(IngestLimits()))
    }

    @Test
    fun aBatchStopsAtItsCountOrItsBytes() {
        val size = IngestBatchSize(maxItems = 3, maxBytes = 1_000, maxFileBytes = 600)

        assertTrue(size.fits(count = 2, bytes = 400, nextSize = 600))
        assertFalse(size.fits(count = 3, bytes = 100, nextSize = 10))
        assertFalse(size.fits(count = 1, bytes = 500, nextSize = 501))
    }

    @Test
    fun aLargeFileKeepsTheResumablePath() {
        val size = IngestBatchSize.from(IngestLimits(maxItems = 64, maxBytes = 32L shl 20))!!

        assertTrue(size.takes(2L shl 20))
        assertFalse(size.takes(IngestBatchSize.MAX_FILE_BYTES + 1))
    }
}
