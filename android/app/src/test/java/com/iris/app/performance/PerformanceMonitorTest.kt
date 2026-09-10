package com.iris.app.performance

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class PerformanceMonitorTest {
    @Test
    fun `does not collect until diagnostics are enabled`() {
        val monitor = PerformanceMonitor()

        monitor.record(Metric.NetworkRecords, 42.0)

        assertFalse(monitor.report.value.enabled)
        assertTrue(monitor.report.value.metrics.isEmpty())
    }

    @Test
    fun `summarizes samples without retaining individual request data`() {
        val monitor = PerformanceMonitor(maxSamplesPerMetric = 3)
        monitor.start()
        listOf(10.0, 20.0, 80.0, 100.0).forEach { monitor.record(Metric.NetworkRecords, it) }

        val summary = monitor.report.value.metrics.single()
        assertEquals("network.records.total", summary.name)
        assertEquals(3, summary.count)
        assertEquals(80.0, summary.medianMs, 0.001)
        assertEquals(100.0, summary.p90Ms, 0.001)
    }
}
