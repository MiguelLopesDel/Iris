package com.iris.app.data.sync

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlin.coroutines.CoroutineContext

/** Shared bounded coalescing loop for independent control-plane requests. */
internal class CoalescingBatcher<T>(
    scope: CoroutineScope,
    private val maxBatchSize: Int = DEFAULT_MAX_BATCH_SIZE,
    private val coalesceWindowMillis: Long = DEFAULT_COALESCE_WINDOW_MILLIS,
    workerContext: CoroutineContext = Dispatchers.IO,
    private val dispatchBatch: suspend (List<T>) -> Unit,
    private val failBatch: (List<T>, Throwable) -> Unit,
) {
    private val pending = Channel<T>(Channel.UNLIMITED)

    init {
        require(maxBatchSize in 1..DEFAULT_MAX_BATCH_SIZE)
        require(coalesceWindowMillis >= 0L)
        scope.launch(workerContext) { runBatches() }
    }

    suspend fun enqueue(item: T) {
        pending.send(item)
    }

    fun close() {
        pending.close()
    }

    private suspend fun runBatches() {
        for (first in pending) {
            val batch = mutableListOf(first)
            try {
                if (maxBatchSize > 1 && coalesceWindowMillis > 0L) {
                    // Give concurrent callers a bounded chance to join this request.
                    delay(coalesceWindowMillis)
                }
                while (batch.size < maxBatchSize) {
                    val next = pending.tryReceive().getOrNull() ?: break
                    batch += next
                }
                dispatchBatch(batch)
            } catch (failure: Throwable) {
                failBatch(batch, failure)
                if (failure is CancellationException) throw failure
            }
        }
    }

    companion object {
        const val DEFAULT_MAX_BATCH_SIZE = 16
        const val DEFAULT_COALESCE_WINDOW_MILLIS = 8L
    }
}
