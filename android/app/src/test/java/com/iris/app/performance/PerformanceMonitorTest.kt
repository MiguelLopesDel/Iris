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

    @Test
    fun `reports aggregate acknowledged upload throughput only while diagnostics are enabled`() {
        val monitor = PerformanceMonitor(maxSamplesPerMetric = 3)

        monitor.recordTransfer(bytes = 1_048_576L, elapsedMillis = 1_000.0, confirmedItems = 2L)
        assertEquals(null, monitor.report.value.upload)

        monitor.start()
        monitor.recordTransfer(bytes = 1_048_576L, elapsedMillis = 1_000.0, confirmedItems = 2L)
        monitor.recordTransfer(bytes = 3_145_728L, elapsedMillis = 3_000.0, confirmedItems = 6L)

        val summary = monitor.report.value.upload!!
        assertEquals(2, summary.runs)
        assertEquals(4_194_304L, summary.bytes)
        assertEquals(4_000.0, summary.elapsedMillis, 0.001)
        assertEquals(1.0, summary.mibPerSecond, 0.001)
        assertEquals(8L, summary.confirmedItems)
        assertEquals(2.0, summary.itemsPerSecond, 0.001)
    }

    @Test
    fun `distinguishes queue wall throughput from active payload throughput`() {
        val monitor = PerformanceMonitor()
        monitor.start()

        monitor.recordTransfer(
            bytes = 10L * 1_048_576L,
            elapsedMillis = 2_000.0,
            activeElapsedMillis = 250.0,
            confirmedItems = 5L,
        )

        val summary = monitor.report.value.upload!!
        assertEquals(2_000.0, summary.elapsedMillis, 0.001)
        assertEquals(250.0, summary.activeElapsedMillis, 0.001)
        assertEquals(5.0, summary.mibPerSecond, 0.001)
        assertEquals(40.0, summary.activeMibPerSecond, 0.001)
        assertEquals(5L, summary.confirmedItems)
        assertEquals(2.5, summary.itemsPerSecond, 0.001)
    }

    @Test
    fun `counts server deduplicated items even when no payload bytes were needed`() {
        val monitor = PerformanceMonitor()
        monitor.start()

        monitor.recordTransfer(
            bytes = 0L,
            elapsedMillis = 500.0,
            activeElapsedMillis = 0.0,
            confirmedItems = 1L,
        )

        val summary = monitor.report.value.upload!!
        assertEquals(0L, summary.bytes)
        assertEquals(1L, summary.confirmedItems)
        assertEquals(2.0, summary.itemsPerSecond, 0.001)
        assertEquals(0.0, summary.activeMibPerSecond, 0.001)
    }

    @Test
    fun `does not attribute a queue run to a diagnostics session started midway`() {
        val monitor = PerformanceMonitor()
        val generationAtQueueStart = monitor.activeGeneration()

        monitor.start()

        assertEquals(null, generationAtQueueStart)
        assertEquals(null, monitor.report.value.upload)
    }

    @Test
    fun `does not record a queue run into a later diagnostics generation`() {
        val monitor = PerformanceMonitor()
        monitor.start()
        val originalGeneration = monitor.activeGeneration()!!

        monitor.stop()
        monitor.start()
        monitor.recordTransferForGeneration(
            generation = originalGeneration,
            bytes = 10_000_000L,
            elapsedMillis = 100.0,
            activeElapsedMillis = 50.0,
        )

        assertEquals(null, monitor.report.value.upload)
    }

    @Test
    fun `active payload duration counts overlapping PUTs once and excludes scan gaps`() {
        var nowNanos = 0L
        val tracker = UploadTransferActivityTracker(nowNanos = { nowNanos })

        val finishFirst = tracker.beginRequest()
        nowNanos = 100_000_000L
        val finishSecond = tracker.beginRequest()
        nowNanos = 200_000_000L
        finishFirst()
        nowNanos = 300_000_000L
        finishSecond()
        nowNanos = 2_300_000_000L // two seconds of idle MediaStore scanning

        assertEquals(300.0, tracker.finishAndGetActiveMillis(), 0.001)
    }
}
