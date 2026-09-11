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
    private var enabled = false

    private val _report = MutableStateFlow(PerformanceReport())
    val report: StateFlow<PerformanceReport> = _report.asStateFlow()

    fun start() {
        synchronized(this) {
            enabled = true
            samples.clear()
            publishLocked()
        }
    }

    fun stop() {
        synchronized(this) {
            enabled = false
            publishLocked()
        }
    }

    fun isEnabled(): Boolean = synchronized(this) { enabled }

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

    private fun recordLocked(metric: Metric, elapsedMillis: Double) {
        if (!elapsedMillis.isFinite() || elapsedMillis < 0) return
        val values = samples.getOrPut(metric.key) { ArrayDeque() }
        if (values.size == maxSamplesPerMetric) values.removeFirst()
        values.addLast(elapsedMillis)
        publishLocked()
    }

    private fun publishLocked() {
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
            }
        )
    }

    private fun percentile(values: List<Double>, percentile: Double): Double {
        if (values.isEmpty()) return 0.0
        val index = (ceil(values.size * percentile).toInt() - 1).coerceIn(0, values.lastIndex)
        return values[index]
    }

    private fun nanosToMillis(nanos: Long): Double = nanos / 1_000_000.0
}

data class PerformanceReport(
    val enabled: Boolean = false,
    val metrics: List<MetricSummary> = emptyList()
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
    PreviewImage("preview.image"),
    PreviewVideo("preview.video"),
    NetworkHealth("network.health.total"),
    NetworkInfo("network.info.total"),
    NetworkRecords("network.records.total"),
    NetworkCollectionMembers("network.collection_members.total"),
    NetworkMedia("network.media.total"),
    NetworkOther("network.other.total")
}
