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
    /** Always-on throughput of the current or last queue run, for the sync screen and history. */
    val speedMeter: UploadSpeedMeter = UploadSpeedMeter(),
    /** Whether photos may be read with their location metadata; see [MediaLocationAccess]. */
    canReadOriginals: () -> Boolean = { false },
    private val apiServiceProvider: (String) -> IrisApiService
) {

    init {
        require(maxConcurrentUploads in 1..MAX_SUPPORTED_UPLOADS) {
            "Upload concurrency must be between 1 and $MAX_SUPPORTED_UPLOADS"
        }
    }

    private val uploadMutex = Mutex()
    private val mediaPayloadSource = MediaPayloadSource(contentResolver, canReadOriginals)
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
        val content = hash(uri)
        ensureSession(isSessionCurrent)
        dbHelper.insertOrIgnoreJob(
            accountKey = accountKey,
            localUri = uriStr,
            filename = filename,
            byteSize = content.size,
            sourceSize = size,
            hashedOriginal = content.original,
            sha256 = content.sha256,
            capturedAt = capturedAtIso,
            source = source
        )
    }

    /** What reconciling one scanned item with the queue did. */
    enum class ScanOutcome {
        /** Not queued before: hashed and queued. */
        QUEUED_NEW,
        /** Its bytes changed since they were hashed: the new content is queued. */
        QUEUED_NEW_VERSION,
        /** Nothing to upload: known with the same content. */
        UNCHANGED,
        /** Changed, but its row is being sent right now; the next scan picks it up. */
        DEFERRED,
    }

    /**
     * Brings the queue in line with one item the scanner found, hashing only
     * when [MediaChangePolicy] says the cheap fingerprint is not enough.
     */
    suspend fun reconcileMedia(
        accountKey: String,
        uri: Uri,
        filename: String,
        capturedAtIso: String,
        source: UploadSource?,
        fingerprint: MediaFingerprint,
        fullVerificationStartedAt: Long?,
        isSessionCurrent: () -> Boolean = { true },
        now: () -> Long = System::currentTimeMillis,
    ): ScanOutcome = withContext(Dispatchers.IO) {
        require(accountKey.isNotBlank()) { "An account key is required to enqueue media" }
        ensureSession(isSessionCurrent)
        val uriStr = uri.toString()
        val known = dbHelper.knownMedia(accountKey, uriStr)
        when (MediaChangePolicy.decide(known, fingerprint, fullVerificationStartedAt)) {
            MediaChangePolicy.Verdict.UNCHANGED -> ScanOutcome.UNCHANGED
            MediaChangePolicy.Verdict.ADOPT_FINGERPRINT -> {
                dbHelper.recordVerifiedFingerprint(accountKey, known!!.jobId, fingerprint, filename, source, verifiedAt = null)
                ScanOutcome.UNCHANGED
            }
            MediaChangePolicy.Verdict.NEW -> {
                val content = hash(uri)
                ensureSession(isSessionCurrent)
                val id = dbHelper.insertOrIgnoreJob(
                    accountKey = accountKey,
                    localUri = uriStr,
                    filename = filename,
                    byteSize = content.size,
                    sourceSize = fingerprint.size,
                    hashedOriginal = content.original,
                    sha256 = content.sha256,
                    capturedAt = capturedAtIso,
                    source = source,
                    dateModifiedSeconds = fingerprint.dateModifiedSeconds,
                    verifiedAt = now(),
                )
                if (id > 0L) ScanOutcome.QUEUED_NEW else ScanOutcome.UNCHANGED
            }
            MediaChangePolicy.Verdict.VERIFY -> {
                val content = hash(uri)
                ensureSession(isSessionCurrent)
                when {
                    // Renamed, moved or touched: same bytes, nothing to send.
                    content.sha256.equals(known!!.sha256, ignoreCase = true) -> {
                        dbHelper.recordVerifiedFingerprint(accountKey, known.jobId, fingerprint, filename, source, now())
                        ScanOutcome.UNCHANGED
                    }
                    dbHelper.replaceWithNewVersion(
                        accountKey, known.jobId, content.sha256, content.size, fingerprint, filename,
                        capturedAtIso, source, now(), hashedOriginal = content.original,
                    ) -> ScanOutcome.QUEUED_NEW_VERSION
                    else -> ScanOutcome.DEFERRED
                }
            }
        }
    }

    /**
     * Starts this account's scan: returns when the running full verification
     * began, or null when none runs. One starts when MediaStore was rebuilt on
     * a known volume (its ids may now name other items), or, when [allowPeriodic],
     * once a week to catch changes MediaStore never reported.
     */
    suspend fun beginScan(
        accountKey: String,
        mediaStoreVersions: Map<String, String>,
        allowPeriodic: Boolean,
        now: Long = System.currentTimeMillis(),
    ): Long? {
        val state = dbHelper.scanState(accountKey)
        state.fullVerificationStartedAt?.let { return it }
        val due = when {
            MediaChangePolicy.mediaStoreRebuilt(state.mediaStoreVersions, mediaStoreVersions) -> true
            !allowPeriodic -> false
            else -> state.lastFullVerificationAt?.let {
                now - it >= MediaChangePolicy.FULL_VERIFICATION_INTERVAL_MILLIS
            } ?: false
        }
        if (!due) return null
        dbHelper.saveScanState(accountKey, state.copy(fullVerificationStartedAt = now))
        return now
    }

    /**
     * Records a scan that went through every item: versions seen, and a
     * finished verification. With [verificationComplete] false (a changed item
     * could not be requeued because it was being sent), a running
     * verification stays open: the next scan checks again what this one
     * could not settle.
     */
    suspend fun finishScan(
        accountKey: String,
        mediaStoreVersions: Map<String, String>,
        fullVerificationStartedAt: Long?,
        now: Long = System.currentTimeMillis(),
        verificationComplete: Boolean = true,
    ) {
        val state = dbHelper.scanState(accountKey)
        dbHelper.saveScanState(
            accountKey,
            state.copy(
                // A card that is not mounted now keeps its last known version.
                mediaStoreVersions = state.mediaStoreVersions + mediaStoreVersions,
                fullVerificationStartedAt = if (verificationComplete) null else fullVerificationStartedAt,
                // The first scan is the baseline the weekly verification counts from.
                lastFullVerificationAt = when {
                    fullVerificationStartedAt == null -> state.lastFullVerificationAt ?: now
                    verificationComplete -> now
                    else -> state.lastFullVerificationAt
                },
            ),
        )
    }

    /**
     * Ties this account's queue to the server installation it describes.
     * When [instanceId] differs from the one recorded, the queue's states
     * belong to another installation and are requeued. With none recorded
     * (a queue from before this check) they are requeued too: it cannot tell
     * which installation they describe, and requeuing costs only reservations,
     * which the server answers "duplicate" for what it has. A new account has
     * nothing to requeue. A server too old to report an id is left alone.
     * Returns how many items were requeued.
     */
    suspend fun bindServerInstance(accountKey: String, instanceId: String?): Int {
        if (instanceId.isNullOrBlank()) return 0
        val state = dbHelper.scanState(accountKey)
        if (state.serverInstanceId == instanceId) return 0
        val requeued = dbHelper.requeueForNewServer(accountKey)
        dbHelper.saveScanState(accountKey, state.copy(serverInstanceId = instanceId))
        return requeued
    }

    private suspend fun hash(uri: Uri): MediaPayloadSource.Content {
        val finishHash = performanceMonitor?.begin(Metric.SyncMediaHash) ?: {}
        return try {
            mediaPayloadSource.computeContent(uri)
        } finally {
            finishHash()
        }
    }

    /**
     * Returns false when an item needs retry. One item's transient failure does
     * not stop the pass: that item is set aside until the next pass and the rest
     * keep going, so a single slow video no longer halts thousands of photos the
     * server could take (or already has). Only a run of consecutive transient
     * failures, which points at the link or the server, stops claiming work.
     * Items the server rejected for good are failed and never stop the queue.
     *
     * A second runner waits for the one
     * that owns the queue instead of returning: items its own scan enqueued may
     * arrive after the first runner stopped claiming, and leaving them would
     * strand them until the next scheduled sync.
     *
     * [onQueueRunStarted] receives the speed meter's number for this pass and
     * [onQueueRunFinished] its final totals, so a caller credits only its own pass.
     */
    suspend fun processQueue(
        accountKey: String,
        sessionIdentity: String,
        workSignal: SyncQueueCoordinator.WorkSignal? = null,
        isSessionCurrent: () -> Boolean = { true },
        onFirstUploadJobClaimed: () -> Unit = {},
        onQueueRunStarted: (Long) -> Unit = {},
        onQueueRunFinished: (UploadSpeedSnapshot) -> Unit = {},
    ): Boolean = withContext(Dispatchers.IO) {
        require(accountKey.isNotBlank()) { "An account key is required to process uploads" }
        require(sessionIdentity.isNotBlank()) { "A session identity is required to process uploads" }
        // One upload loop at a time; a concurrent worker waits its turn.
        uploadMutex.lock()
        _isUploading.value = true
        onQueueRunStarted(speedMeter.startRun())
        val queueStartedAtNanos = System.nanoTime()
        val acknowledgedBytes = AtomicLong(0L)
        val confirmedItems = AtomicLong(0L)
        val transferGeneration = performanceMonitor?.activeGeneration()
        val transferActivity = transferGeneration?.let { UploadTransferActivityTracker() }
        val finishQueue = performanceMonitor?.begin(Metric.SyncUploadQueue) ?: {}
        try {
            val retryRequested = AtomicBoolean(false)
            val firstUploadJobClaimed = AtomicBoolean(false)
            // The pass claims in id order: jobs being sent, and those that failed
            // transiently and wait for the next pass, all sit at or below this id.
            var lastClaimedId = 0L
            val anyDeferred = AtomicBoolean(false)
            val transientFailures = TransientFailureStreak(MAX_CONSECUTIVE_TRANSIENT_FAILURES)
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
                        onBytesAcknowledged = { bytes ->
                            acknowledgedBytes.addAndGet(bytes)
                            speedMeter.recordAcknowledged(bytes)
                        },
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
                                        dbHelper.claimNextPendingJob(accountKey, lastClaimedId)?.also { job ->
                                            lastClaimedId = job.id
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
                                    when (transfer.execute(nextJob)) {
                                        ResumableUploadTransfer.ItemResult.CONFIRMED -> {
                                            transientFailures.reset()
                                            confirmedItems.incrementAndGet()
                                            speedMeter.recordConfirmedItem()
                                        }
                                        ResumableUploadTransfer.ItemResult.FAILED -> Unit
                                        ResumableUploadTransfer.ItemResult.RETRY -> {
                                            anyDeferred.set(true)
                                            // Back in the queue, not shown as sending; it
                                            // resumes from its committed offset.
                                            dbHelper.updateJobState(accountKey, nextJob.id, UploadJobState.QUEUED)
                                            if (transientFailures.recordFailure()) {
                                                // Other already-active jobs may finish, then
                                                // WorkManager retries from committed offsets.
                                                retryRequested.set(true)
                                                workSignal?.stopWorkers()
                                            }
                                        }
                                    }
                                } finally {
                                    claimMutex.withLock {
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
            !retryRequested.get() && !anyDeferred.get()
        } finally {
            speedMeter.finishRun()
            onQueueRunFinished(speedMeter.snapshot())
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
        const val MAX_CONSECUTIVE_TRANSIENT_FAILURES = 3
        const val MAX_SUPPORTED_UPLOADS = 16
        const val COMPLETION_LOCK_STRIPES = 16
    }

}
