package com.iris.app.data.sync

/**
 * Upload throughput measured from bytes the server acknowledged, the same
 * bytes that are durably saved. It is always on: unlike the opt-in
 * diagnostics, it drives the sync screen and the run history.
 *
 * A run is one pass over the upload queue. The current rate covers the last
 * [windowMillis]; the average covers the whole run, idle waits included.
 */
class UploadSpeedMeter(
    private val nowMillis: () -> Long = { System.nanoTime() / 1_000_000 },
    private val windowMillis: Long = 10_000L,
) {
    private data class Sample(val atMillis: Long, val bytes: Long)

    private val samples = ArrayDeque<Sample>()
    private var runStartedAtMillis: Long? = null
    private var runEndedAtMillis: Long? = null
    private var runBytes = 0L
    private var runItems = 0L
    private var runNumber = 0L

    @Synchronized
    fun startRun() {
        runNumber++
        samples.clear()
        runStartedAtMillis = nowMillis()
        runEndedAtMillis = null
        runBytes = 0L
        runItems = 0L
    }

    @Synchronized
    fun recordAcknowledged(bytes: Long) {
        if (bytes <= 0L || runStartedAtMillis == null || runEndedAtMillis != null) return
        val now = nowMillis()
        samples.addLast(Sample(now, bytes))
        runBytes += bytes
        dropExpired(now)
    }

    @Synchronized
    fun recordConfirmedItem() {
        if (runStartedAtMillis == null || runEndedAtMillis != null) return
        runItems++
    }

    @Synchronized
    fun finishRun() {
        if (runStartedAtMillis != null && runEndedAtMillis == null) runEndedAtMillis = nowMillis()
    }

    @Synchronized
    fun snapshot(): UploadSpeedSnapshot {
        val startedAt = runStartedAtMillis ?: return UploadSpeedSnapshot(runNumber = runNumber)
        val now = nowMillis()
        val endedAt = runEndedAtMillis
        val elapsed = ((endedAt ?: now) - startedAt).coerceAtLeast(0L)
        val current = if (endedAt != null) {
            0.0
        } else {
            dropExpired(now)
            // Early in a run the window is not full yet; dividing by the whole
            // window would understate the rate, and by a few milliseconds
            // would overstate one burst. One second is the floor.
            val span = minOf(windowMillis, elapsed).coerceAtLeast(1_000L)
            samples.sumOf { it.bytes } * 1_000.0 / span
        }
        val average = if (elapsed > 0L) runBytes * 1_000.0 / maxOf(elapsed, 1_000L) else 0.0
        return UploadSpeedSnapshot(
            running = endedAt == null,
            currentBytesPerSecond = current,
            averageBytesPerSecond = average,
            runBytes = runBytes,
            runItems = runItems,
            runElapsedMillis = elapsed,
            runNumber = runNumber,
        )
    }

    private fun dropExpired(now: Long) {
        while (samples.isNotEmpty() && samples.first().atMillis <= now - windowMillis) {
            samples.removeFirst()
        }
    }
}

data class UploadSpeedSnapshot(
    val running: Boolean = false,
    val currentBytesPerSecond: Double = 0.0,
    val averageBytesPerSecond: Double = 0.0,
    val runBytes: Long = 0L,
    val runItems: Long = 0L,
    val runElapsedMillis: Long = 0L,
    /** Increments with every run, so a caller can tell whether a run happened since it looked. */
    val runNumber: Long = 0L,
)
