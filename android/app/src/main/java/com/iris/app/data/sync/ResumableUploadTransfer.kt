package com.iris.app.data.sync

import android.net.Uri
import com.iris.app.data.local.UploadDatabaseHelper
import com.iris.app.data.model.LocalUploadJob
import com.iris.app.data.model.UploadInitRequest
import com.iris.app.data.model.UploadJobState
import com.iris.app.data.remote.IrisApiService
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.withContext
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import retrofit2.HttpException
import java.io.IOException
import java.security.MessageDigest
import kotlin.math.min

/** Runs one account-scoped item's resumable upload protocol. */
internal class ResumableUploadTransfer(
    private val context: Context,
    private val observer: Observer,
) {
    internal data class Context(
        val accountKey: String,
        val sessionIdentity: String,
        val dbHelper: UploadDatabaseHelper,
        val apiServiceProvider: (String) -> IrisApiService,
        val mediaPayloadSource: MediaPayloadSource,
        val initBatcher: UploadInitBatcher,
        val completionBatcher: UploadCompleteBatcher,
        val completionMutexes: Array<Mutex>,
        val ensureSession: suspend () -> Unit,
        val isSessionCurrent: () -> Boolean,
    )

    internal data class Observer(
        val onBytesAcknowledged: (Long) -> Unit,
        val beginPayloadRequest: () -> (() -> Unit),
        val onProgress: (Long, Long, Long) -> Unit,
    )

    enum class ItemResult {
        /** The server has the item (uploaded, already present, or processing). */
        CONFIRMED,
        /** The media left the device before it was uploaded; the item is failed and the queue moves on. */
        SOURCE_MISSING,
        /** A transient failure: stop claiming work and let WorkManager retry from committed offsets. */
        RETRY,
    }

    /**
     * Uploads one job. A job whose local media was deleted is failed instead of
     * retried: it can never succeed, and retrying it stopped the whole queue on
     * every run, so one deleted photo halted the backup for good.
     */
    suspend fun execute(job: LocalUploadJob): ItemResult = withContext(Dispatchers.IO) {
        var readFailed = false
        val handled = transfer(job) { readFailed = true }
        when {
            handled -> ItemResult.CONFIRMED
            // A deleted file surfaces as the same IOException as a dropped
            // link; only its absence on the device tells the two apart.
            readFailed && !context.mediaPayloadSource.isAvailable(Uri.parse(job.localUri)) -> markSourceMissing(job)
            else -> ItemResult.RETRY
        }
    }

    private suspend fun markSourceMissing(job: LocalUploadJob): ItemResult {
        context.dbHelper.updateJobState(
            context.accountKey, job.id, UploadJobState.FAILED, SOURCE_MISSING_MESSAGE,
        )
        return ItemResult.SOURCE_MISSING
    }

    private suspend fun transfer(job: LocalUploadJob, onIoFailure: () -> Unit): Boolean = withContext(Dispatchers.IO) {
        val accountKey = context.accountKey
        val db = context.dbHelper
        val api = context.apiServiceProvider(context.sessionIdentity)
        var uploadId = job.uploadId
        var offset = job.nextByteOffset
        var chunkSize = job.chunkSize
        var remoteUploadStarted = !uploadId.isNullOrBlank()

        try {
            context.ensureSession()
            if (uploadId.isNullOrBlank()) {
                val init = context.initBatcher.initialize(
                    clientUploadId(job),
                    UploadInitRequest(
                        filename = job.filename,
                        size = job.byteSize,
                        sha256 = job.sha256,
                        capturedAt = job.capturedAt,
                        source = job.source,
                    ),
                )
                context.ensureSession()
                if (init.state != "uploading") {
                    when (init.state) {
                        "ready" -> db.updateJobState(accountKey, job.id, UploadJobState.READY)
                        "duplicate" -> db.updateJobState(accountKey, job.id, UploadJobState.DUPLICATE)
                        "pending_processing" -> db.updateJobState(accountKey, job.id, UploadJobState.PENDING_PROCESSING)
                        else -> db.updateJobState(
                            accountKey, job.id, UploadJobState.FAILED,
                            "O servidor informa que este envio está em estado ${init.state}",
                        )
                    }
                    return@withContext init.state in setOf("ready", "duplicate", "pending_processing")
                }
                uploadId = init.uploadId
                offset = init.offset
                chunkSize = init.chunkSize
                remoteUploadStarted = true
                db.updateUploadStarted(accountKey, job.id, uploadId, offset, chunkSize)
            }

            val uri = Uri.parse(job.localUri)
            var conflicts = 0
            while (offset < job.byteSize) {
                context.ensureSession()
                val bytesToRead = min(job.byteSize - offset, chunkSize.toLong())
                val body = context.mediaPayloadSource.createChunkRequestBody(uri, offset, bytesToRead)
                val finishRequest = observer.beginPayloadRequest()
                val response = try {
                    api.uploadChunk(uploadId, offset, body)
                } finally {
                    finishRequest()
                }
                context.ensureSession()
                when {
                    response.isSuccessful -> {
                        val nextOffset = response.body()?.offset ?: (offset + bytesToRead)
                        val acknowledged = (nextOffset - offset).coerceIn(0L, bytesToRead)
                        offset = nextOffset
                        conflicts = 0
                        db.updateOffsetTransactionally(accountKey, job.id, offset)
                        observer.onBytesAcknowledged(acknowledged)
                        observer.onProgress(job.id, job.byteSize, offset)
                    }
                    response.code() == 409 -> {
                        if (++conflicts > 3) {
                            db.updateJobState(accountKey, job.id, UploadJobState.FAILED,
                                "Conflito persistente de offset no upload (409)")
                            return@withContext false
                        }
                        val status = api.getUploadStatus(uploadId)
                        context.ensureSession()
                        when (status.state) {
                            "ready" -> { db.updateJobState(accountKey, job.id, UploadJobState.READY); return@withContext true }
                            "duplicate" -> { db.updateJobState(accountKey, job.id, UploadJobState.DUPLICATE); return@withContext true }
                            "pending_processing" -> { db.updateJobState(accountKey, job.id, UploadJobState.PENDING_PROCESSING); return@withContext true }
                            else -> {
                                offset = status.offset
                                db.updateOffsetTransactionally(accountKey, job.id, offset)
                                observer.onProgress(job.id, job.byteSize, offset)
                            }
                        }
                    }
                    response.code() == 401 -> return@withContext false
                    response.code() == 404 -> { db.resetUploadProgress(accountKey, job.id); return@withContext false }
                    else -> {
                        val error = response.errorBody()?.string() ?: "Erro HTTP ${response.code()}"
                        db.updateJobState(accountKey, job.id, UploadJobState.FAILED, error)
                        return@withContext false
                    }
                }
            }

            val mutex = context.completionMutexes[job.sha256.hashCode().ushr(1) % context.completionMutexes.size]
            val completion = mutex.withLock {
                context.ensureSession()
                context.completionBatcher.complete(uploadId)
            }
            context.ensureSession()
            when (completion.state) {
                "pending_processing", "processing" -> db.updateJobState(accountKey, job.id, UploadJobState.PENDING_PROCESSING)
                "duplicate" -> db.updateJobState(accountKey, job.id, UploadJobState.DUPLICATE)
                "failed_processing" -> db.updateJobState(accountKey, job.id, UploadJobState.FAILED,
                    completion.errorMessage ?: "O servidor não conseguiu processar a mídia enviada")
                else -> db.updateJobState(accountKey, job.id, UploadJobState.READY)
            }
            true
        } catch (failure: UploadCompleteBatchItemException) {
            if (failure.statusCode == 404) db.resetUploadProgress(accountKey, job.id)
            else if (failure.statusCode != 401 && failure.statusCode != 409 && failure.statusCode < 500) {
                db.updateJobState(accountKey, job.id, UploadJobState.FAILED,
                    failure.message ?: "Falha ao concluir o envio")
            }
            false
        } catch (failure: IOException) {
            onIoFailure()
            false
        } catch (cancelled: CancellationException) {
            withContext(NonCancellable) { db.updateJobState(accountKey, job.id, UploadJobState.QUEUED) }
            throw cancelled
        } catch (failure: HttpException) {
            if (!context.isSessionCurrent()) {
                withContext(NonCancellable) { db.updateJobState(accountKey, job.id, UploadJobState.QUEUED) }
                throw CancellationException("Device session changed during media sync")
            }
            if (failure.code() == 404 && remoteUploadStarted) db.resetUploadProgress(accountKey, job.id)
            else if (failure.code() == 401 || failure.code() == 409) db.updateJobState(accountKey, job.id, UploadJobState.QUEUED)
            else db.updateJobState(accountKey, job.id, UploadJobState.FAILED,
                failure.localizedMessage ?: "Erro HTTP ${failure.code()} durante envio")
            false
        } catch (failure: Exception) {
            if (!context.isSessionCurrent()) {
                withContext(NonCancellable) { db.updateJobState(accountKey, job.id, UploadJobState.QUEUED) }
                throw CancellationException("Device session changed during media sync")
            }
            db.updateJobState(accountKey, job.id, UploadJobState.FAILED,
                failure.localizedMessage ?: "Erro desconhecido durante envio")
            false
        }
    }

    private fun clientUploadId(job: LocalUploadJob): String {
        val source = job.source
        val stableFields = listOf(
            job.id.toString(), job.filename, job.byteSize.toString(), job.sha256, job.capturedAt,
            source?.id.orEmpty(), source?.name.orEmpty(), source?.relativePath.orEmpty(),
            source?.volume.orEmpty(), source?.mediaStoreId.orEmpty(),
            source?.generation?.toString().orEmpty(), source?.mediaKind.orEmpty(),
        ).joinToString("\u0000")
        return MessageDigest.getInstance("SHA-256")
            .digest(stableFields.toByteArray(Charsets.UTF_8))
            .joinToString("") { byte -> "%02x".format(byte) }
    }

    companion object {
        const val SOURCE_MISSING_MESSAGE = "O arquivo não existe mais no aparelho"
    }
}
