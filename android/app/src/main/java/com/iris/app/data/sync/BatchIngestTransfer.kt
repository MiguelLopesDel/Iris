package com.iris.app.data.sync

import android.net.Uri
import com.iris.app.data.model.IngestLimits
import com.iris.app.data.model.LocalUploadJob
import com.iris.app.data.model.UploadCompleteBatchItemResponse
import com.iris.app.data.model.UploadInitBatchItemRequest
import com.iris.app.data.model.UploadInitBatchRequest
import com.iris.app.data.model.UploadJobState
import com.iris.app.data.sync.ResumableUploadTransfer.ItemResult
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody
import okio.BufferedSink
import retrofit2.HttpException
import java.io.IOException

/**
 * Sends a batch of small queued photos with one reservation and one request.
 *
 * One at a time, every photo cost a reservation slot, a PUT and a completion,
 * and the server synced each file on its own; on a spinning disk those fixed
 * costs, not the bytes, bounded the backup. Here the batch is reserved with
 * `"ingest": true` (the server also admits it then) and its files travel back
 * to back in one `POST /api/sync/ingest`, which stores and finishes them all.
 *
 * Each photo keeps its own outcome, decided by the same rules as the
 * resumable path ([ResumableUploadTransfer]), whose content checks it reuses.
 */
internal class BatchIngestTransfer(
    private val context: ResumableUploadTransfer.Context,
    private val single: ResumableUploadTransfer,
    private val observer: ResumableUploadTransfer.Observer,
) {
    /** Per-job results, and the limits the server reported for the next batch. */
    data class Outcome(val results: Map<Long, ItemResult>, val limits: IngestLimits?)

    suspend fun send(queued: List<LocalUploadJob>): Outcome = withContext(Dispatchers.IO) {
        val results = linkedMapOf<Long, ItemResult>()
        val current = mutableListOf<LocalUploadJob>()
        for (job in queued) {
            try {
                current += single.withCurrentContent(job)
            } catch (_: IOException) {
                results[job.id] = unreadable(job)
            }
        }
        if (current.isEmpty()) return@withContext Outcome(results, null)

        val db = context.dbHelper
        val accountKey = context.accountKey
        val api = context.apiServiceProvider(context.sessionIdentity)
        var sending: List<Pair<LocalUploadJob, String>> = emptyList()
        try {
            context.ensureSession()
            val reserved = api.initUploadBatch(
                UploadInitBatchRequest(uploads = current.map(::reservation), ingest = true)
            ).uploads.associateBy { it.clientUploadId }
            context.ensureSession()
            val toSend = mutableListOf<Pair<LocalUploadJob, String>>()
            for (job in current) {
                val answer = reserved[single.clientUploadId(job)]
                when {
                    answer == null -> results[job.id] = requeue(job)
                    answer.errorCode != null -> results[job.id] = settleError(job, answer.errorCode, answer.errorMessage)
                    answer.state == "uploading" && !answer.uploadId.isNullOrBlank() -> {
                        db.updateUploadStarted(accountKey, job.id, answer.uploadId, 0L, answer.chunkSize)
                        toSend += job to answer.uploadId
                    }
                    else -> results[job.id] = settleState(job, answer.state, null)
                }
            }
            sending = toSend
            if (sending.isEmpty()) return@withContext Outcome(results, null)

            val manifest = sending.joinToString(",") { (job, uploadId) -> "$uploadId:${job.byteSize}" }
            val finishRequest = observer.beginPayloadRequest()
            val response = try {
                api.ingest(manifest, batchBody(sending.map { it.first }))
            } finally {
                finishRequest()
            }
            context.ensureSession()
            if (!response.isSuccessful) {
                val code = response.code()
                val message = response.errorBody()?.string()
                for ((job, _) in sending) results[job.id] = settleError(job, code, message)
                return@withContext Outcome(results, null)
            }
            val answer = response.body()
            val byUploadId = answer?.uploads.orEmpty().associateBy { it.uploadId }
            for ((job, uploadId) in sending) {
                val outcome = byUploadId[uploadId]
                results[job.id] = if (outcome == null) requeue(job) else settleItem(job, outcome)
                if (results[job.id] == ItemResult.CONFIRMED) {
                    observer.onBytesAcknowledged(job.byteSize)
                    observer.onProgress(job.id, job.byteSize, job.byteSize)
                }
            }
            Outcome(results, answer?.limits)
        } catch (refused: OriginalRefusedException) {
            // An original this batch was hashed from cannot be read now: hash
            // the batch's files again before they are sent (rare: location
            // access was revoked mid-sync).
            for (job in current) if (job.id !in results) {
                results[job.id] = try {
                    single.refreshContent(job)
                    requeue(job)
                } catch (_: IOException) {
                    unreadable(job)
                }
            }
            Outcome(results, null)
        } catch (failure: IOException) {
            // A dropped link and a file deleted mid-read look the same; only
            // the file's absence on the device tells them apart.
            for (job in current) if (job.id !in results) results[job.id] = unreadable(job)
            Outcome(results, null)
        } catch (cancelled: CancellationException) {
            withContext(NonCancellable) {
                for (job in current) if (job.id !in results) db.updateJobState(accountKey, job.id, UploadJobState.QUEUED)
            }
            throw cancelled
        } catch (failure: HttpException) {
            if (!context.isSessionCurrent()) {
                withContext(NonCancellable) {
                    for (job in current) if (job.id !in results) db.updateJobState(accountKey, job.id, UploadJobState.QUEUED)
                }
                throw CancellationException("Device session changed during media sync")
            }
            for (job in current) if (job.id !in results) {
                results[job.id] = settleError(job, failure.code(), failure.localizedMessage)
            }
            Outcome(results, null)
        }
    }

    private fun reservation(job: LocalUploadJob) = UploadInitBatchItemRequest(
        clientUploadId = single.clientUploadId(job),
        filename = job.filename,
        size = job.byteSize,
        sha256 = job.sha256,
        capturedAt = job.capturedAt,
        source = job.source,
    )

    /** Every job's file, whole, in order; each read as the version it was hashed from. */
    private fun batchBody(jobs: List<LocalUploadJob>): RequestBody = object : RequestBody() {
        override fun contentType() = "application/octet-stream".toMediaType()
        override fun contentLength(): Long = jobs.sumOf { it.byteSize }
        override fun isOneShot(): Boolean = false

        override fun writeTo(sink: BufferedSink) {
            for (job in jobs) {
                context.mediaPayloadSource
                    .createChunkRequestBody(Uri.parse(job.localUri), 0L, job.byteSize, job.hashedOriginal)
                    .writeTo(sink)
            }
        }
    }

    private suspend fun settleItem(job: LocalUploadJob, outcome: UploadCompleteBatchItemResponse): ItemResult =
        if (outcome.errorCode != null) {
            settleError(job, outcome.errorCode, outcome.errorMessage)
        } else {
            settleState(job, outcome.state, outcome.errorMessage)
        }

    private suspend fun settleState(job: LocalUploadJob, state: String?, message: String?): ItemResult {
        val db = context.dbHelper
        val accountKey = context.accountKey
        return when (IngestItemPolicy.ofState(state)) {
            IngestItemPolicy.Action.READY -> { db.updateJobState(accountKey, job.id, UploadJobState.READY); ItemResult.CONFIRMED }
            IngestItemPolicy.Action.DUPLICATE -> { db.updateJobState(accountKey, job.id, UploadJobState.DUPLICATE); ItemResult.CONFIRMED }
            IngestItemPolicy.Action.PENDING -> { db.updateJobState(accountKey, job.id, UploadJobState.PENDING_PROCESSING); ItemResult.CONFIRMED }
            IngestItemPolicy.Action.SEND_AGAIN -> requeue(job)
            else -> {
                db.updateJobState(accountKey, job.id, UploadJobState.FAILED,
                    message ?: "O servidor informa que este envio está em estado $state")
                ItemResult.FAILED
            }
        }
    }

    private suspend fun settleError(job: LocalUploadJob, code: Int, message: String?): ItemResult {
        val db = context.dbHelper
        val accountKey = context.accountKey
        return when (IngestItemPolicy.ofError(code)) {
            IngestItemPolicy.Action.HASH_AGAIN -> {
                // The bytes sent are not the ones hashed: the file changed.
                val refreshed = try {
                    single.refreshContent(job)
                } catch (_: IOException) {
                    return unreadable(job)
                }
                if (refreshed.sha256 == job.sha256 && refreshed.byteSize == job.byteSize) {
                    db.updateJobState(accountKey, job.id, UploadJobState.FAILED,
                        message ?: "O servidor recebeu bytes diferentes do arquivo")
                    ItemResult.FAILED
                } else {
                    requeue(job)
                }
            }
            IngestItemPolicy.Action.RESERVE_AGAIN -> {
                db.resetUploadProgress(accountKey, job.id)
                ItemResult.RETRY
            }
            IngestItemPolicy.Action.SEND_AGAIN -> requeue(job)
            else -> {
                db.updateJobState(accountKey, job.id, UploadJobState.FAILED, message ?: "Erro HTTP $code durante envio")
                ItemResult.FAILED
            }
        }
    }

    private suspend fun requeue(job: LocalUploadJob): ItemResult {
        context.dbHelper.updateJobState(context.accountKey, job.id, UploadJobState.QUEUED)
        return ItemResult.RETRY
    }

    private suspend fun unreadable(job: LocalUploadJob): ItemResult =
        if (!context.mediaPayloadSource.isAvailable(Uri.parse(job.localUri))) {
            single.markSourceMissing(job)
        } else {
            requeue(job)
        }
}
