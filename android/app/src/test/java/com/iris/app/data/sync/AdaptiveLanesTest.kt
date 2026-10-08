package com.iris.app.data.sync

import kotlinx.coroutines.async
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.yield
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class AdaptiveLanesTest {

    /** Feeds one decision's worth of batches at [lanes], each sent at [perLaneRate] bytes/s. */
    private fun AdaptiveLanes.window(perLaneRate: Long) {
        val lanes = this.lanes
        repeat(2 * lanes) { recordBatch(lanes, bytes = perLaneRate, millis = 1_000) }
    }

    @Test
    fun `adds lanes while each one raises throughput`() {
        val lanes = AdaptiveLanes(initial = 3, max = 8)

        lanes.window(perLaneRate = 8_000_000) // 24 MB/s
        assertEquals(4, lanes.lanes)
        lanes.window(perLaneRate = 8_000_000) // 32 MB/s
        assertEquals(5, lanes.lanes)
    }

    @Test
    fun `steps back to the best count when another lane only queues`() {
        val lanes = AdaptiveLanes(initial = 3, max = 8)

        lanes.window(perLaneRate = 8_000_000) // 3 lanes: 24 MB/s
        lanes.window(perLaneRate = 6_200_000) // 4 lanes: 24.8 MB/s, under 10% more

        assertEquals(3, lanes.lanes)
    }

    @Test
    fun `probes one more lane again after holding`() {
        val lanes = AdaptiveLanes(initial = 3, max = 8, holdDecisions = 2)
        lanes.window(perLaneRate = 8_000_000)
        lanes.window(perLaneRate = 6_000_000)
        assertEquals(3, lanes.lanes)

        lanes.window(perLaneRate = 8_000_000)
        assertEquals(3, lanes.lanes)
        lanes.window(perLaneRate = 8_000_000)

        assertEquals(4, lanes.lanes)
    }

    @Test
    fun `never goes past the maximum`() {
        val lanes = AdaptiveLanes(initial = 3, max = 4)

        repeat(5) { lanes.window(perLaneRate = 8_000_000) }

        assertEquals(4, lanes.lanes)
    }

    @Test
    fun `one slow batch does not decide alone`() {
        val lanes = AdaptiveLanes(initial = 3, max = 8)

        repeat(5) { lanes.recordBatch(3, bytes = 8_000_000, millis = 1_000) }

        assertEquals(3, lanes.lanes)
    }

    @Test
    fun `a window where a lane ran out of work is dropped`() {
        val lanes = AdaptiveLanes(initial = 3, max = 8)

        repeat(5) { lanes.recordBatch(3, bytes = 8_000_000, millis = 1_000) }
        lanes.markIdle()
        lanes.recordBatch(3, bytes = 8_000_000, millis = 1_000)

        assertEquals(3, lanes.lanes)
    }

    @Test
    fun `batches sent under another lane count are ignored`() {
        val lanes = AdaptiveLanes(initial = 3, max = 8)
        lanes.window(perLaneRate = 8_000_000)

        // Started while 3 ran; finished after the step to 4.
        repeat(8) { lanes.recordBatch(3, bytes = 1, millis = 1_000) }

        assertEquals(4, lanes.lanes)
    }

    @Test
    fun `a resting lane runs once the target reaches it and stops when the pass ends`() = runBlocking {
        val lanes = AdaptiveLanes(initial = 3, max = 8)
        assertTrue(lanes.awaitTurn(0))

        val fourth = async { lanes.awaitTurn(3) }
        yield()
        assertFalse(fourth.isCompleted)
        lanes.window(perLaneRate = 8_000_000)
        assertTrue(fourth.await())

        val last = async { lanes.awaitTurn(7) }
        yield()
        lanes.close()
        assertFalse(last.await())
    }
}
