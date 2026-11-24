package com.iris.app.data.sync

/**
 * Whether a resumable upload is getting anywhere. The server answers each
 * accepted chunk with the next offset; an answer that does not move it (a
 * stale or misbehaving server), or a chunk size of zero, sent the same chunk
 * again with no end.
 */
internal class ChunkProgress(private val maxStalls: Int = 3) {
    private var stalls = 0

    /** False once the offset failed to advance more than [maxStalls] times in a row. */
    fun accept(from: Long, to: Long): Boolean {
        stalls = if (to > from) 0 else stalls + 1
        return stalls <= maxStalls
    }

    companion object {
        /** For a reservation that names no chunk size: well under the server's request limit (32 MiB by default). */
        const val FALLBACK_CHUNK_BYTES = 8 * 1024 * 1024

        fun usableChunkSize(fromServer: Int): Int = if (fromServer > 0) fromServer else FALLBACK_CHUNK_BYTES
    }
}
