package com.iris.app.data.sync

import android.content.ContentResolver
import android.content.ContentUris
import android.net.Uri
import android.os.Build
import android.provider.MediaStore
import com.iris.app.data.model.DeviceMediaSource
import com.iris.app.data.model.MediaScanPolicy
import com.iris.app.data.model.UploadSource
import com.iris.app.performance.Metric
import com.iris.app.performance.PerformanceMonitor
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.joinAll
import kotlinx.coroutines.launch
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.withContext
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone

class MediaStoreScanner(
    private val contentResolver: ContentResolver,
    private val uploadManager: SyncUploadManager,
    private val performanceMonitor: PerformanceMonitor? = null,
    /** MediaStore's version per volume ([MediaStoreVersions.read]); empty where the platform has none. */
    private val mediaStoreVersions: () -> Map<String, String> = { emptyMap() },
) {

    /** How far a scan is: media items looked at, out of those the account's folders include. */
    data class ScanProgress(val examined: Int, val total: Int)

    private val _scanProgress = MutableStateFlow<ScanProgress?>(null)

    /**
     * The running scan's progress, or null when none runs. Hashing thousands of
     * new items takes minutes, and without this the screen only showed the
     * queue growing with nothing being sent.
     */
    val scanProgress: StateFlow<ScanProgress?> = _scanProgress.asStateFlow()

    private val isoFormat = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss'Z'", Locale.US).apply {
        timeZone = TimeZone.getTimeZone("UTC")
    }

    suspend fun discoverSources(): List<DeviceMediaSource> = withContext(Dispatchers.IO) {
        val sources = linkedMapOf<String, DeviceMediaSource>()
        discoverCollection(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, "image", sources)
        discoverCollection(MediaStore.Video.Media.EXTERNAL_CONTENT_URI, "video", sources)
        // Biggest first. Alphabetical order buried Camera and Screenshots under
        // hundreds of folders named after whatever a downloaded archive happened
        // to contain -- a real device here listed 273 sources, most of them
        // "06", "07", "72x72" from extracted website backups.
        sources.values.sortedWith(
            compareByDescending<DeviceMediaSource> { it.itemCount }
                .thenBy { it.name.lowercase(Locale.getDefault()) }
                .thenBy { it.mediaKind }
        )
    }

    suspend fun scanAndEnqueueNewMedia(
        accountKey: String,
        policy: MediaScanPolicy = MediaScanPolicy(),
        isSessionCurrent: () -> Boolean = { true },
        onNewJobEnqueued: suspend () -> Unit = {},
        /** Whether this run may start the weekly full verification (it hashes everything). */
        allowFullVerification: Boolean = false,
    ): Int = withContext(Dispatchers.IO) {
        val finishScan = performanceMonitor?.begin(Metric.SyncMediaScan) ?: {}
        try {
            require(accountKey.isNotBlank()) { "An account key is required to scan media for upload" }
            ensureSession(isSessionCurrent)
            val total = (if (policy.includeImages) countIncluded(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, "image", policy) else 0) +
                (if (policy.includeVideos) countIncluded(MediaStore.Video.Media.EXTERNAL_CONTENT_URI, "video", policy) else 0)
            val progress = ProgressCounter(total)
            val versions = mediaStoreVersions()
            val verificationStartedAt = uploadManager.beginScan(accountKey, versions, allowFullVerification)
            val deferred = java.util.concurrent.atomic.AtomicInteger(0)
            var count = 0
            if (policy.includeImages) {
                count += scanCollection(
                    accountKey,
                    MediaStore.Images.Media.EXTERNAL_CONTENT_URI,
                    "image",
                    policy,
                    onNewJobEnqueued,
                    isSessionCurrent,
                    progress,
                    verificationStartedAt,
                    deferred,
                )
            }
            if (policy.includeVideos) {
                count += scanCollection(
                    accountKey,
                    MediaStore.Video.Media.EXTERNAL_CONTENT_URI,
                    "video",
                    policy,
                    onNewJobEnqueued,
                    isSessionCurrent,
                    progress,
                    verificationStartedAt,
                    deferred,
                )
            }
            // Reached only when every item was examined: an interrupted scan
            // leaves a running verification to resume next time.
            // A changed item that was being sent could not be requeued: keep
            // the verification open so the next scan checks it again. Once
            // closed, ordinary scans trust its unchanged fingerprint and skip it.
            uploadManager.finishScan(accountKey, versions, verificationStartedAt, verificationComplete = deferred.get() == 0)
            count
        } finally {
            _scanProgress.value = null
            finishScan()
        }
    }

    /** Publishes progress every few items: one update per item only adds UI churn. */
    private inner class ProgressCounter(private val total: Int) {
        // Advanced by several hashing workers at once.
        private val examined = java.util.concurrent.atomic.AtomicInteger(0)

        init {
            _scanProgress.value = ScanProgress(0, total)
        }

        fun advance() {
            val now = examined.incrementAndGet()
            if (now % PROGRESS_STEP == 0 || now == total) {
                // Workers finish out of order: never move the shown count back.
                _scanProgress.update { current ->
                    if (current == null || current.examined < now) ScanProgress(now, total) else current
                }
            }
        }
    }

    private fun countIncluded(collectionUri: Uri, mediaKind: String, policy: MediaScanPolicy): Int {
        val columns = buildList {
            add(MediaStore.MediaColumns.BUCKET_ID)
            if (Build.VERSION.SDK_INT >= 29) add(MediaStore.MediaColumns.VOLUME_NAME)
        }.toTypedArray()
        return contentResolver.query(collectionUri, columns, "${MediaStore.MediaColumns.SIZE} > 0", null, null)?.use { cursor ->
            val bucketIdColumn = cursor.getColumnIndex(MediaStore.MediaColumns.BUCKET_ID)
            val volumeColumn = cursor.getColumnIndex(MediaStore.MediaColumns.VOLUME_NAME)
            var included = 0
            while (cursor.moveToNext()) {
                val sourceId = sourceId(
                    cursor.stringOrEmpty(volumeColumn, "external"),
                    cursor.stringOrEmpty(bucketIdColumn, "unknown"),
                    mediaKind,
                )
                if (policy.includes(sourceId, mediaKind)) included++
            }
            included
        } ?: 0
    }

    private fun projection(): Array<String> = buildList {
        add(MediaStore.MediaColumns._ID)
        add(MediaStore.MediaColumns.DISPLAY_NAME)
        add(MediaStore.MediaColumns.SIZE)
        add(MediaStore.MediaColumns.DATE_ADDED)
        add(MediaStore.MediaColumns.DATE_MODIFIED)
        add(MediaStore.MediaColumns.DATE_TAKEN)
        add(MediaStore.MediaColumns.BUCKET_ID)
        add(MediaStore.MediaColumns.BUCKET_DISPLAY_NAME)
        if (Build.VERSION.SDK_INT >= 29) {
            add(MediaStore.MediaColumns.RELATIVE_PATH)
            add(MediaStore.MediaColumns.VOLUME_NAME)
        }
        if (Build.VERSION.SDK_INT >= 30) add(MediaStore.MediaColumns.GENERATION_MODIFIED)
    }.toTypedArray()

    private fun discoverCollection(
        collectionUri: Uri,
        mediaKind: String,
        target: MutableMap<String, DeviceMediaSource>
    ) {
        contentResolver.query(collectionUri, projection(), "${MediaStore.MediaColumns.SIZE} > 0", null, null)?.use { cursor ->
            val bucketIdColumn = cursor.getColumnIndex(MediaStore.MediaColumns.BUCKET_ID)
            val bucketNameColumn = cursor.getColumnIndex(MediaStore.MediaColumns.BUCKET_DISPLAY_NAME)
            val relativePathColumn = cursor.getColumnIndex(MediaStore.MediaColumns.RELATIVE_PATH)
            val volumeColumn = cursor.getColumnIndex(MediaStore.MediaColumns.VOLUME_NAME)
            while (cursor.moveToNext()) {
                val volume = cursor.stringOrEmpty(volumeColumn, "external")
                val bucketId = cursor.stringOrEmpty(bucketIdColumn, "unknown")
                val sourceId = sourceId(volume, bucketId, mediaKind)
                val previous = target[sourceId]
                target[sourceId] = DeviceMediaSource(
                    id = sourceId,
                    name = cursor.stringOrEmpty(bucketNameColumn, "Unknown source"),
                    relativePath = cursor.stringOrEmpty(relativePathColumn, ""),
                    volume = volume,
                    mediaKind = mediaKind,
                    itemCount = (previous?.itemCount ?: 0) + 1
                )
            }
        }
    }

    private suspend fun scanCollection(
        accountKey: String,
        collectionUri: Uri,
        mediaKind: String,
        policy: MediaScanPolicy,
        onNewJobEnqueued: suspend () -> Unit,
        isSessionCurrent: () -> Boolean,
        progress: ProgressCounter,
        verificationStartedAt: Long?,
        deferred: java.util.concurrent.atomic.AtomicInteger,
    ): Int {
        val selection = "${MediaStore.MediaColumns.SIZE} > 0"
        val sortOrder = "${MediaStore.MediaColumns.DATE_ADDED} DESC"

        val enqueued = java.util.concurrent.atomic.AtomicInteger(0)
        val cursor = contentResolver.query(
            collectionUri,
            projection(),
            selection,
            null,
            sortOrder
        ) ?: throw IllegalStateException("MediaStore returned no cursor for a media scan")
        // The cursor is read on this coroutine alone; each row's check (and the
        // hash of a new or changed file, which reads it whole) runs on one of
        // HASH_PARALLELISM workers. One at a time, a first sync of a large
        // library spent its time waiting on single file reads; side by side,
        // it is bound by how fast storage can read.
        coroutineScope {
        val rows = Channel<suspend () -> Unit>(HASH_PARALLELISM * 2)
        val workers = List(HASH_PARALLELISM) {
            launch(Dispatchers.IO) { for (row in rows) row() }
        }
        try {
        cursor.use {
            val idColumn = cursor.getColumnIndexOrThrow(MediaStore.MediaColumns._ID)
            val nameColumn = cursor.getColumnIndexOrThrow(MediaStore.MediaColumns.DISPLAY_NAME)
            val sizeColumn = cursor.getColumnIndexOrThrow(MediaStore.MediaColumns.SIZE)
            val dateAddedColumn = cursor.getColumnIndexOrThrow(MediaStore.MediaColumns.DATE_ADDED)
            val dateModifiedColumn = cursor.getColumnIndex(MediaStore.MediaColumns.DATE_MODIFIED)
            val dateTakenColumn = cursor.getColumnIndex(MediaStore.MediaColumns.DATE_TAKEN)
            val bucketIdColumn = cursor.getColumnIndex(MediaStore.MediaColumns.BUCKET_ID)
            val bucketNameColumn = cursor.getColumnIndex(MediaStore.MediaColumns.BUCKET_DISPLAY_NAME)
            val relativePathColumn = cursor.getColumnIndex(MediaStore.MediaColumns.RELATIVE_PATH)
            val volumeColumn = cursor.getColumnIndex(MediaStore.MediaColumns.VOLUME_NAME)
            val generationColumn = cursor.getColumnIndex(MediaStore.MediaColumns.GENERATION_MODIFIED)

            while (cursor.moveToNext()) {
                ensureSession(isSessionCurrent)
                val id = cursor.getLong(idColumn)
                val name = cursor.getString(nameColumn) ?: "media_$id"
                val size = cursor.getLong(sizeColumn)
                val dateTakenMs = if (dateTakenColumn >= 0) cursor.getLong(dateTakenColumn) else 0L
                val timestampMs = if (dateTakenMs > 0L) {
                    dateTakenMs
                } else {
                    val dateAddedSeconds = cursor.getLong(dateAddedColumn)
                    dateAddedSeconds * 1000L
                }
                val capturedAtIso = isoFormat.format(Date(timestampMs))
                val volume = cursor.stringOrEmpty(volumeColumn, "external")
                val bucketId = cursor.stringOrEmpty(bucketIdColumn, "unknown")
                val sourceId = sourceId(volume, bucketId, mediaKind)
                if (!policy.includes(sourceId, mediaKind)) continue

                val itemUri = ContentUris.withAppendedId(collectionUri, id)
                val generation = if (generationColumn >= 0) cursor.getLong(generationColumn) else 0L
                val dateModifiedSeconds = if (dateModifiedColumn >= 0 && !cursor.isNull(dateModifiedColumn)) {
                    cursor.getLong(dateModifiedColumn)
                } else {
                    0L
                }
                val sourceName = cursor.stringOrEmpty(bucketNameColumn, "Unknown source")
                val relativePath = cursor.stringOrEmpty(relativePathColumn, "")
                rows.send {
                val outcome = uploadManager.reconcileMedia(
                    accountKey = accountKey,
                    uri = itemUri,
                    filename = name,
                    capturedAtIso = capturedAtIso,
                    fingerprint = MediaFingerprint(
                        size = size,
                        dateModifiedSeconds = dateModifiedSeconds,
                        generation = generation,
                    ),
                    fullVerificationStartedAt = verificationStartedAt,
                    source = UploadSource(
                        id = sourceId,
                        name = sourceName,
                        relativePath = relativePath,
                        volume = volume,
                        mediaStoreId = id.toString(),
                        generation = generation,
                        mediaKind = mediaKind
                    ),
                    isSessionCurrent = isSessionCurrent,
                )
                if (outcome == SyncUploadManager.ScanOutcome.DEFERRED) deferred.incrementAndGet()
                if (outcome == SyncUploadManager.ScanOutcome.QUEUED_NEW ||
                    outcome == SyncUploadManager.ScanOutcome.QUEUED_NEW_VERSION
                ) {
                    enqueued.incrementAndGet()
                    onNewJobEnqueued()
                }
                progress.advance()
                }
            }
        }
        } finally {
            rows.close()
        }
        workers.joinAll()
        }
        return enqueued.get()
    }

    private companion object {
        const val PROGRESS_STEP = 25

        /** Files hashed side by side; beyond a few, storage reads are the limit. */
        const val HASH_PARALLELISM = 4
    }

    private fun sourceId(volume: String, bucketId: String, mediaKind: String): String =
        DeviceFolders.sourceId(volume, bucketId, mediaKind)

    private suspend fun ensureSession(isSessionCurrent: () -> Boolean) {
        currentCoroutineContext().ensureActive()
        if (!isSessionCurrent()) {
            throw CancellationException("Device session changed during media scan")
        }
    }

    private fun android.database.Cursor.stringOrEmpty(column: Int, fallback: String): String =
        if (column >= 0 && !isNull(column)) getString(column).orEmpty().ifBlank { fallback } else fallback
}
