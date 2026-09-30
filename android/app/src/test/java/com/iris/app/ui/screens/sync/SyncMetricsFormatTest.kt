package com.iris.app.ui.screens.sync

import androidx.work.WorkInfo
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import java.util.Locale

class SyncMetricsFormatTest {
    private val ptBr = Locale.forLanguageTag("pt-BR")

    @Test
    fun speed_is_decimal_and_also_in_megabits_for_speed_test_comparison() {
        assertEquals("28,0 MB/s · 224 Mbps", SyncMetricsFormat.speed(28_000_000.0, ptBr))
        assertEquals("0,0 MB/s · 0 Mbps", SyncMetricsFormat.speed(-5.0, ptBr))
    }

    @Test
    fun bytes_pick_a_readable_unit() {
        assertEquals("512 B", SyncMetricsFormat.bytes(512L, ptBr))
        assertEquals("4 kB", SyncMetricsFormat.bytes(4_200L, ptBr))
        assertEquals("212,3 MB", SyncMetricsFormat.bytes(212_300_000L, ptBr))
        assertEquals("1,25 GB", SyncMetricsFormat.bytes(1_250_000_000L, ptBr))
    }

    @Test
    fun duration_uses_the_two_largest_units() {
        assertEquals("45 s", SyncMetricsFormat.duration(45_000L))
        assertEquals("11 min 3 s", SyncMetricsFormat.duration(663_000L))
        assertEquals("2 h 05 min", SyncMetricsFormat.duration(7_500_000L))
    }

    @Test
    fun remaining_time_needs_both_bytes_and_a_rate() {
        assertEquals(10_000L, SyncMetricsFormat.remainingMillis(200_000_000L, 20_000_000.0))
        assertNull(SyncMetricsFormat.remainingMillis(0L, 20_000_000.0))
        assertNull(SyncMetricsFormat.remainingMillis(1_000L, 0.0))
    }

    @Test
    fun stop_reasons_group_into_actionable_causes() {
        assertEquals(SyncMetricsFormat.StopCause.NONE, SyncMetricsFormat.stopCause(null))
        assertEquals(SyncMetricsFormat.StopCause.TIME_LIMIT, SyncMetricsFormat.stopCause(WorkInfo.STOP_REASON_TIMEOUT))
        assertEquals(
            SyncMetricsFormat.StopCause.CONNECTION_LOST,
            SyncMetricsFormat.stopCause(WorkInfo.STOP_REASON_CONSTRAINT_CONNECTIVITY)
        )
        assertEquals(
            SyncMetricsFormat.StopCause.BATTERY_RESTRICTION,
            SyncMetricsFormat.stopCause(WorkInfo.STOP_REASON_APP_STANDBY)
        )
        assertEquals(SyncMetricsFormat.StopCause.REPLACED, SyncMetricsFormat.stopCause(WorkInfo.STOP_REASON_CANCELLED_BY_APP))
        assertEquals(SyncMetricsFormat.StopCause.OTHER, SyncMetricsFormat.stopCause(12345))
    }
}
