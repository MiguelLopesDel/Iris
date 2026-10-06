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
        /**
         * The item can never succeed as it is (the server rejected it, or its
         * media left the device): it is marked failed and the queue moves on.
         */
        FAILED,
        /** A transient failure: the item resumes from its committed offset on a later pass. */
        RETRY,
    }

    /**
     * Uploads one job. A job whose local media was deleted is failed instead of
     * retried: it can never succeed, and retrying it stopped the whole queue on
     * every run, so one deleted photo halted the backup for good.
     */
    suspend fun execute(queued: LocalUploadJob): ItemResult = withContext(Dispatchers.IO) {
        var readFailed = false
        val job = try {
            withCurrentContent(queued)
        } catch (_: IOException) {
            readFailed = true
            null
        }
        val result = if (job == null) ItemResult.RETRY else transfer(job) { readFailed = true }
        when {
            // A deleted file surfaces as the same IOException as a dropped
            // link; only its absence on the device tells the two apart.
            result == ItemResult.RETRY && readFailed &&
                !context.mediaPayloadSource.isAvailable(Uri.parse(queued.localUri)) -> markSourceMissing(queued)
            else -> result
        }
    }

    /**
     * A job not yet reserved declares its hash and size to the server, and
     * both must describe the file as it is now. An app can rewrite a file
     * after it was hashed, without MediaStore noticing; the upload then sent
     * the old size, the server got different bytes and refused them. A
     * changed size is cheap to see, and the file is hashed again only then.
     */
    private suspend fun withCurrentContent(job: LocalUploadJob): LocalUploadJob {
        // MediaStore removes a photo's location unless ACCESS_MEDIA_LOCATION is
        // granted *when the file is read*, through any URI. Granting or
        // revoking it after hashing changes the bytes the same URI returns, so
        // the declared hash no longer describes what would be sent: hash again,
        // dropping a reservation made with the old hash, before sending bytes
        // the server would refuse.
        if (job.hashedWithLocation != context.mediaPayloadSource.locationAccessGranted()) {
            return refreshContent(job)
        }
        if (!job.uploadId.isNullOrBlank()) return job
        val uri = Uri.parse(job.localUri)
        val size = context.mediaPayloadSource.sizeOf(uri) ?: return job
        if (size == job.byteSize) return job
        return refreshContent(job)
    }

    private suspend fun refreshContent(job: LocalUploadJob): LocalUploadJob {
        val content = context.mediaPayloadSource.computeContent(Uri.parse(job.localUri))
        context.dbHelper.setContent(
            context.accountKey, job.id, content.sha256, content.size, content.original, content.withLocation,
        )
        return job.copy(
            sha256 = content.sha256,
            byteSize = content.size,
            hashedOriginal = content.original,
            hashedWithLocation = content.withLocation,
            uploadId = null,
            nextByteOffset = 0L,
        )
    }

    private suspend fun markSourceMissing(job: LocalUploadJob): ItemResult {
        context.dbHelper.updateJobState(
            context.accountKey, job.id, UploadJobState.FAILED, SOURCE_MISSING_MESSAGE,
        )
        return ItemResult.FAILED
    }

    /**
     * A job marked FAILED here is final and reported as [ItemResult.FAILED], so
     * the caller moves on; reporting it as a retry halted the whole queue for
     * an item that could not succeed anyway.
     */
    private suspend fun transfer(job: LocalUploadJob, onIoFailure: () -> Unit): ItemResult = withContext(Dispatchers.IO) {
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
                    return@withContext if (init.state in setOf("ready", "duplicate", "pending_processing")) {
                        ItemResult.CONFIRMED
                    } else {
                        ItemResult.FAILED
                    }
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
                // The same version the declared hash was computed from.
                val body = context.mediaPayloadSource.createChunkRequestBody(uri, offset, bytesToRead, job.hashedOriginal)
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
                            return@withContext ItemResult.FAILED
                        }
                        val status = api.getUploadStatus(uploadId)
                        context.ensureSession()
                        when (status.state) {
                            "ready" -> { db.updateJobState(accountKey, job.id, UploadJobState.READY); return@withContext ItemResult.CONFIRMED }
                            "duplicate" -> { db.updateJobState(accountKey, job.id, UploadJobState.DUPLICATE); return@withContext ItemResult.CONFIRMED }
                            "pending_processing" -> { db.updateJobState(accountKey, job.id, UploadJobState.PENDING_PROCESSING); return@withContext ItemResult.CONFIRMED }
                            else -> {
                                offset = status.offset
                                db.updateOffsetTransactionally(accountKey, job.id, offset)
                                observer.onProgress(job.id, job.byteSize, offset)
                            }
                        }
                    }
                    response.code() == 401 || isTransientHttpStatus(response.code()) -> return@withContext ItemResult.RETRY
                    response.code() == 404 -> { db.resetUploadProgress(accountKey, job.id); return@withContext ItemResult.RETRY }
                    else -> {
                        val error = response.errorBody()?.string() ?: "Erro HTTP ${response.code()}"
                        db.updateJobState(accountKey, job.id, UploadJobState.FAILED, error)
                        return@withContext ItemResult.FAILED
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
                "failed_processing" -> {
                    db.updateJobState(accountKey, job.id, UploadJobState.FAILED,
                        completion.errorMessage ?: "O servidor não conseguiu processar a mídia enviada")
                    return@withContext ItemResult.FAILED
                }
                else -> db.updateJobState(accountKey, job.id, UploadJobState.READY)
            }
            ItemResult.CONFIRMED
        } catch (failure: UploadCompleteBatchItemException) {
            if (failure.statusCode == 404) {
                db.resetUploadProgress(accountKey, job.id)
                ItemResult.RETRY
            } else if (failure.statusCode == HASH_MISMATCH_STATUS) {
                // The bytes sent are not the ones hashed: the file changed in
                // between. Hash it again and send it from the start.
                val before = job.sha256
                val refreshed = try {
                    refreshContent(job)
                } catch (_: IOException) {
                    onIoFailure()
                    return@withContext ItemResult.RETRY
                }
                if (refreshed.sha256 == before && refreshed.byteSize == job.byteSize) {
                    // Same bytes on a second read: not a changed file. Retrying would loop.
                    db.updateJobState(accountKey, job.id, UploadJobState.FAILED,
                        failure.message ?: "O servidor recebeu bytes diferentes do arquivo")
                    ItemResult.FAILED
                } else {
                    db.updateJobState(accountKey, job.id, UploadJobState.QUEUED)
                    ItemResult.RETRY
                }
            } else if (failure.statusCode != 401 && failure.statusCode != 409 && !isTransientHttpStatus(failure.statusCode)) {
                db.updateJobState(accountKey, job.id, UploadJobState.FAILED,
                    failure.message ?: "Falha ao concluir o envio")
                ItemResult.FAILED
            } else {
                ItemResult.RETRY
            }
        } catch (refused: OriginalRefusedException) {
            // The original this upload was hashed from cannot be read now. Hash
            // the file again (the preferred version is now the redacted one) and
            // start over, rather than send bytes of a version it did not declare.
            try {
                refreshContent(job)
            } catch (_: IOException) {
                onIoFailure()
            }
            ItemResult.RETRY
        } catch (failure: IOException) {
            onIoFailure()
            ItemResult.RETRY
        } catch (cancelled: CancellationException) {
            withContext(NonCancellable) { db.updateJobState(accountKey, job.id, UploadJobState.QUEUED) }
            throw cancelled
        } catch (failure: HttpException) {
            if (!context.isSessionCurrent()) {
                withContext(NonCancellable) { db.updateJobState(accountKey, job.id, UploadJobState.QUEUED) }
                throw CancellationException("Device session changed during media sync")
            }
            if (failure.code() == 404 && remoteUploadStarted) {
                db.resetUploadProgress(accountKey, job.id)
                ItemResult.RETRY
            } else if (failure.code() == 401 || failure.code() == 409 || isTransientHttpStatus(failure.code())) {
                db.updateJobState(accountKey, job.id, UploadJobState.QUEUED)
                ItemResult.RETRY
            } else {
                db.updateJobState(accountKey, job.id, UploadJobState.FAILED,
                    failure.localizedMessage ?: "Erro HTTP ${failure.code()} durante envio")
                ItemResult.FAILED
            }
        } catch (failure: Exception) {
            if (!context.isSessionCurrent()) {
                withContext(NonCancellable) { db.updateJobState(accountKey, job.id, UploadJobState.QUEUED) }
                throw CancellationException("Device session changed during media sync")
            }
            db.updateJobState(accountKey, job.id, UploadJobState.FAILED,
                failure.localizedMessage ?: "Erro desconhecido durante envio")
            ItemResult.FAILED
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
        /**
         * A status that says "not now" rather than "never": a timeout, rate
         * limiting or a server error. Marking those items failed left them out
         * of every later pass, although the next attempt would likely succeed.
         * Other 4xx (400, 413, 415...) reject the item itself and stay final.
         */
        fun isTransientHttpStatus(code: Int): Boolean = code == 408 || code == 429 || code >= 500

        /** The server's answer when the received bytes do not match the declared hash. */
        const val HASH_MISMATCH_STATUS = 422

        const val SOURCE_MISSING_MESSAGE = "O arquivo não existe mais no aparelho"
    }
}
