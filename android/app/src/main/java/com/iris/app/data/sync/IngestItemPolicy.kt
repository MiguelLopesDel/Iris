package com.iris.app.data.sync

/**
 * What a batch-ingest answer means for one queued photo, by the same rules as
 * the resumable path: a state the server settled, or an error that is final
 * for the photo, or one worth another attempt.
 */
internal object IngestItemPolicy {
    enum class Action {
        READY,
        DUPLICATE,
        PENDING,
        /** Back to the queue as it is: a later pass sends it again. */
        SEND_AGAIN,
        /** The server lost the reservation: reserve again from the start. */
        RESERVE_AGAIN,
        /** The bytes did not match the declared hash: hash the file again. */
        HASH_AGAIN,
        FAIL,
    }

    fun ofState(state: String?): Action = when (state) {
        "ready" -> Action.READY
        "duplicate" -> Action.DUPLICATE
        "pending_processing", "processing" -> Action.PENDING
        // Reserved but not received (a batch cut short, or resolved after a stop).
        "uploading", "receiving" -> Action.SEND_AGAIN
        else -> Action.FAIL
    }

    fun ofError(code: Int): Action = when {
        code == ResumableUploadTransfer.HASH_MISMATCH_STATUS -> Action.HASH_AGAIN
        code == 404 -> Action.RESERVE_AGAIN
        code == 401 || code == 409 || ResumableUploadTransfer.isTransientHttpStatus(code) -> Action.SEND_AGAIN
        else -> Action.FAIL
    }
}
