package com.iris.app

import com.iris.app.data.remote.IrisApiClient
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * Serves the captured server fixtures over real HTTP and checks each screen's
 * data actually arrives — the automation of tapping through the tabs to see
 * whether they render content or an error.
 *
 * This is the transport level rather than the rendered pixels: the composables
 * reach into the IrisApplication singleton for their api client
 * (`context.applicationContext as IrisApplication`), so rendering them needs
 * the whole application object stood up. The defect class this guards against
 * — the Persons tab showing a decode error instead of a list — lands in the
 * response before anything is drawn, so it is catchable here, in milliseconds,
 * with the real Retrofit stack and the real Json configuration.
 *
 * If those screens ever take their client as a parameter, this can grow into
 * rendering assertions without changing what it covers.
 */
class ScreenDataSmokeTest {

    private lateinit var server: MockWebServer

    @Before
    fun startServer() {
        server = MockWebServer()
        server.start()
    }

    @After
    fun stopServer() {
        server.shutdown()
    }

    private fun fixture(name: String): String =
        checkNotNull(javaClass.classLoader?.getResourceAsStream("fixtures/$name")) {
            "Fixture ausente: fixtures/$name"
        }.bufferedReader().readText()

    private fun serve(name: String): IrisApiClient {
        server.enqueue(
            MockResponse()
                .setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody(fixture(name))
        )
        return IrisApiClient(server.url("/").toString())
    }

    @Test
    fun `persons screen receives its list instead of a decode error`() = runBlocking {
        val persons = serve("persons.json").apiService.getPersons().persons

        // The tab rendered nothing but a decode error in production with this
        // exact payload, because one of these has no name.
        assertEquals(2, persons.size)
        assertTrue(persons.any { it.name.isEmpty() })
    }

    @Test
    fun `gallery screen receives a page of records`() = runBlocking {
        val page = serve("records.json").apiService.getRecords()

        assertEquals(2, page.records.size)
        assertEquals(1, page.page)
        assertTrue(page.records.all { it.arquivo.isNotBlank() })
    }

    @Test
    fun `albums screen receives its collections`() = runBlocking {
        val collections = serve("collections.json").apiService.getCollections().collections

        assertEquals(2, collections.size)
        assertTrue(collections.all { it.name.isNotBlank() })
    }

    @Test
    fun `album contents arrive through the records field`() = runBlocking {
        // The server answers with `records`; `members` is the older shape kept
        // as a fallback. Reading the wrong one yields a silently empty album.
        val response = serve("collection_members.json").apiService.getCollectionMembers(1)

        assertTrue(response.records.isNotEmpty())
    }

    @Test
    fun `person media screen receives that person's results`() = runBlocking {
        val response = serve("person_media.json").apiService.getPersonMedia(1)

        assertEquals(1, response.personId)
        assertTrue(response.results.isNotEmpty())
    }

    @Test
    fun `settings screen receives server info`() = runBlocking {
        val info = serve("info.json").apiService.getInfo()

        assertEquals(2, info.totalRecords)
    }

    @Test
    fun `concepts arrive even when the catalog has none`() = runBlocking {
        val concepts = serve("concepts.json").apiService.getConcepts().concepts

        assertTrue(concepts.isEmpty())
    }
}
