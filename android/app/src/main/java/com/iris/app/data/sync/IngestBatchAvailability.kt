package com.iris.app.data.sync

import com.iris.app.data.model.IngestLimits

/** Distinguishes an older server from a server that temporarily cannot report ingest limits. */
internal sealed interface IngestBatchAvailability {
    data class Supported(val size: IngestBatchSize) : IngestBatchAvailability
    data object Unsupported : IngestBatchAvailability
    data object Retry : IngestBatchAvailability

    companion object {
        fun fromHttp(statusCode: Int, limits: IngestLimits?): IngestBatchAvailability = when {
            statusCode == 404 -> Unsupported
            statusCode in 200..299 -> IngestBatchSize.from(limits)
                ?.let(::Supported) ?: Retry
            else -> Retry
        }
    }
}
