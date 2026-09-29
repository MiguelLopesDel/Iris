package com.iris.app.performance

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlin.math.ceil

/**
 * Local, opt-in performance diagnostics. It stores aggregate timings only:
 * never URLs, request bodies, account data, or media identifiers.
 */
class PerformanceMonitor(
    private val nowNanos: () -> Long = System::nanoTime,
    private val maxSamplesPerMetric: Int = 120
) {
    private val samples = linkedMapOf<String, ArrayDeque<Double>>()
    private val transferSamples = ArrayDeque<TransferSample>()
    private var enabled = false
    private var recordingGeneration = 0L

    private val _report = MutableStateFlow(PerformanceReport())
    val report: StateFlow<PerformanceReport> = _report.asStateFlow()

    fun start() {
        synchronized(this) {
            recordingGeneration++
            enabled = true
            samples.clear()
            transferSamples.clear()
            publishLocked()
        }
    }

    fun stop() {
        synchronized(this) {
            recordingGeneration++
            enabled = false
            publishLocked()
        }
    }

    fun isEnabled(): Boolean = synchronized(this) { enabled }

    /** Identifies the current diagnostics session, or null while diagnostics are off. */
    fun activeGeneration(): Long? = synchronized(this) {
        if (enabled) recordingGeneration else null
    }

    /** Starts a span and returns its idempotent finisher. */
    fun begin(metric: Metric): () -> Unit {
        if (!isEnabled()) return {}
        val startedAt = nowNanos()
        var finished = false
        return {
            synchronized(this) {
                if (!finished) {
                    finished = true
                    recordLocked(metric, nanosToMillis(nowNanos() - startedAt))
                }
            }
        }
    }

    fun record(metric: Metric, elapsedMillis: Double) {
        synchronized(this) {
            if (enabled) recordLocked(metric, elapsedMillis)
        }
    }

    /** Records only acknowledged upload bytes and aggregate elapsed time for one queue run. */
    fun recordTransfer(
        bytes: Long,
        elapsedMillis: Double,
        activeElapsedMillis: Double = elapsedMillis,
        confirmedItems: Long = 0L,
    ) {
        if (bytes < 0L || confirmedItems < 0L || (bytes == 0L && confirmedItems == 0L) ||
            !elapsedMillis.isFinite() || elapsedMillis <= 0.0 ||
            !activeElapsedMillis.isFinite() || activeElapsedMillis < 0.0
        ) return
        synchronized(this) {
            if (!enabled) return
            recordTransferLocked(bytes, elapsedMillis, activeElapsedMillis, confirmedItems)
        }
    }

    /** Records a run only into the diagnostics session that observed its start. */
    fun recordTransferForGeneration(
        generation: Long,
        bytes: Long,
        elapsedMillis: Double,
        activeElapsedMillis: Double,
        confirmedItems: Long = 0L,
    ) {
        if (bytes < 0L || confirmedItems < 0L || (bytes == 0L && confirmedItems == 0L) ||
            !elapsedMillis.isFinite() || elapsedMillis <= 0.0 ||
            !activeElapsedMillis.isFinite() || activeElapsedMillis < 0.0
        ) return
        synchronized(this) {
            if (!enabled || recordingGeneration != generation) return
            recordTransferLocked(bytes, elapsedMillis, activeElapsedMillis, confirmedItems)
        }
    }

    private fun recordTransferLocked(
        bytes: Long,
        elapsedMillis: Double,
        activeElapsedMillis: Double,
        confirmedItems: Long,
    ) {
        if (transferSamples.size == maxSamplesPerMetric) transferSamples.removeFirst()
        transferSamples.addLast(TransferSample(bytes, elapsedMillis, activeElapsedMillis, confirmedItems))
        publishLocked()
    }

    private fun recordLocked(metric: Metric, elapsedMillis: Double) {
        if (!elapsedMillis.isFinite() || elapsedMillis < 0) return
        val values = samples.getOrPut(metric.key) { ArrayDeque() }
        if (values.size == maxSamplesPerMetric) values.removeFirst()
        values.addLast(elapsedMillis)
        publishLocked()
    }

    private fun publishLocked() {
        val totalTransferBytes = transferSamples.sumOf { it.bytes }
        val totalConfirmedItems = transferSamples.sumOf { it.confirmedItems }
        val totalTransferMillis = transferSamples.sumOf { it.elapsedMillis }
        val totalActiveTransferMillis = transferSamples.sumOf { it.activeElapsedMillis }
        _report.value = PerformanceReport(
            enabled = enabled,
            metrics = samples.map { (name, values) ->
                val ordered = values.sorted()
                MetricSummary(
                    name = name,
                    count = ordered.size,
                    medianMs = percentile(ordered, 0.50),
                    p90Ms = percentile(ordered, 0.90),
                    maxMs = ordered.lastOrNull() ?: 0.0
                )
            },
            upload = if (transferSamples.isEmpty() || totalTransferMillis <= 0.0) null else {
                UploadPerformanceSummary(
                    runs = transferSamples.size,
                    bytes = totalTransferBytes,
                    elapsedMillis = totalTransferMillis,
                    mibPerSecond = totalTransferBytes / 1_048_576.0 / (totalTransferMillis / 1_000.0),
                    activeElapsedMillis = totalActiveTransferMillis,
                    activeMibPerSecond = if (totalActiveTransferMillis > 0.0) {
                        totalTransferBytes / 1_048_576.0 / (totalActiveTransferMillis / 1_000.0)
                    } else {
                        0.0
                    },
                    confirmedItems = totalConfirmedItems,
                    itemsPerSecond = totalConfirmedItems / (totalTransferMillis / 1_000.0),
                )
            }
        )
    }

    private fun percentile(values: List<Double>, percentile: Double): Double {
        if (values.isEmpty()) return 0.0
        val index = (ceil(values.size * percentile).toInt() - 1).coerceIn(0, values.lastIndex)
        return values[index]
    }

    private fun nanosToMillis(nanos: Long): Double = nanos / 1_000_000.0

    private data class TransferSample(
        val bytes: Long,
        val elapsedMillis: Double,
        val activeElapsedMillis: Double,
        val confirmedItems: Long,
    )
}

/** Counts the wall-time union of concurrent payload PUT calls, excluding scan/queue gaps. */
class UploadTransferActivityTracker(
    private val nowNanos: () -> Long = System::nanoTime,
) {
    private val lock = Any()
    private var activeRequests = 0
    private var activeStartedAtNanos = 0L
    private var accumulatedActiveNanos = 0L
    private var finished = false

    fun beginRequest(): () -> Unit {
        val startedAtNanos = nowNanos()
        synchronized(lock) {
            check(!finished) { "Cannot start a payload request after transfer accounting is finished" }
            if (activeRequests == 0) activeStartedAtNanos = startedAtNanos
            activeRequests++
        }

        var requestFinished = false
        return {
            synchronized(lock) {
                if (!requestFinished) {
                    requestFinished = true
                    check(activeRequests > 0) { "Payload request accounting became unbalanced" }
                    activeRequests--
                    if (activeRequests == 0) {
                        accumulatedActiveNanos += (nowNanos() - activeStartedAtNanos).coerceAtLeast(0L)
                    }
                }
            }
        }
    }

    fun finishAndGetActiveMillis(): Double = synchronized(lock) {
        check(activeRequests == 0) { "Cannot finish payload accounting while requests are active" }
        finished = true
        accumulatedActiveNanos / 1_000_000.0
    }
}

data class PerformanceReport(
    val enabled: Boolean = false,
    val metrics: List<MetricSummary> = emptyList(),
    val upload: UploadPerformanceSummary? = null
)

data class UploadPerformanceSummary(
    val runs: Int,
    val bytes: Long,
    val elapsedMillis: Double,
    val mibPerSecond: Double,
    val activeElapsedMillis: Double,
    val activeMibPerSecond: Double,
    val confirmedItems: Long,
    val itemsPerSecond: Double,
)

data class MetricSummary(
    val name: String,
    val count: Int,
    val medianMs: Double,
    val p90Ms: Double,
    val maxMs: Double
)

/** Only predefined metric names can be recorded, which prevents private data leakage. */
enum class Metric(val key: String) {
    AppToConfiguration("startup.app_to_configuration"),
    GalleryFirstPage("gallery.first_page_data"),
    GalleryFirstContent("gallery.first_content"),
    GalleryPage("gallery.page_data"),
    CollectionMembers("collection.members_data"),
    CollectionFirstContent("collection.first_content"),
    NavigationGallery("navigation.gallery_first_frame"),
    NavigationSearch("navigation.search_first_frame"),
    NavigationAlbums("navigation.albums_first_frame"),
    NavigationSync("navigation.sync_first_frame"),
    NavigationSpaces("navigation.spaces_first_frame"),
    PreviewImage("preview.image"),
    PreviewVideo("preview.video"),
    NetworkHealth("network.health.total"),
    NetworkInfo("network.info.total"),
    NetworkRecords("network.records.total"),
    NetworkCollectionMembers("network.collection_members.total"),
    NetworkMedia("network.media.total"),
    SyncMediaScan("sync.media_scan.total"),
    SyncMediaHash("sync.media_hash.total"),
    SyncFirstUploadJobStart("sync.upload.first_job_start"),
    SyncUploadQueue("sync.upload_queue.total"),
    SyncUploadInit("sync.upload_init.total"),
    SyncUploadInitBatch("sync.upload_init_batch.total"),
    SyncUploadStatus("sync.upload_status.total"),
    SyncUploadChunk("sync.upload_chunk.total"),
    SyncUploadChunkBody("sync.upload_chunk.body"),
    SyncUploadChunkAckWait("sync.upload_chunk.ack_wait"),
    SyncUploadComplete("sync.upload_complete.total"),
    SyncUploadCompleteBatch("sync.upload_complete_batch.total"),
    NetworkOther("network.other.total")
}
