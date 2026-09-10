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

    override fun callStart(call: Call) {
        metric = metricFor(call.request())
        finish = monitor.begin(metric)
    }

    override fun callEnd(call: Call) = finishOnce()

    override fun callFailed(call: Call, ioe: IOException) = finishOnce()

    private fun finishOnce() {
        finish?.invoke()
        finish = null
    }

    companion object {
        fun factory(monitor: PerformanceMonitor): Factory = Factory { IrisPerformanceEventListener(monitor) }

        private fun metricFor(request: Request): Metric = when {
            request.url.encodedPath.endsWith("/healthz") -> Metric.NetworkHealth
            request.url.encodedPath.contains("/api/info") -> Metric.NetworkInfo
            request.url.encodedPath.contains("/api/records") -> Metric.NetworkRecords
            request.url.encodedPath.contains("/media/") || request.url.encodedPath.contains("/thumbnail") -> Metric.NetworkMedia
            else -> Metric.NetworkOther
        }
    }
}
