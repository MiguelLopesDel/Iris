package com.iris.app.data.sync

import com.iris.app.data.model.IngestLimits

/**
 * How large each batch-ingest request is, from what the server says it takes.
 *
 * A file larger than [maxFileBytes] keeps the resumable path: it can resume
 * from a committed offset, which a batch cannot.
 */
internal data class IngestBatchSize(val maxItems: Int, val maxBytes: Long, val maxFileBytes: Long) {
    fun takes(size: Long): Boolean = size <= maxFileBytes

    /** Whether a photo of [nextSize] still fits a batch holding [count] photos and [bytes]. */
    fun fits(count: Int, bytes: Long, nextSize: Long): Boolean =
        count < maxItems && bytes + nextSize <= maxBytes

    companion object {
        const val MAX_FILE_BYTES = 8L * 1024 * 1024

        /** Null when the server reports no usable limits (an older server: keep the resumable path). */
        fun from(limits: IngestLimits?): IngestBatchSize? {
            if (limits == null || limits.maxItems <= 0 || limits.maxBytes <= 0L) return null
            val items = (if (limits.suggestedItems > 0) limits.suggestedItems else limits.maxItems)
                .coerceAtMost(limits.maxItems)
            return IngestBatchSize(items, limits.maxBytes, minOf(MAX_FILE_BYTES, limits.maxBytes))
        }
    }
}
