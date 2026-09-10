package com.iris.app

import com.iris.app.data.catalog.CatalogStore
import com.iris.app.data.catalog.MediaCatalog
import com.iris.app.data.model.MediaRecord
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The batching and reconciliation logic, verified on the JVM.
 *
 * The in-memory store here is the second adapter behind [CatalogStore] — with
 * only the SQLite one the seam would be hypothetical. It also records how many
 * write transactions happened, which is the property this design exists for:
 * one write per batch, not one per record.
 */
class MediaCatalogTest {

    private class InMemoryStore : CatalogStore {
        val rows = linkedMapOf<Int, MediaRecord>()
        var writeCalls = 0
            private set

        override suspend fun upsertBatch(records: List<MediaRecord>) {
            writeCalls++
            records.forEach { record -> record.dbId?.let { rows[it] = record } }
        }

        override suspend fun page(offset: Int, limit: Int, mediaType: String): List<MediaRecord> =
            rows.values
                .filter { mediaType == "all" || it.mediaType == mediaType }
                .sortedByDescending { it.fileMtime ?: 0.0 }
                .drop(offset)
                .take(limit)

        override suspend fun count(mediaType: String): Int =
            rows.values.count { mediaType == "all" || it.mediaType == mediaType }

        override suspend fun clear() = rows.clear()
    }

    private fun record(dbId: Int, mtime: Double = dbId.toDouble(), type: String = "image") =
        MediaRecord(
            index = dbId,
            dbId = dbId,
            arquivo = "media-$dbId.jpg",
            mediaType = type,
            fileMtime = mtime,
        )

    private fun pageOf(records: List<MediaRecord>, page: Int, totalPages: Int, total: Int) =
        MediaCatalog.FetchedPage(records, page, totalPages, total)

    @Test
    fun `writes once per page, not once per record`() = runTest {
        val store = InMemoryStore()
        val catalog = MediaCatalog(store) { page, _, _ ->
            val records = (1..50).map { record(dbId = (page - 1) * 50 + it) }
            pageOf(records, page, totalPages = 3, total = 150)
        }

        val result = catalog.reconcile()

        assertEquals(150, result.written)
        assertEquals(150, store.rows.size)
        // The whole point: three pages of fifty records are three writes.
        assertEquals(3, store.writeCalls)
    }

    @Test
    fun `asks the server for coarse pages, not screenfuls`() = runTest {
        val requested = mutableListOf<Int>()
        val catalog = MediaCatalog(InMemoryStore()) { page, perPage, _ ->
            requested += perPage
            pageOf(listOf(record(page)), page, totalPages = 1, total = 1)
        }

        catalog.reconcile()

        assertEquals(listOf(MediaCatalog.RECONCILE_PAGE_SIZE), requested)
        assertTrue(MediaCatalog.RECONCILE_PAGE_SIZE > 24)
    }

    @Test
    fun `a failed page keeps what was already written`() = runTest {
        val store = InMemoryStore()
        val catalog = MediaCatalog(store) { page, _, _ ->
            if (page == 3) throw java.io.IOException("conexão caiu")
            pageOf((1..10).map { record((page - 1) * 10 + it) }, page, totalPages = 9, total = 90)
        }

        val result = catalog.reconcile()

        // Two pages landed before the failure; a partial mirror beats none.
        assertEquals(20, store.rows.size)
        assertEquals(20, result.written)
        assertFalse(result.complete)
    }

    @Test
    fun `stops at the page budget so a first run yields something usable`() = runTest {
        val store = InMemoryStore()
        val catalog = MediaCatalog(store) { page, _, _ ->
            pageOf((1..10).map { record((page - 1) * 10 + it) }, page, totalPages = 100, total = 1000)
        }

        val result = catalog.reconcile(maxPages = 2)

        assertEquals(20, store.rows.size)
        assertEquals(2, result.pagesFetched)
        assertFalse(result.complete)
    }

    @Test
    fun `re-reconciling updates rows in place instead of duplicating them`() = runTest {
        val store = InMemoryStore()
        var caption = "antes"
        val catalog = MediaCatalog(store) { page, _, _ ->
            pageOf(
                listOf(record(dbId = 1).copy(descricaoIa = caption)),
                page, totalPages = 1, total = 1,
            )
        }

        catalog.reconcile()
        caption = "depois"
        catalog.reconcile()

        assertEquals(1, store.rows.size)
        assertEquals("depois", store.rows[1]?.descricaoIa)
    }

    @Test
    fun `records without a stable id are not mirrored`() = runTest {
        val store = InMemoryStore()
        val catalog = MediaCatalog(store) { page, _, _ ->
            // `index` is a position in the server's sorted catalog, not an
            // identity — keying the mirror on it would mismatch rows as soon as
            // anything is imported or removed.
            pageOf(listOf(record(1), record(2).copy(dbId = null)), page, 1, 2)
        }

        val result = catalog.reconcile()

        assertEquals(1, result.written)
        assertEquals(setOf(1), store.rows.keys)
    }

    @Test
    fun `the mirror answers without touching the network`() = runTest {
        val store = InMemoryStore()
        var fetches = 0
        val catalog = MediaCatalog(store) { page, _, _ ->
            fetches++
            pageOf((1..5).map { record(it, mtime = it.toDouble()) }, page, 1, 5)
        }
        catalog.reconcile()

        val firstScreen = catalog.cached(offset = 0, limit = 3)

        assertEquals(1, fetches)
        // Newest capture first, like the gallery renders it.
        assertEquals(listOf(5, 4, 3), firstScreen.map { it.dbId })
        assertEquals(5, catalog.cachedCount())
    }

    @Test
    fun `filtering by media type is answered from the mirror`() = runTest {
        val store = InMemoryStore()
        val catalog = MediaCatalog(store) { page, _, _ ->
            pageOf(
                listOf(record(1, type = "image"), record(2, type = "video"), record(3, type = "image")),
                page, 1, 3,
            )
        }
        catalog.reconcile()

        assertEquals(2, catalog.cachedCount("image"))
        assertEquals(1, catalog.cachedCount("video"))
        assertEquals(listOf(2), catalog.cached(0, 10, "video").map { it.dbId })
    }
}
