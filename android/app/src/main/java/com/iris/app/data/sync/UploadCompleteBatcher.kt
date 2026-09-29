package com.iris.app.data.sync

import com.iris.app.data.model.UploadCompleteBatchItemRequest
import com.iris.app.data.model.UploadCompleteBatchItemResponse
import com.iris.app.data.model.UploadCompleteBatchRequest
import com.iris.app.data.remote.IrisApiService
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope

/** Batches completion control calls while preserving an independent result per media item. */
internal class UploadCompleteBatcher(
    private val apiService: IrisApiService,
    scope: CoroutineScope,
    private val maxBatchSize: Int = MAX_BATCH_SIZE,
    private val coalesceWindowMillis: Long = COALESCE_WINDOW_MILLIS,
) {
    private data class PendingCompletion(
        val uploadId: String,
        val result: CompletableDeferred<UploadCompleteBatchItemResponse>,
    )

    private val batcher = CoalescingBatcher(
        scope = scope,
        maxBatchSize = maxBatchSize,
        coalesceWindowMillis = coalesceWindowMillis,
        dispatchBatch = ::sendBatch,
        failBatch = { batch, failure -> batch.forEach { it.result.completeExceptionally(failure) } },
    )

    suspend fun complete(uploadId: String): UploadCompleteBatchItemResponse {
        val result = CompletableDeferred<UploadCompleteBatchItemResponse>()
        batcher.enqueue(PendingCompletion(uploadId, result))
        return result.await().also { item ->
            item.errorCode?.let { status ->
                throw UploadCompleteBatchItemException(
                    statusCode = status,
                    message = item.errorMessage ?: "Falha ao concluir o envio",
                )
            }
        }
    }

    fun close() {
        batcher.close()
    }

    private suspend fun sendBatch(batch: List<PendingCompletion>) {
        val response = apiService.completeUploadBatch(
            UploadCompleteBatchRequest(
                uploads = batch.map { item -> UploadCompleteBatchItemRequest(item.uploadId) },
            ),
        )
        val expectedIds = batch.map { it.uploadId }.toSet()
        val byUploadId = response.uploads.associateBy { it.uploadId }
        check(byUploadId.size == batch.size && byUploadId.keys == expectedIds) {
            "O servidor retornou um lote de conclusão incompleto ou incompatível"
        }
        batch.forEach { item -> item.result.complete(byUploadId.getValue(item.uploadId)) }
    }

    companion object {
        const val MAX_BATCH_SIZE = CoalescingBatcher.DEFAULT_MAX_BATCH_SIZE
        const val COALESCE_WINDOW_MILLIS = CoalescingBatcher.DEFAULT_COALESCE_WINDOW_MILLIS
    }
}

internal class UploadCompleteBatchItemException(
    val statusCode: Int,
    message: String,
) : Exception(message)
