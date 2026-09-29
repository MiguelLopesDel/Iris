package com.iris.app.performance

import okhttp3.Call
import okhttp3.EventListener
import okhttp3.Request
import java.io.IOException

/** Records end-to-end HTTP call duration, grouped into fixed non-sensitive route families. */
class IrisPerformanceEventListener(
    private val monitor: PerformanceMonitor
) : EventListener() {
    private var metric: Metric = Metric.NetworkOther
    private var finish: (() -> Unit)? = null
    private var isUploadChunk = false
    private var uploadBodyStartedAtNanos: Long? = null
    private var uploadBodyEndedAtNanos: Long? = null

    override fun callStart(call: Call) {
        metric = metricFor(call.request())
        isUploadChunk = metric == Metric.SyncUploadChunk
        finish = monitor.begin(metric)
    }

    override fun requestBodyStart(call: Call) {
        if (isUploadChunk) {
            uploadBodyStartedAtNanos = System.nanoTime()
            uploadBodyEndedAtNanos = null
        }
    }

    override fun requestBodyEnd(call: Call, byteCount: Long) {
        if (!isUploadChunk) return
        val finishedAt = System.nanoTime()
        uploadBodyStartedAtNanos?.let { startedAt ->
            monitor.record(Metric.SyncUploadChunkBody, nanosToMillis(finishedAt - startedAt))
        }
        uploadBodyStartedAtNanos = null
        uploadBodyEndedAtNanos = finishedAt
    }

    override fun responseHeadersStart(call: Call) {
        if (!isUploadChunk) return
        uploadBodyEndedAtNanos?.let { bodyEndedAt ->
            monitor.record(Metric.SyncUploadChunkAckWait, nanosToMillis(System.nanoTime() - bodyEndedAt))
        }
        uploadBodyEndedAtNanos = null
    }

    override fun callEnd(call: Call) = finishOnce()

    override fun callFailed(call: Call, ioe: IOException) = finishOnce()

    private fun finishOnce() {
        finish?.invoke()
        finish = null
    }

    private fun nanosToMillis(nanos: Long): Double = nanos / 1_000_000.0

    companion object {
        fun factory(monitor: PerformanceMonitor): Factory = Factory { IrisPerformanceEventListener(monitor) }

        private fun metricFor(request: Request): Metric {
            val path = request.url.encodedPath
            return when {
            path.endsWith("/healthz") -> Metric.NetworkHealth
            path.endsWith("/api/sync/uploads/batch") && request.method == "POST" -> Metric.SyncUploadInitBatch
            path.endsWith("/api/sync/uploads/complete-batch") && request.method == "POST" -> Metric.SyncUploadCompleteBatch
            path.contains("/api/sync/uploads") && request.method == "POST" &&
                path.endsWith("/complete") -> Metric.SyncUploadComplete
            path.endsWith("/api/sync/uploads") && request.method == "POST" -> Metric.SyncUploadInit
            path.contains("/api/sync/uploads/") && request.method == "PUT" -> Metric.SyncUploadChunk
            path.contains("/api/sync/uploads/") && request.method == "GET" -> Metric.SyncUploadStatus
            path.contains("/api/info") -> Metric.NetworkInfo
            path.contains("/api/collections/") &&
                path.endsWith("/members") -> Metric.NetworkCollectionMembers
            path.contains("/api/records") -> Metric.NetworkRecords
            path.contains("/media/") || path.contains("/thumbnail") -> Metric.NetworkMedia
            else -> Metric.NetworkOther
            }
        }
    }
}
