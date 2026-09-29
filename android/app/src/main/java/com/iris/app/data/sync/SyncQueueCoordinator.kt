package com.iris.app.data.sync

import kotlinx.coroutines.async
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.first

/** Coordinates media discovery with the durable upload queue. */
object SyncQueueCoordinator {
    /**
     * A versioned wake-up signal shared by discovery and the upload workers.
     * Versioning (rather than a conflated one-shot event) lets every idle
     * worker detect that the durable queue changed, without losing a signal
     * between checking the database and suspending.
     */
    class WorkSignal internal constructor() {
        data class Snapshot(
            val revision: Long,
            val scanFinished: Boolean,
            val workersStopped: Boolean,
        )

        private val state = MutableStateFlow(Snapshot(0L, scanFinished = false, workersStopped = false))

        val snapshot: Snapshot
            get() = state.value

        fun signalNewWork() {
            update { it.copy(revision = it.revision + 1L) }
        }

        fun finishScan() {
            update { it.copy(revision = it.revision + 1L, scanFinished = true) }
        }

        fun stopWorkers() {
            update { it.copy(revision = it.revision + 1L, workersStopped = true) }
        }

        suspend fun awaitChange(afterRevision: Long) {
            state.first { it.revision != afterRevision || it.scanFinished || it.workersStopped }
        }

        @Synchronized
        private fun update(transform: (Snapshot) -> Snapshot) {
            state.value = transform(state.value)
        }
    }

    data class Result<T>(
        val scanResult: T,
        val queueCompleted: Boolean,
    )

    /**
     * Scan for new media while draining the account's durable queue. The callback
     * must be invoked after each newly inserted job so the queue can start before
     * a complete MediaStore scan finishes.
     */
    suspend fun <T> scanAndDrain(
        scanAndEnqueue: suspend (onNewJobEnqueued: suspend () -> Unit) -> T,
        drainQueue: suspend (workSignal: WorkSignal) -> Boolean,
    ): Result<T> = coroutineScope {
        val workSignal = WorkSignal()
        // Start immediately so jobs persisted by an earlier run drain while a
        // fresh MediaStore scan is still discovering additional items.
        val drainer = async { drainQueue(workSignal) }

        val scanResult = try {
            scanAndEnqueue { workSignal.signalNewWork() }
        } finally {
            workSignal.finishScan()
        }

        Result(scanResult, drainer.await())
    }
}
