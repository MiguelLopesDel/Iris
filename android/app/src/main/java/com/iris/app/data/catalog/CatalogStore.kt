package com.iris.app.data.catalog

import com.iris.app.data.model.MediaRecord

/**
 * Local mirror of the server's catalog — the seam the gallery reads from.
 *
 * The gallery today renders straight from whatever a network call returned and
 * keeps it only in memory, so a cold start shows nothing until three sequential
 * round trips finish, and killing the app throws the whole thing away. Behind
 * this interface the catalog lives on disk, is written in batches rather than
 * row by row, and is reconciled with the server in the background.
 *
 * Two adapters exist: SQLite on the device and an in-memory one for tests, so
 * batching and ordering can be verified on the JVM without an emulator.
 */
interface CatalogStore {

    /**
     * Writes a whole batch in one transaction, keyed by the record's stable
     * database id. Writing per record would be one transaction per row — the
     * write amplification this design exists to avoid.
     *
     * Records without a `dbId` are skipped: `index` is a position in the
     * server's sorted catalog, not an identity, so keying on it would silently
     * mismatch rows as soon as anything is imported or removed.
     */
    suspend fun upsertBatch(records: List<MediaRecord>)

    /** A window of the mirror, newest capture first. */
    suspend fun page(offset: Int, limit: Int, mediaType: String = "all"): List<MediaRecord>

    /** How many records the mirror holds for a filter. */
    suspend fun count(mediaType: String = "all"): Int

    /** Drops everything — used when the mirror belongs to a different server. */
    suspend fun clear()
}
