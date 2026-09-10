package com.iris.app.data.catalog

import com.iris.app.data.model.MediaRecord

/**
 * The gallery's source of truth.
 *
 * Before this, the gallery was a pass-through: every screenful came from the
 * network and lived only in memory, so a cold start waited on three sequential
 * round trips before painting anything and a kill discarded the lot. Here the
 * mirror answers immediately from disk and the network's job is reconciliation,
 * not serving — which is the shape a photo library needs and the reason
 * Google Photos opens instantly on a bad connection.
 *
 * Two things the caller does not have to know about, which is the point of
 * putting them behind one interface:
 *
 * - **Coarse fetching.** Reconciliation pulls far larger pages than a screenful
 *   (see [RECONCILE_PAGE_SIZE]). Scroll position stops driving request size.
 * - **Batched writes.** Each fetched page is written in a single transaction.
 *   One transaction per record would not be a redundant write — it would be
 *   exactly one write per record — and still be the wrong shape, because the
 *   per-transaction cost would dominate a 17k-record catalog.
 */
class MediaCatalog(
    private val store: CatalogStore,
    private val fetchPage: suspend (page: Int, perPage: Int, mediaType: String) -> FetchedPage,
) {

    /** One page as the server returned it. */
    data class FetchedPage(
        val records: List<MediaRecord>,
        val page: Int,
        val totalPages: Int,
        val total: Int,
    )

    data class ReconcileResult(
        val written: Int,
        val pagesFetched: Int,
        val serverTotal: Int,
        val complete: Boolean,
    )

    /** What the mirror can answer right now, without touching the network. */
    suspend fun cached(offset: Int, limit: Int, mediaType: String = "all"): List<MediaRecord> =
        store.page(offset, limit, mediaType)

    suspend fun cachedCount(mediaType: String = "all"): Int = store.count(mediaType)

    /**
     * Pulls the catalog from the server into the mirror, newest first.
     *
     * Stops at [maxPages] so a first run on a large library yields something
     * usable quickly instead of blocking on the whole catalog; calling again
     * continues from the top, and an unchanged page is simply rewritten with the
     * same values. A failed page ends the run and keeps what was already
     * written — a partial mirror is still better than none, and the next call
     * picks up from there.
     */
    suspend fun reconcile(
        mediaType: String = "all",
        maxPages: Int = Int.MAX_VALUE,
    ): ReconcileResult {
        var written = 0
        var pagesFetched = 0
        var serverTotal = 0
        var page = 1
        var complete = false

        while (pagesFetched < maxPages) {
            val fetched = try {
                fetchPage(page, RECONCILE_PAGE_SIZE, mediaType)
            } catch (_: Exception) {
                break
            }
            pagesFetched++
            serverTotal = fetched.total

            if (fetched.records.isEmpty()) {
                complete = true
                break
            }

            store.upsertBatch(fetched.records)
            written += fetched.records.count { it.dbId != null }

            if (fetched.page >= fetched.totalPages) {
                complete = true
                break
            }
            page = fetched.page + 1
        }

        return ReconcileResult(written, pagesFetched, serverTotal, complete)
    }

    companion object {
        /**
         * Deliberately far larger than a screenful. The gallery used to request
         * 24 records at a time because that is roughly what a scroll reveals,
         * which made walking a 17k-record catalog 723 requests. Reconciliation
         * is not driven by what is on screen, so it pays one round trip per 200.
         */
        const val RECONCILE_PAGE_SIZE = 200
    }
}
