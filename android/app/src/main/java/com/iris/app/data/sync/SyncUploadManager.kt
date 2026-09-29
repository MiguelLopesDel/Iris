package com.iris.app.data.sync

import android.content.ContentResolver
import android.net.Uri
import com.iris.app.data.local.UploadDatabaseHelper
import com.iris.app.data.model.LocalUploadJob
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
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicLong

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
    private val mediaPayloadSource = MediaPayloadSource(contentResolver)
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
            mediaPayloadSource.computeSha256(uri)
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
                val transfer = ResumableUploadTransfer(
                    context = ResumableUploadTransfer.Context(
                        accountKey = accountKey,
                        sessionIdentity = sessionIdentity,
                        dbHelper = dbHelper,
                        apiServiceProvider = apiServiceProvider,
                        mediaPayloadSource = mediaPayloadSource,
                        initBatcher = initBatcher,
                        completionBatcher = completionBatcher,
                        completionMutexes = completionMutexes,
                        ensureSession = { ensureSession(isSessionCurrent) },
                        isSessionCurrent = isSessionCurrent,
                    ),
                    observer = ResumableUploadTransfer.Observer(
                        onBytesAcknowledged = { bytes -> acknowledgedBytes.addAndGet(bytes) },
                        beginPayloadRequest = { transferActivity?.beginRequest() ?: {} },
                        onProgress = ::updateProgress,
                    ),
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
                                    val itemConfirmed = transfer.execute(nextJob)
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

    private suspend fun ensureSession(isSessionCurrent: () -> Boolean) {
        currentCoroutineContext().ensureActive()
        if (!isSessionCurrent()) {
            throw CancellationException("Device session changed during media sync")
        }
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

}
