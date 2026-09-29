package com.iris.app.data.sync

import android.content.ContentResolver
import android.net.Uri
import com.iris.app.data.local.UploadDatabaseHelper
import com.iris.app.data.model.LocalUploadJob
import com.iris.app.data.model.UploadInitRequest
import com.iris.app.data.model.UploadJobState
import com.iris.app.data.model.UploadSource
import com.iris.app.data.remote.IrisApiService
import com.iris.app.performance.Metric
import com.iris.app.performance.PerformanceMonitor
import com.iris.app.performance.UploadTransferActivityTracker
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.joinAll
import kotlinx.coroutines.withContext
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody
import okio.BufferedSink
import java.io.FileInputStream
import java.io.InputStream
import java.security.MessageDigest
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicLong
import kotlin.math.min

class SyncUploadManager(
    private val contentResolver: ContentResolver,
    private val dbHelper: UploadDatabaseHelper,
    private val performanceMonitor: PerformanceMonitor? = null,
    private val maxConcurrentUploads: Int = MAX_CONCURRENT_UPLOADS,
    private val uploadInitMaxBatchSize: Int = UploadInitBatcher.MAX_BATCH_SIZE,
    private val uploadInitCoalesceWindowMillis: Long = UploadInitBatcher.COALESCE_WINDOW_MILLIS,
    private val apiServiceProvider: (String) -> IrisApiService
) {

    init {
        require(maxConcurrentUploads in 1..MAX_SUPPORTED_UPLOADS) {
            "Upload concurrency must be between 1 and $MAX_SUPPORTED_UPLOADS"
        }
    }

    private val uploadMutex = Mutex()
    // Serialize completions for identical content hashes so this process
    // cannot race its own server-side deduplication, while unrelated media
    // can finalize concurrently up to the worker-pool limit.
    private val completionMutexes = Array(COMPLETION_LOCK_STRIPES) { Mutex() }
    private val progressLock = Any()
    private val activeProgress = mutableMapOf<Long, TransferProgress>()

    private data class TransferProgress(val totalBytes: Long, val sentBytes: Long)

    private val _isUploading = MutableStateFlow(false)
    val isUploading: StateFlow<Boolean> = _isUploading.asStateFlow()

    private val _currentProgress = MutableStateFlow(0f)
    val currentProgress: StateFlow<Float> = _currentProgress.asStateFlow()

    suspend fun enqueueMedia(
        accountKey: String,
        uri: Uri,
        filename: String,
        size: Long,
        capturedAtIso: String,
        source: UploadSource? = null,
        isSessionCurrent: () -> Boolean = { true },
    ): Long = withContext(Dispatchers.IO) {
        require(accountKey.isNotBlank()) { "An account key is required to enqueue media" }
        ensureSession(isSessionCurrent)
        val uriStr = uri.toString()
        if (dbHelper.isUriEnqueued(accountKey, uriStr)) {
            return@withContext -1L
        }
        val finishHash = performanceMonitor?.begin(Metric.SyncMediaHash) ?: {}
        val hash = try {
            computeSha256(uri)
        } finally {
            finishHash()
        }
        ensureSession(isSessionCurrent)
        dbHelper.insertOrIgnoreJob(
            accountKey = accountKey,
            localUri = uriStr,
            filename = filename,
            byteSize = size,
            sha256 = hash,
            capturedAt = capturedAtIso,
            source = source
        )
    }

    /** Returns false when another runner owns the queue or an item needs retry. */
    suspend fun processQueue(
        accountKey: String,
        sessionIdentity: String,
        workSignal: SyncQueueCoordinator.WorkSignal? = null,
        isSessionCurrent: () -> Boolean = { true },
        onFirstUploadJobClaimed: () -> Unit = {},
    ): Boolean = withContext(Dispatchers.IO) {
        require(accountKey.isNotBlank()) { "An account key is required to process uploads" }
        require(sessionIdentity.isNotBlank()) { "A session identity is required to process uploads" }
        // Prevent multiple workers or UI buttons from running concurrent upload loops
        if (!uploadMutex.tryLock()) {
            return@withContext false
        }
        _isUploading.value = true
        val queueStartedAtNanos = System.nanoTime()
        val acknowledgedBytes = AtomicLong(0L)
        val confirmedItems = AtomicLong(0L)
        val transferGeneration = performanceMonitor?.activeGeneration()
        val transferActivity = transferGeneration?.let { UploadTransferActivityTracker() }
        val finishQueue = performanceMonitor?.begin(Metric.SyncUploadQueue) ?: {}
        try {
            val retryRequested = AtomicBoolean(false)
            val firstUploadJobClaimed = AtomicBoolean(false)
            val activeJobIds = mutableSetOf<Long>()
            val claimMutex = Mutex()

            coroutineScope {
                val initBatcher = UploadInitBatcher(
                    apiService = apiServiceProvider(sessionIdentity),
                    scope = this,
                    maxBatchSize = uploadInitMaxBatchSize,
                    coalesceWindowMillis = uploadInitCoalesceWindowMillis,
                )
                val completionBatcher = UploadCompleteBatcher(
                    apiService = apiServiceProvider(sessionIdentity),
                    scope = this,
                )
                try {
                    val workers = List(maxConcurrentUploads) {
                        launch(Dispatchers.IO) {
                            while (!retryRequested.get()) {
                                ensureSession(isSessionCurrent)
                                // Snapshot before claiming. If discovery signals a
                                // new durable job between an empty claim and await,
                                // the revision check below makes this worker retry
                                // instead of missing the wake-up.
                                val observedQueueRevision = workSignal?.snapshot?.revision ?: -1L
                                val nextJob = claimMutex.withLock {
                                    if (retryRequested.get()) {
                                        null
                                    } else {
                                        dbHelper.claimNextPendingJob(accountKey, activeJobIds)?.also { job ->
                                            activeJobIds += job.id
                                            updateProgress(job.id, job.byteSize, job.nextByteOffset)
                                        }
                                    }
                                }
                                if (nextJob == null) {
                                    val signal = workSignal ?: break
                                    val currentSignal = signal.snapshot
                                    if (currentSignal.revision != observedQueueRevision) {
                                        continue
                                    }
                                    if (currentSignal.scanFinished || currentSignal.workersStopped) {
                                        break
                                    }
                                    signal.awaitChange(observedQueueRevision)
                                    continue
                                }

                                if (firstUploadJobClaimed.compareAndSet(false, true)) {
                                    onFirstUploadJobClaimed()
                                }

                                try {
                                    val itemConfirmed = executeUpload(
                                        accountKey,
                                        sessionIdentity,
                                        nextJob,
                                        isSessionCurrent,
                                        initBatcher,
                                        completionBatcher,
                                        onBytesAcknowledged = { bytes -> acknowledgedBytes.addAndGet(bytes) },
                                        beginPayloadRequest = { transferActivity?.beginRequest() ?: {} },
                                    )
                                    if (itemConfirmed) {
                                        confirmedItems.incrementAndGet()
                                    } else {
                                        // Stop claiming more work on any transient
                                        // failure. Other already-active jobs may
                                        // finish, then WorkManager retries the
                                        // durable queue from committed offsets.
                                        retryRequested.set(true)
                                        workSignal?.stopWorkers()
                                    }
                                } finally {
                                    claimMutex.withLock {
                                        activeJobIds.remove(nextJob.id)
                                        removeProgress(nextJob.id)
                                    }
                                }
                            }
                        }
                    }
                    // Keep the batch channel open until every worker has
                    // completed its final per-item reservation await. Closing
                    // it immediately after launch races the workers and turns
                    // valid jobs into "Channel was closed" failures.
                    workers.joinAll()
                } finally {
                    initBatcher.close()
                    completionBatcher.close()
                }
            }
            !retryRequested.get()
        } finally {
            _isUploading.value = false
            synchronized(progressLock) {
                activeProgress.clear()
                _currentProgress.value = 0f
            }
            finishQueue()
            val queueElapsedMillis = (System.nanoTime() - queueStartedAtNanos) / 1_000_000.0
            if (transferGeneration != null) {
                val activeElapsedMillis = transferActivity?.finishAndGetActiveMillis() ?: 0.0
                performanceMonitor?.recordTransferForGeneration(
                    generation = transferGeneration,
                    bytes = acknowledgedBytes.get(),
                    elapsedMillis = queueElapsedMillis,
                    activeElapsedMillis = activeElapsedMillis,
                    confirmedItems = confirmedItems.get(),
                )
            }
            uploadMutex.unlock()
        }
    }

    private suspend fun executeUpload(
        accountKey: String,
        sessionIdentity: String,
        job: LocalUploadJob,
        isSessionCurrent: () -> Boolean,
        initBatcher: UploadInitBatcher,
        completionBatcher: UploadCompleteBatcher,
        onBytesAcknowledged: (Long) -> Unit,
        beginPayloadRequest: () -> (() -> Unit),
    ): Boolean = withContext(Dispatchers.IO) {
        val apiService = apiServiceProvider(sessionIdentity)
        var uploadId = job.uploadId
        var currentOffset = job.nextByteOffset
        var chunkSize = job.chunkSize
        var remoteUploadStarted = !uploadId.isNullOrBlank()

        try {
            ensureSession(isSessionCurrent)
            // Step 1: Initiate upload if not yet started
            if (uploadId.isNullOrBlank()) {
                val initResponse = initBatcher.initialize(
                    clientUploadId = clientUploadId(job),
                    request = UploadInitRequest(
                        filename = job.filename,
                        size = job.byteSize,
                        sha256 = job.sha256,
                        capturedAt = job.capturedAt,
                        source = job.source
                    )
                )
                ensureSession(isSessionCurrent)
                if (initResponse.state != "uploading") {
                    when (initResponse.state) {
                        "ready" -> dbHelper.updateJobState(accountKey, job.id, UploadJobState.READY)
                        "duplicate" -> dbHelper.updateJobState(accountKey, job.id, UploadJobState.DUPLICATE)
                        "pending_processing" -> dbHelper.updateJobState(accountKey, job.id, UploadJobState.PENDING_PROCESSING)
                        else -> dbHelper.updateJobState(
                            accountKey,
                            job.id,
                            UploadJobState.FAILED,
                            "O servidor informa que este envio está em estado ${initResponse.state}",
                        )
                    }
                    return@withContext initResponse.state in setOf("ready", "duplicate", "pending_processing")
                }
                uploadId = initResponse.uploadId
                currentOffset = initResponse.offset
                chunkSize = initResponse.chunkSize
                remoteUploadStarted = true
                dbHelper.updateUploadStarted(
                    accountKey = accountKey,
                    id = job.id,
                    uploadId = uploadId,
                    offset = currentOffset,
                    chunkSize = initResponse.chunkSize
                )
            }

            // Step 2: Sequential chunk upload loop
            val uri = Uri.parse(job.localUri)
            var conflictCount = 0

            while (currentOffset < job.byteSize) {
                ensureSession(isSessionCurrent)
                val remaining = job.byteSize - currentOffset
                val bytesToRead = min(remaining, chunkSize.toLong())
                val requestBody = createChunkRequestBody(uri, currentOffset, bytesToRead)

                val finishPayloadRequest = beginPayloadRequest()
                val response = try {
                    apiService.uploadChunk(
                        uploadId = uploadId,
                        offset = currentOffset,
                        body = requestBody
                    )
                } finally {
                    finishPayloadRequest()
                }
                ensureSession(isSessionCurrent)

                if (response.isSuccessful) {
                    val nextOffset = response.body()?.offset ?: (currentOffset + bytesToRead)
                    val acknowledged = (nextOffset - currentOffset).coerceIn(0L, bytesToRead)
                    currentOffset = nextOffset
                    conflictCount = 0
                    dbHelper.updateOffsetTransactionally(accountKey, job.id, currentOffset)
                    onBytesAcknowledged(acknowledged)
                    updateProgress(job.id, job.byteSize, currentOffset)
                } else if (response.code() == 409) {
                    conflictCount++
                    if (conflictCount > 3) {
                        dbHelper.updateJobState(accountKey, job.id, UploadJobState.FAILED, "Conflito persistente de offset no upload (409)")
                        return@withContext false
                    }
                    // Offset mismatch! Query server confirmed position
                    val status = apiService.getUploadStatus(uploadId)
                    ensureSession(isSessionCurrent)
                    when (status.state) {
                        "ready" -> {
                            dbHelper.updateJobState(accountKey, job.id, UploadJobState.READY)
                            return@withContext true
                        }
                        "duplicate" -> {
                            dbHelper.updateJobState(accountKey, job.id, UploadJobState.DUPLICATE)
                            return@withContext true
                        }
                        "pending_processing" -> {
                            dbHelper.updateJobState(accountKey, job.id, UploadJobState.PENDING_PROCESSING)
                            return@withContext true
                        }
                        else -> {
                            currentOffset = status.offset
                            dbHelper.updateOffsetTransactionally(accountKey, job.id, currentOffset)
                            updateProgress(job.id, job.byteSize, currentOffset)
                        }
                    }
                } else if (response.code() == 401) {
                    // Session might be refreshing; do not permanently fail, let WorkManager retry
                    return@withContext false
                } else if (response.code() == 404) {
                    // Upload IDs are bound to the server device session. If the
                    // same account logs in again on a new device session, start
                    // a fresh resumable upload instead of failing this job.
                    dbHelper.resetUploadProgress(accountKey, job.id)
                    return@withContext false
                } else {
                    val err = response.errorBody()?.string() ?: "Erro HTTP ${response.code()}"
                    dbHelper.updateJobState(accountKey, job.id, UploadJobState.FAILED, err)
                    return@withContext false
                }
            }

            // Step 3: Complete upload and verify full SHA-256
            val completionMutex = completionMutexes[
                job.sha256.hashCode().ushr(1) % completionMutexes.size
            ]
            val completion = completionMutex.withLock {
                ensureSession(isSessionCurrent)
                completionBatcher.complete(uploadId)
            }
            ensureSession(isSessionCurrent)
            when (completion.state) {
                "pending_processing", "processing" -> {
                    // Backup succeeded; indexing pending. Do not mark failed or re-upload.
                    dbHelper.updateJobState(accountKey, job.id, UploadJobState.PENDING_PROCESSING)
                }
                "duplicate" -> {
                    // Original already present in private library
                    dbHelper.updateJobState(accountKey, job.id, UploadJobState.DUPLICATE)
                }
                "failed_processing" -> {
                    dbHelper.updateJobState(
                        accountKey,
                        job.id,
                        UploadJobState.FAILED,
                        completion.errorMessage ?: "O servidor não conseguiu processar a mídia enviada",
                    )
                }
                else -> {
                    dbHelper.updateJobState(accountKey, job.id, UploadJobState.READY)
                }
            }
            true
        } catch (completionFailure: UploadCompleteBatchItemException) {
            if (completionFailure.statusCode == 404) {
                // Like the single-item completion route, a missing/device-scoped
                // upload is resumable only by starting a fresh reservation.
                dbHelper.resetUploadProgress(accountKey, job.id)
                false
            } else if (completionFailure.statusCode == 401 || completionFailure.statusCode == 409 ||
                completionFailure.statusCode >= 500
            ) {
                false
            } else {
                dbHelper.updateJobState(
                    accountKey,
                    job.id,
                    UploadJobState.FAILED,
                    completionFailure.message ?: "Falha ao concluir o envio",
                )
                false
            }
        } catch (e: java.io.IOException) {
            // Transient network failure: keep job in UPLOADING state so WorkManager can retry cleanly
            false
        } catch (cancelled: CancellationException) {
            // Logout / WorkManager cancellation must not turn a resumable upload into a failure.
            withContext(NonCancellable) {
                dbHelper.updateJobState(accountKey, job.id, UploadJobState.QUEUED)
            }
            throw cancelled
        } catch (unauthorized: retrofit2.HttpException) {
            if (!isSessionCurrent()) {
                withContext(NonCancellable) {
                    dbHelper.updateJobState(accountKey, job.id, UploadJobState.QUEUED)
                }
                throw CancellationException("Device session changed during media sync")
            }
            if (unauthorized.code() == 404 && remoteUploadStarted) {
                dbHelper.resetUploadProgress(accountKey, job.id)
                return@withContext false
            }
            if (unauthorized.code() == 401 || unauthorized.code() == 409) {
                dbHelper.updateJobState(accountKey, job.id, UploadJobState.QUEUED)
                false
            } else {
                dbHelper.updateJobState(
                    accountKey,
                    job.id,
                    UploadJobState.FAILED,
                    unauthorized.localizedMessage ?: "Erro HTTP ${unauthorized.code()} durante envio"
                )
                false
            }
        } catch (e: Exception) {
            if (!isSessionCurrent()) {
                withContext(NonCancellable) {
                    dbHelper.updateJobState(accountKey, job.id, UploadJobState.QUEUED)
                }
                throw CancellationException("Device session changed during media sync")
            }
            dbHelper.updateJobState(
                accountKey,
                job.id,
                UploadJobState.FAILED,
                e.localizedMessage ?: "Erro desconhecido durante envio"
            )
            false
        }
    }

    private suspend fun ensureSession(isSessionCurrent: () -> Boolean) {
        currentCoroutineContext().ensureActive()
        if (!isSessionCurrent()) {
            throw CancellationException("Device session changed during media sync")
        }
    }

    private fun clientUploadId(job: LocalUploadJob): String {
        val source = job.source
        val stableFields = listOf(
            job.id.toString(),
            job.filename,
            job.byteSize.toString(),
            job.sha256,
            job.capturedAt,
            source?.id.orEmpty(),
            source?.name.orEmpty(),
            source?.relativePath.orEmpty(),
            source?.volume.orEmpty(),
            source?.mediaStoreId.orEmpty(),
            source?.generation?.toString().orEmpty(),
            source?.mediaKind.orEmpty(),
        ).joinToString("\u0000")
        return MessageDigest.getInstance("SHA-256")
            .digest(stableFields.toByteArray(Charsets.UTF_8))
            .joinToString("") { byte -> "%02x".format(byte) }
    }

    private fun updateProgress(jobId: Long, totalBytes: Long, sentBytes: Long) {
        synchronized(progressLock) {
            activeProgress[jobId] = TransferProgress(totalBytes, sentBytes)
            val total = activeProgress.values.sumOf { it.totalBytes }
            val sent = activeProgress.values.sumOf { it.sentBytes }
            _currentProgress.value = if (total > 0L) (sent.toDouble() / total).toFloat() else 0f
        }
    }

    private fun removeProgress(jobId: Long) {
        synchronized(progressLock) {
            activeProgress.remove(jobId)
            val total = activeProgress.values.sumOf { it.totalBytes }
            val sent = activeProgress.values.sumOf { it.sentBytes }
            _currentProgress.value = if (total > 0L) (sent.toDouble() / total).toFloat() else 0f
        }
    }

    internal companion object {
        const val MAX_CONCURRENT_UPLOADS = 16
        const val MAX_SUPPORTED_UPLOADS = 16
        const val COMPLETION_LOCK_STRIPES = 16
    }

    /**
     * Streaming RequestBody with O(1) kernel seek via ParcelFileDescriptor/FileChannel.
     * Uses a lightweight 64 KB buffer, eliminating 32 MB JVM heap allocations and OOM risk.
     * isOneShot() = false allows OkHttp to retry cleanly upon 401 token refresh.
     */
    private fun createChunkRequestBody(
        uri: Uri,
        offset: Long,
        length: Long
    ): RequestBody = object : RequestBody() {
        override fun contentType() = "application/octet-stream".toMediaType()
        override fun contentLength(): Long = length
        override fun isOneShot(): Boolean = false

        override fun writeTo(sink: BufferedSink) {
            val pfd = try {
                contentResolver.openFileDescriptor(uri, "r")
            } catch (_: Exception) {
                null
            }

            if (pfd != null) {
                pfd.use { fd ->
                    FileInputStream(fd.fileDescriptor).use { stream ->
                        stream.channel.position(offset)
                        val buffer = ByteArray(64 * 1024)
                        var bytesRemaining = length
                        while (bytesRemaining > 0) {
                            val toRead = min(bytesRemaining, buffer.size.toLong()).toInt()
                            val read = stream.read(buffer, 0, toRead)
                            if (read == -1) break
                            sink.write(buffer, 0, read)
                            bytesRemaining -= read
                        }
                    }
                }
            } else {
                contentResolver.openInputStream(uri)?.use { stream ->
                    skipFully(stream, offset)
                    val buffer = ByteArray(64 * 1024)
                    var bytesRemaining = length
                    while (bytesRemaining > 0) {
                        val toRead = min(bytesRemaining, buffer.size.toLong()).toInt()
                        val read = stream.read(buffer, 0, toRead)
                        if (read == -1) break
                        sink.write(buffer, 0, read)
                        bytesRemaining -= read
                    }
                } ?: throw java.io.IOException("Não foi possível abrir o arquivo da mídia local")
            }
        }
    }

    private fun skipFully(stream: InputStream, bytesToSkip: Long) {
        var remaining = bytesToSkip
        while (remaining > 0) {
            val skipped = stream.skip(remaining)
            if (skipped <= 0) {
                if (stream.read() == -1) break
                remaining -= 1
            } else {
                remaining -= skipped
            }
        }
    }

    fun computeSha256(uri: Uri): String {
        val digest = MessageDigest.getInstance("SHA-256")
        val pfd = try {
            contentResolver.openFileDescriptor(uri, "r")
        } catch (_: Exception) {
            null
        }

        if (pfd != null) {
            pfd.use { fd ->
                FileInputStream(fd.fileDescriptor).use { stream ->
                    val buffer = ByteArray(64 * 1024)
                    var count: Int
                    while (stream.read(buffer).also { count = it } != -1) {
                        digest.update(buffer, 0, count)
                    }
                }
            }
        } else {
            contentResolver.openInputStream(uri)?.use { stream ->
                val buffer = ByteArray(64 * 1024)
                var count: Int
                while (stream.read(buffer).also { count = it } != -1) {
                    digest.update(buffer, 0, count)
                }
            } ?: return ""
        }
        val bytes = digest.digest()
        return bytes.joinToString("") { "%02x".format(it) }
    }
}
