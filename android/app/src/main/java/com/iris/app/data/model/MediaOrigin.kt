package com.iris.app.data.model

/**
 * Where the item the user is looking at actually lives.
 *
 * A gallery cell alone cannot answer "is this still on my phone, or only on the
 * server?", and that question decides whether deleting locally loses anything.
 * The states below are derived from the durable upload queue, which is the only
 * local record that survives the app being killed mid-upload.
 */
enum class MediaOrigin {
    /** On the server, with no trace of it in this device's upload history. */
    IRIS_ONLY,

    /** Bytes are moving right now, or waiting for their turn. */
    UPLOADING,

    /** Accepted by the server; captions, OCR and embeddings still pending. */
    PROCESSING,

    /** Uploaded from this device, so the phone is expected to still hold it. */
    ON_DEVICE,

    /** Upload attempt ended badly; the original is still only on the phone. */
    FAILED
}

/**
 * Maps a server item to its device state using the content hash.
 *
 * The hash is the join key because it is the one identifier both sides compute
 * independently: the client hashes the file before uploading, and the server
 * stores it with the item. The client never learns the server's media id for a
 * finished upload, so matching by id would require a schema change and a
 * migration for information the hash already carries.
 */
class MediaOriginIndex(private val stateByHash: Map<String, MediaOrigin>) {

    fun originOf(contentHash: String?): MediaOrigin {
        if (contentHash.isNullOrBlank()) return MediaOrigin.IRIS_ONLY
        return stateByHash[contentHash.lowercase()] ?: MediaOrigin.IRIS_ONLY
    }

    companion object {
        val EMPTY = MediaOriginIndex(emptyMap())

        fun from(jobs: List<LocalUploadJob>): MediaOriginIndex {
            val stateByHash = mutableMapOf<String, MediaOrigin>()
            for (job in jobs) {
                val hash = job.sha256.lowercase()
                if (hash.isBlank()) continue
                val candidate = job.state.toOrigin()
                val current = stateByHash[hash]
                // The same file can be queued more than once across rescans, so
                // one hash can carry several job rows. A later failed retry of a
                // file that already reached the server must not be reported as
                // failed: success outranks everything, and failure only stands
                // when nothing better happened to that hash.
                if (current == null || candidate.rank() > current.rank()) {
                    stateByHash[hash] = candidate
                }
            }
            return MediaOriginIndex(stateByHash)
        }

        private fun MediaOrigin.rank(): Int = when (this) {
            MediaOrigin.ON_DEVICE -> 4
            MediaOrigin.PROCESSING -> 3
            MediaOrigin.UPLOADING -> 2
            MediaOrigin.FAILED -> 1
            MediaOrigin.IRIS_ONLY -> 0
        }

        private fun UploadJobState.toOrigin(): MediaOrigin = when (this) {
            UploadJobState.QUEUED, UploadJobState.UPLOADING -> MediaOrigin.UPLOADING
            UploadJobState.PENDING_PROCESSING, UploadJobState.PROCESSING -> MediaOrigin.PROCESSING
            UploadJobState.READY, UploadJobState.DUPLICATE -> MediaOrigin.ON_DEVICE
            UploadJobState.FAILED, UploadJobState.FAILED_PROCESSING -> MediaOrigin.FAILED
        }
    }
}
