package com.iris.app.data.sync

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class UploadSpeedMeterTest {
    private var now = 0L
    private val meter = UploadSpeedMeter(nowMillis = { now }, windowMillis = 10_000L)

    @Test
    fun nothing_is_reported_before_the_first_run() {
        val snapshot = meter.snapshot()
        assertEquals(0L, snapshot.runNumber)
        assertFalse(snapshot.running)
        assertEquals(0.0, snapshot.currentBytesPerSecond, 0.0)
    }

    @Test
    fun current_rate_covers_only_the_recent_window() {
        meter.startRun()
        now = 1_000L; meter.recordAcknowledged(10_000_000L)
        now = 20_000L; meter.recordAcknowledged(5_000_000L)
        now = 25_000L

        val snapshot = meter.snapshot()
        // Only the ack at 20 s is inside the 10 s window ending at 25 s.
        assertEquals(500_000.0, snapshot.currentBytesPerSecond, 0.001)
        assertEquals(15_000_000L * 1_000.0 / 25_000L, snapshot.averageBytesPerSecond, 0.001)
        assertEquals(15_000_000L, snapshot.runBytes)
    }

    @Test
    fun a_run_younger_than_the_window_divides_by_its_age_with_a_one_second_floor() {
        meter.startRun()
        now = 200L; meter.recordAcknowledged(1_000_000L)
        // 200 ms in, one burst must not read as 5 MB/s.
        assertEquals(1_000_000.0, meter.snapshot().currentBytesPerSecond, 0.001)
        now = 4_000L
        assertEquals(250_000.0, meter.snapshot().currentBytesPerSecond, 0.001)
    }

    @Test
    fun rate_decays_to_zero_when_acknowledgements_stop() {
        meter.startRun()
        now = 1_000L; meter.recordAcknowledged(8_000_000L)
        now = 12_000L
        assertEquals(0.0, meter.snapshot().currentBytesPerSecond, 0.0)
        assertTrue(meter.snapshot().averageBytesPerSecond > 0.0)
    }

    @Test
    fun finished_run_keeps_its_totals_and_ignores_late_events() {
        meter.startRun()
        now = 2_000L
        meter.recordAcknowledged(4_000_000L)
        meter.recordConfirmedItem()
        meter.finishRun()
        now = 60_000L
        meter.recordAcknowledged(99L)
        meter.recordConfirmedItem()

        val snapshot = meter.snapshot()
        assertFalse(snapshot.running)
        assertEquals(0.0, snapshot.currentBytesPerSecond, 0.0)
        assertEquals(4_000_000L, snapshot.runBytes)
        assertEquals(1L, snapshot.runItems)
        assertEquals(2_000L, snapshot.runElapsedMillis)
        assertEquals(2_000_000.0, snapshot.averageBytesPerSecond, 0.001)
    }

    @Test
    fun each_run_starts_from_zero_and_advances_the_run_number() {
        meter.startRun()
        meter.recordAcknowledged(1_000L)
        meter.finishRun()
        meter.startRun()

        val snapshot = meter.snapshot()
        assertEquals(2L, snapshot.runNumber)
        assertEquals(0L, snapshot.runBytes)
        assertTrue(snapshot.running)
    }
}
