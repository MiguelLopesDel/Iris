package com.iris.app.data.sync

import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Test
import kotlinx.coroutines.ExperimentalCoroutinesApi

@OptIn(ExperimentalCoroutinesApi::class)
class CoalescingBatcherTest {
    @Test
    fun `batches queued items up to configured limit and flushes remainder`() = runTest {
        val dispatched = mutableListOf<List<Int>>()
        val failures = mutableListOf<Throwable>()
        val batcher = CoalescingBatcher<Int>(
            scope = this,
            maxBatchSize = 2,
            coalesceWindowMillis = 1,
            workerContext = StandardTestDispatcher(testScheduler),
            dispatchBatch = { dispatched.add(it) },
            failBatch = { _, failure -> failures += failure },
        )

        batcher.enqueue(1)
        batcher.enqueue(2)
        batcher.enqueue(3)
        batcher.close()
        advanceUntilIdle()

        assertEquals(listOf(listOf(1, 2), listOf(3)), dispatched)
        assertEquals(emptyList<Throwable>(), failures)
    }

    @Test
    fun `dispatch failure is returned to the batch failure handler`() = runTest {
        val rejected = mutableListOf<Pair<List<Int>, Throwable>>()
        val batcher = CoalescingBatcher<Int>(
            scope = this,
            maxBatchSize = 1,
            coalesceWindowMillis = 0,
            workerContext = StandardTestDispatcher(testScheduler),
            dispatchBatch = { error("network failed") },
            failBatch = { batch, failure -> rejected += batch to failure },
        )

        batcher.enqueue(7)
        batcher.close()
        advanceUntilIdle()

        assertEquals(1, rejected.size)
        assertEquals(listOf(7), rejected.single().first)
        assertEquals("network failed", rejected.single().second.message)
    }
}
