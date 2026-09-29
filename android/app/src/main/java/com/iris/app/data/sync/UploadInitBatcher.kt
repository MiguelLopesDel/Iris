package com.iris.app.data.sync

import com.iris.app.data.model.UploadInitBatchItemRequest
import com.iris.app.data.model.UploadInitBatchRequest
import com.iris.app.data.model.UploadInitResponse
import com.iris.app.data.model.UploadInitRequest
import com.iris.app.data.remote.IrisApiService
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope

/** Coalesces concurrent per-item reservations without combining media payloads. */
internal class UploadInitBatcher(
    private val apiService: IrisApiService,
    scope: CoroutineScope,
    private val maxBatchSize: Int = MAX_BATCH_SIZE,
    private val coalesceWindowMillis: Long = COALESCE_WINDOW_MILLIS,
) {
    private data class PendingInit(
        val clientUploadId: String,
        val request: UploadInitRequest,
        val result: CompletableDeferred<UploadInitResponse>,
    )

    private val batcher = CoalescingBatcher(
        scope = scope,
        maxBatchSize = maxBatchSize,
        coalesceWindowMillis = coalesceWindowMillis,
        dispatchBatch = ::sendBatch,
        failBatch = { batch, failure -> batch.forEach { it.result.completeExceptionally(failure) } },
    )

    suspend fun initialize(
        clientUploadId: String,
        request: UploadInitRequest,
    ): UploadInitResponse {
        val result = CompletableDeferred<UploadInitResponse>()
        batcher.enqueue(PendingInit(clientUploadId, request, result))
        return result.await()
    }

    fun close() {
        batcher.close()
    }

    private suspend fun sendBatch(batch: List<PendingInit>) {
        val response = apiService.initUploadBatch(
            UploadInitBatchRequest(
                uploads = batch.map { item ->
                    UploadInitBatchItemRequest(
                        clientUploadId = item.clientUploadId,
                        filename = item.request.filename,
                        size = item.request.size,
                        sha256 = item.request.sha256,
                        capturedAt = item.request.capturedAt,
                        source = item.request.source,
                    )
                },
            ),
        )
        val byClientId = response.uploads.associateBy { it.clientUploadId }
        check(byClientId.size == batch.size && byClientId.keys == batch.map { it.clientUploadId }.toSet()) {
            "O servidor retornou um lote de inicialização incompleto ou incompatível"
        }
        batch.forEach { item ->
            val serverItem = byClientId.getValue(item.clientUploadId)
            if (serverItem.errorCode != null) {
                item.result.completeExceptionally(
                    UploadInitBatchItemException(
                        serverItem.errorCode,
                        serverItem.errorMessage ?: "Falha ao reservar envio",
                    ),
                )
            } else {
                val uploadId = serverItem.uploadId
                if (uploadId == null) {
                    item.result.completeExceptionally(
                        IllegalStateException("Resposta do servidor sem upload_id"),
                    )
                } else {
                    item.result.complete(
                        UploadInitResponse(
                            uploadId = uploadId,
                            offset = serverItem.offset,
                            chunkSize = serverItem.chunkSize,
                            state = serverItem.state,
                        ),
                    )
                }
            }
        }
    }

    companion object {
        const val MAX_BATCH_SIZE = CoalescingBatcher.DEFAULT_MAX_BATCH_SIZE
        const val COALESCE_WINDOW_MILLIS = CoalescingBatcher.DEFAULT_COALESCE_WINDOW_MILLIS
    }
}

internal class UploadInitBatchItemException(
    val statusCode: Int,
    message: String,
) : Exception(message)
