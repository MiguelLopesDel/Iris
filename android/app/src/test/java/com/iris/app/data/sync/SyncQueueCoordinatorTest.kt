package com.iris.app.data.sync

import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.async
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.ExperimentalCoroutinesApi
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class SyncQueueCoordinatorTest {
    @Test
    fun `newly discovered media starts uploading before the full scan completes`() = runTest {
        val allowScanToFinish = CompletableDeferred<Unit>()
        val scanFinished = CompletableDeferred<Unit>()
        val firstUploadStarted = CompletableDeferred<Unit>()
        val enqueuedItems = mutableListOf<Int>()
        val uploadedItems = mutableListOf<Int>()

        val run = async {
            SyncQueueCoordinator.scanAndDrain(
                scanAndEnqueue = { onNewJobEnqueued ->
                    enqueuedItems += 1
                    onNewJobEnqueued()
                    allowScanToFinish.await()
                    enqueuedItems += 2
                    onNewJobEnqueued()
                    scanFinished.complete(Unit)
                    enqueuedItems.toList()
                },
                drainQueue = { workSignal ->
                    var queueFinished = false
                    while (!queueFinished) {
                        val observedRevision = workSignal.snapshot.revision
                        val nextItem = enqueuedItems.firstOrNull { it !in uploadedItems }
                        if (nextItem != null) {
                            uploadedItems += nextItem
                            if (nextItem == 1) firstUploadStarted.complete(Unit)
                            continue
                        }
                        val currentSignal = workSignal.snapshot
                        if (currentSignal.revision != observedRevision) continue
                        if (currentSignal.scanFinished || currentSignal.workersStopped) {
                            queueFinished = true
                        } else {
                            workSignal.awaitChange(observedRevision)
                        }
                    }
                    true
                },
            )
        }

        runCurrent()
        val firstUploadPrecededScanCompletion = firstUploadStarted.isCompleted && !scanFinished.isCompleted
        allowScanToFinish.complete(Unit)
        advanceUntilIdle()

        assertTrue(
            "The first queued item must start uploading while discovery is still scanning",
            firstUploadPrecededScanCompletion,
        )
        val result = run.await()
        assertTrue(result.queueCompleted)
        assertEquals(listOf(1, 2), result.scanResult)
        assertEquals(listOf(1, 2), uploadedItems)
    }

    @Test
    fun `failed queue drain is not retried repeatedly during the same scan`() = runTest {
        var drainAttempts = 0
        val result = SyncQueueCoordinator.scanAndDrain(
            scanAndEnqueue = { onNewJobEnqueued ->
                onNewJobEnqueued()
                onNewJobEnqueued()
                2
            },
            drainQueue = {
                drainAttempts++
                false
            },
        )

        assertEquals(2, result.scanResult)
        assertEquals(1, drainAttempts)
        assertTrue(!result.queueCompleted)
    }
}
