package com.iris.app.data.sync

import com.iris.app.data.remote.IrisApiClient
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.util.Collections

/** The speed test through the real Retrofit stack against a fake server. */
class ServerSpeedTestTest {
    private lateinit var server: MockWebServer
    private val requests = Collections.synchronizedList(mutableListOf<RecordedRequest>())

    @Before
    fun setUp() {
        server = MockWebServer()
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                requests += request
                val mode = request.requestUrl?.queryParameter("mode")
                return MockResponse()
                    .setHeader("Content-Type", "application/json")
                    .setBody("""{"bytes": ${request.bodySize}, "server_seconds": 0.01, "mode": "$mode"}""")
            }
        }
        server.start()
    }

    @After
    fun tearDown() {
        server.shutdown()
    }

    @Test
    fun runs_single_parallel_and_disk_phases_with_the_declared_bytes() = runBlocking {
        val api = IrisApiClient(server.url("/").toString()).apiService
        var clock = 0L
        val test = ServerSpeedTest(bytesPerPhase = 400_000L, parallelConnections = 4, nowNanos = {
            clock += 100_000_000L // every reading advances 100 ms
            clock
        })
        val seen = mutableListOf<ServerSpeedTest.Phase>()

        val results = test.run(api) { seen += it.phase }

        assertEquals(ServerSpeedTest.Phase.entries.toList(), seen)
        assertEquals(listOf(400_000L, 400_000L, 400_000L), results.map { it.bytes })
        assertTrue(results.all { it.bytesPerSecond > 0.0 })
        // One request, then four in parallel, then one written to disk.
        assertEquals(6, requests.size)
        assertEquals(5, requests.count { it.requestUrl?.queryParameter("mode") == "discard" })
        assertEquals(1, requests.count { it.requestUrl?.queryParameter("mode") == "disk" })
        assertEquals(4, requests.count { it.bodySize == 100_000L })
        assertTrue(requests.all { it.path!!.startsWith("/api/sync/speedtest") && it.method == "POST" })
    }

    @Test
    fun synthetic_body_is_exactly_the_declared_length_and_not_compressible() {
        val body = ServerSpeedTest.syntheticBody(200_000L)
        val buffer = okio.Buffer()
        body.writeTo(buffer)
        assertEquals(200_000L, body.contentLength())
        assertEquals(200_000L, buffer.size)
        val bytes = buffer.readByteArray()
        assertTrue("payload must not be a run of zeros", bytes.distinct().size > 200)
    }
}
