package com.iris.app.data.model

import com.iris.app.data.sync.KnownMedia
import com.iris.app.data.sync.MediaChangePolicy
import com.iris.app.data.sync.MediaFingerprint

/**
 * Where the item the user is looking at actually lives.
 *
 * A gallery cell alone cannot answer "is this still on my phone, or only on the
 * server?", and that question decides whether deleting locally loses anything.
 * The states below are derived from the durable upload queue, which is the only
 * local record that survives the app being killed mid-upload.
 */
enum class MediaOrigin {
    /** Exists only in the phone's gallery and has not been sent to Iris. */
    DEVICE_ONLY,

    /** On the server, with no trace of it in this device's upload history. */
    IRIS_ONLY,

    /** Bytes are moving right now, or waiting for their turn. */
    UPLOADING,

    /** Accepted by the server; captions, OCR and embeddings still pending. */
    PROCESSING,

    /** Uploaded from this device, so the phone is expected to still hold it. */
    ON_DEVICE,

    /** Upload attempt ended badly; the original is still only on the phone. */
    FAILED,

    /**
     * The file changed since it was hashed (edited, overwritten, or its id now
     * names another item): what the server holds is unknown until the next scan
     * hashes it again.
     */
    CHECKING
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
class MediaOriginIndex(
    private val stateByHash: Map<String, MediaOrigin>,
    private val stateByLocalUri: Map<String, MediaOrigin>,
    /** The fingerprint each local item had when hashed, keyed like [stateByLocalUri]. */
    private val knownByLocalUri: Map<String, KnownMedia> = emptyMap(),
) {

    fun originOf(contentHash: String?): MediaOrigin {
        if (contentHash.isNullOrBlank()) return MediaOrigin.IRIS_ONLY
        return stateByHash[contentHash.lowercase()] ?: MediaOrigin.IRIS_ONLY
    }

    fun originOf(record: MediaRecord): MediaOrigin = record.deviceUri?.let { uri ->
        val key = MediaStoreKey.of(uri)
        val origin = stateByLocalUri[key] ?: return@let MediaOrigin.DEVICE_ONLY
        val current = record.deviceFingerprint
        val known = knownByLocalUri[key]
        if (current != null && known != null &&
            MediaChangePolicy.decide(known, current, null) == MediaChangePolicy.Verdict.VERIFY
        ) {
            MediaOrigin.CHECKING
        } else {
            origin
        }
    } ?: originOf(record.contentHash)

    /** Processing and finished jobs mean the server has already accepted the bytes. */
    fun hasServerCopy(contentHash: String?): Boolean = when (originOf(contentHash)) {
        MediaOrigin.ON_DEVICE, MediaOrigin.PROCESSING -> true
        else -> false
    }

    companion object {
        val EMPTY = MediaOriginIndex(emptyMap(), emptyMap())

        fun from(jobs: List<LocalUploadJob>): MediaOriginIndex {
            val stateByHash = mutableMapOf<String, MediaOrigin>()
            val stateByLocalUri = mutableMapOf<String, MediaOrigin>()
            val knownByLocalUri = mutableMapOf<String, KnownMedia>()
            for (job in jobs) {
                val candidate = job.state.toOrigin()
                val localUri = job.localUri.let { if (it.isBlank()) it else MediaStoreKey.of(it) }
                val localCurrent = stateByLocalUri[localUri]
                if (localUri.isNotBlank() && (localCurrent == null || candidate.rank() > localCurrent.rank())) {
                    stateByLocalUri[localUri] = candidate
                    knownByLocalUri[localUri] = KnownMedia(
                        jobId = job.id,
                        sha256 = job.sha256,
                        fingerprint = MediaFingerprint(
                            size = job.byteSize,
                            dateModifiedSeconds = job.sourceDateModified,
                            generation = job.source?.generation ?: 0L,
                        ),
                        verifiedAt = job.verifiedAt,
                    )
                }
                val hash = job.sha256.lowercase()
                if (hash.isBlank()) continue
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
            return MediaOriginIndex(stateByHash, stateByLocalUri, knownByLocalUri)
        }

        private fun MediaOrigin.rank(): Int = when (this) {
            MediaOrigin.ON_DEVICE -> 5
            MediaOrigin.PROCESSING -> 4
            MediaOrigin.UPLOADING -> 3
            MediaOrigin.CHECKING -> 2
            MediaOrigin.FAILED -> 2
            MediaOrigin.DEVICE_ONLY -> 1
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

/**
 * The upload queue reduced to the few numbers a person actually reads.
 *
 * A first sync enqueues thousands of rows, and a list of thousands of identical
 * cards answers no question that these counts do not. The grouping is not
 * obvious enough to leave implicit: a duplicate is a success (the server
 * already holds those bytes), and an item accepted but not yet indexed is
 * neither "sending" nor "done".
 */
data class UploadQueueSummary(
    val queued: Int = 0,
    val uploading: Int = 0,
    val processing: Int = 0,
    /** Sent by this device. */
    val uploaded: Int = 0,
    /**
     * Never sent: the server already had the same bytes. Shown apart from
     * [uploaded] because on a new server or a reinstall nearly everything lands
     * here, and one "done" number made thousands look uploaded in seconds.
     */
    val alreadyOnServer: Int = 0,
    val failed: Int = 0
) {
    /** Items the server holds, sent now or before. */
    val finished: Int get() = uploaded + alreadyOnServer
    val total: Int get() = queued + uploading + processing + finished + failed

    companion object {
        fun from(counts: Map<UploadJobState, Int>): UploadQueueSummary {
            fun count(vararg states: UploadJobState) = states.sumOf { counts[it] ?: 0 }
            return UploadQueueSummary(
                queued = count(UploadJobState.QUEUED),
                uploading = count(UploadJobState.UPLOADING),
                processing = count(
                    UploadJobState.PENDING_PROCESSING,
                    UploadJobState.PROCESSING
                ),
                uploaded = count(UploadJobState.READY),
                alreadyOnServer = count(UploadJobState.DUPLICATE),
                failed = count(UploadJobState.FAILED, UploadJobState.FAILED_PROCESSING)
            )
        }
    }
}
