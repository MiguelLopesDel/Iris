package com.iris.app.data.sync

import com.iris.app.data.model.IngestLimits
import org.junit.Assert.assertEquals
import org.junit.Test

class IngestBatchAvailabilityTest {
    @Test
    fun `only a missing endpoint selects the resumable legacy protocol`() {
        assertEquals(IngestBatchAvailability.Unsupported, IngestBatchAvailability.fromHttp(404, null))
    }

    @Test
    fun `temporary and authentication failures defer instead of changing upload protocol`() {
        listOf(401, 408, 429, 500, 503).forEach { status ->
            assertEquals(IngestBatchAvailability.Retry, IngestBatchAvailability.fromHttp(status, null))
        }
    }

    @Test
    fun `valid limits enable the batch protocol`() {
        assertEquals(
            IngestBatchAvailability.Supported(IngestBatchSize(16, 32L shl 20, 8L shl 20)),
            IngestBatchAvailability.fromHttp(
                200,
                IngestLimits(maxItems = 16, maxBytes = 32L shl 20, suggestedItems = 16),
            ),
        )
    }

    @Test
    fun `successful but unusable limits do not trigger a protocol downgrade`() {
        assertEquals(IngestBatchAvailability.Retry, IngestBatchAvailability.fromHttp(200, IngestLimits()))
    }
}
