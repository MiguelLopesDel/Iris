package com.iris.app.data.sync

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.update

/**
 * How many batches are in flight, found by trying.
 *
 * The right number depends on the path, not the device: one connection
 * through a VPN tunnel carries a fraction of what the link does, so more
 * connections side by side go faster, until the link or the server is full
 * and more only queue. The controller climbs one lane at a time while each
 * step raises throughput by at least [minGain], steps back to the best count
 * when one does not, and probes again after a while, since the network
 * changes during a long sync.
 *
 * Throughput is measured per batch (bytes over the time it took), times the
 * lanes running: a whole batch is acknowledged at once, so counting
 * acknowledged bytes in a time window swings by a batch either way. A window
 * in which a lane ran out of work says nothing about lanes and is dropped.
 */
internal class AdaptiveLanes(
    initial: Int = INITIAL,
    private val max: Int = MAX,
    private val minGain: Double = MIN_GAIN,
    private val holdDecisions: Int = HOLD_DECISIONS,
) {
    private data class State(val target: Int, val closed: Boolean = false)

    private enum class Phase { CLIMBING, HOLDING }

    private val state = MutableStateFlow(State(initial.coerceIn(1, max)))

    /** The lanes currently allowed to run: lane i runs while i < lanes. */
    val lanes: Int
        get() = state.value.target

    private var phase = Phase.CLIMBING
    private var bestRate: Double? = null
    private var bestLanes = state.value.target
    private var held = 0

    // The current window: batches that started and ended under the current target.
    private var windowBytes = 0L
    private var windowMillis = 0L
    private var windowBatches = 0

    /**
     * Suspends a lane above the target until the target reaches it or the pass
     * ends. Returns whether the lane should run another batch.
     */
    suspend fun awaitTurn(lane: Int): Boolean {
        val now = state.first { lane < it.target || it.closed }
        return lane < now.target
    }

    /** One batch of [bytes] took [millis], sent while [lanesAtStart] lanes ran. */
    @Synchronized
    fun recordBatch(lanesAtStart: Int, bytes: Long, millis: Long) {
        if (bytes <= 0L || millis <= 0L || lanesAtStart != state.value.target) return
        windowBytes += bytes
        windowMillis += millis
        windowBatches++
        // Two batches per lane: enough that one slow batch does not decide.
        if (windowBatches < 2 * lanesAtStart) return
        val rate = lanesAtStart * windowBytes * 1_000.0 / windowMillis
        decide(lanesAtStart, rate)
    }

    /** A lane found no work: this window measured the queue, not the lanes. */
    @Synchronized
    fun markIdle() {
        resetWindow()
    }

    /** The pass is over: lanes waiting for a turn stop. */
    fun close() {
        state.update { it.copy(closed = true) }
    }

    private fun decide(lanes: Int, rate: Double) {
        resetWindow()
        val best = bestRate
        when (phase) {
            Phase.CLIMBING -> when {
                best == null || rate >= best * (1 + minGain) -> {
                    bestRate = rate
                    bestLanes = lanes
                    if (lanes < max) setTarget(lanes + 1) else hold()
                }
                // No gain: the extra lane only queued. Go back to the best count.
                else -> {
                    setTarget(bestLanes)
                    hold()
                }
            }
            Phase.HOLDING -> {
                held++
                if (held >= holdDecisions && lanes < max) {
                    // The path may have changed: measure here again and probe one more.
                    phase = Phase.CLIMBING
                    bestRate = rate
                    bestLanes = lanes
                    setTarget(lanes + 1)
                }
            }
        }
    }

    private fun hold() {
        phase = Phase.HOLDING
        held = 0
    }

    private fun setTarget(lanes: Int) {
        state.update { it.copy(target = lanes) }
    }

    private fun resetWindow() {
        windowBytes = 0L
        windowMillis = 0L
        windowBatches = 0
    }

    companion object {
        /** Where a pass starts: measured best against an HDD server on a home network. */
        const val INITIAL = 3
        const val MAX = 8
        const val MIN_GAIN = 0.10
        const val HOLD_DECISIONS = 6
    }
}
