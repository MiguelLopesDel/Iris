package com.iris.app

import com.iris.app.data.model.CollectionsResponse
import com.iris.app.data.model.ConceptsResponse
import com.iris.app.data.model.PersonsResponse
import com.iris.app.data.model.RecordsResponse
import com.iris.app.data.model.ServerInfo
import com.iris.app.data.remote.IrisApiClient
import com.iris.app.ui.components.decodeThumbGrid
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Decodes responses produced by the real server instead of by hand.
 *
 * The fixtures under `src/test/resources/fixtures/` are written by
 * `tests/test_android_contract_fixtures.py` in this same repo: a real SQLite
 * catalog goes through the real backend and the real routes, and the output is
 * committed here. Regenerate with:
 *
 *     IRIS_UPDATE_FIXTURES=1 pytest tests/test_android_contract_fixtures.py
 *
 * That pytest fails when the server's shape drifts from what is committed, so a
 * change on the server surfaces in review on the Android side — which is what
 * hand-written fixtures could never do, since they only ever agreed with the
 * assumptions of whoever wrote the model.
 */
class ServerContractTest {

    private val json = IrisApiClient.RESPONSE_JSON

    private fun fixture(name: String): String =
        checkNotNull(javaClass.classLoader?.getResourceAsStream("fixtures/$name")) {
            "Fixture ausente: fixtures/$name — rode o gerador em pytest."
        }.bufferedReader().readText()

    @Test
    fun `records response from the real server decodes`() {
        val decoded = json.decodeFromString<RecordsResponse>(fixture("records.json"))

        assertEquals(2, decoded.total)
        assertEquals(1, decoded.page)
        assertEquals(2, decoded.records.size)
    }

    @Test
    fun `persons response decodes with an unnamed person present`() {
        val decoded = json.decodeFromString<PersonsResponse>(fixture("persons.json"))

        assertEquals(2, decoded.persons.size)
        // The unnamed one is the whole point: this exact payload used to fail
        // the decode of the entire response and blank the Persons tab.
        assertTrue(decoded.persons.any { it.name.isEmpty() })
        assertTrue(decoded.persons.any { it.name == "Fulano de Tal" })
    }

    @Test
    fun `placeholder in a real record decodes into a usable grid`() {
        val decoded = json.decodeFromString<RecordsResponse>(fixture("records.json"))

        val withPlaceholder = decoded.records.first { !it.thumbHash.isNullOrEmpty() }
        val grid = decodeThumbGrid(withPlaceholder.thumbHash)

        assertNotNull("O placeholder do servidor não decodificou", grid)
        assertEquals(grid!!.side * grid.side, grid.pixels.size)
    }

    @Test
    fun `a record with no placeholder is tolerated`() {
        val decoded = json.decodeFromString<RecordsResponse>(fixture("records.json"))

        // Catalogs indexed before the placeholder existed, or not yet
        // backfilled, ship an empty string here.
        val withoutPlaceholder = decoded.records.first { it.thumbHash.isNullOrEmpty() }
        assertNull(decodeThumbGrid(withoutPlaceholder.thumbHash))
    }

    @Test
    fun `collections response decodes, accents intact`() {
        val decoded = json.decodeFromString<CollectionsResponse>(fixture("collections.json"))

        assertEquals(2, decoded.collections.size)
        assertTrue(decoded.collections.any { it.name == "Memórias de férias" })
    }

    @Test
    fun `concepts response decodes when empty`() {
        val decoded = json.decodeFromString<ConceptsResponse>(fixture("concepts.json"))

        assertTrue(decoded.concepts.isEmpty())
    }

    @Test
    fun `server info decodes`() {
        val decoded = json.decodeFromString<ServerInfo>(fixture("info.json"))

        assertEquals(2, decoded.totalRecords)
    }
}
