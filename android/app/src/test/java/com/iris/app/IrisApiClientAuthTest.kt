package com.iris.app

import com.iris.app.data.local.DeviceAuthStore
import com.iris.app.data.remote.IrisApiClient
import okhttp3.Request
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Before
import org.junit.Test
import kotlinx.coroutines.runBlocking

class IrisApiClientAuthTest {

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

    @Test
    fun authenticated_client_sends_bearer_token_for_private_media() {
        val credentials = FakeCredentials(accessToken = "preview-token")
        val client = IrisApiClient(server.url("/").toString(), credentials)
        server.enqueue(MockResponse().setResponseCode(200).setBody("thumbnail"))

        client.authenticatedOkHttpClient.newCall(
            Request.Builder().url(server.url("/thumbs/example.jpg")).build()
        ).execute().use { response ->
            assertEquals(200, response.code)
        }

        assertEquals("Bearer preview-token", server.takeRequest().getHeader("Authorization"))
    }

    @Test
    fun authenticated_client_does_not_send_token_to_device_login() {
        val credentials = FakeCredentials(accessToken = "private-token")
        val client = IrisApiClient(server.url("/").toString(), credentials)
        server.enqueue(MockResponse().setResponseCode(200).setBody("{}"))

        client.authenticatedOkHttpClient.newCall(
            Request.Builder().url(server.url("/api/auth/devices/login")).build()
        ).execute().close()

        assertNull(server.takeRequest().getHeader("Authorization"))
    }

    @Test
    fun authenticated_client_does_not_leak_token_to_foreign_server() {
        val foreignServer = MockWebServer()
        foreignServer.start()
        try {
            val credentials = FakeCredentials(accessToken = "private-token")
            val client = IrisApiClient(server.url("/").toString(), credentials)
            foreignServer.enqueue(MockResponse().setResponseCode(200).setBody("foreign"))

            client.authenticatedOkHttpClient.newCall(
                Request.Builder().url(foreignServer.url("/api/records")).build()
            ).execute().close()

            assertNull(foreignServer.takeRequest().getHeader("Authorization"))
        } finally {
            foreignServer.shutdown()
        }
    }

    @Test
    fun authenticated_client_does_not_send_token_if_server_origin_mismatched() {
        val credentials = FakeCredentials(
            accessToken = "private-token",
            serverOrigin = "http://mismatch-host:9999"
        )
        val client = IrisApiClient(server.url("/").toString(), credentials)
        server.enqueue(MockResponse().setResponseCode(200).setBody("ok"))

        client.authenticatedOkHttpClient.newCall(
            Request.Builder().url(server.url("/api/records")).build()
        ).execute().close()

        assertNull(server.takeRequest().getHeader("Authorization"))
    }

    @Test
    fun updateBaseUrl_clears_credentials_when_origin_changes() {
        val currentOrigin = IrisApiClient.getOrigin(server.url("/").toString())
        val credentials = FakeCredentials(
            accessToken = "private-token",
            serverOrigin = currentOrigin
        )
        val client = IrisApiClient(server.url("/").toString(), credentials)
        assertEquals("private-token", credentials.getAccessToken())

        // Switch to different origin
        client.updateBaseUrl("http://192.168.1.100:8000/")
        assertNull(credentials.getAccessToken())
    }

    @Test
    fun authenticated_client_converts_html_login_redirect_to_json_401() {
        val credentials = FakeCredentials(accessToken = "private-token")
        val client = IrisApiClient(server.url("/").toString(), credentials)
        server.enqueue(
            MockResponse()
                .setResponseCode(303)
                .setHeader("Location", "/login")
                .setBody("<html>login</html>")
        )

        client.authenticatedOkHttpClient.newCall(
            Request.Builder().url(server.url("/api/records")).build()
        ).execute().use { response ->
            assertEquals(401, response.code)
            assertEquals("{\"detail\":\"Autenticação necessária\"}", response.body?.string())
        }
    }

    @Test
    fun record_detail_accepts_object_collection_and_concept_memberships() = runBlocking {
        val client = IrisApiClient(server.url("/").toString())
        server.enqueue(
            MockResponse()
                .setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody(
                    """{
                    "index":7,
                    "arquivo":"private-photo.jpg",
                    "collections":[{"id":24,"name":"Family photos"}],
                    "concepts":[{"id":8,"name":"Beach","category":"place","confirmed":true}]
                    }""".trimIndent()
                )
        )

        val record = client.apiService.getRecordDetail(7)

        assertEquals("Family photos", record.collections.single().name)
        assertEquals("Beach", record.concepts.single().name)
        assertEquals(true, record.concepts.single().confirmed)
    }

    @Test
    fun collection_members_accept_server_records_contract() = runBlocking {
        val client = IrisApiClient(server.url("/").toString())
        server.enqueue(
            MockResponse()
                .setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody(
                    """{
                    "db_ids":[7],
                    "records":[{"index":7,"arquivo":"family-photo.jpg"}]
                    }""".trimIndent()
                )
        )

        val response = client.apiService.getCollectionMembers(24)

        assertEquals(7, response.records.single().index)
    }

    private class FakeCredentials(
        private var accessToken: String? = null,
        private var refreshToken: String? = "refresh-token",
        private var deviceId: String? = "device-id",
        private var serverOrigin: String? = null
    ) : DeviceAuthStore {
        override fun getAccessToken(): String? = accessToken
        override fun getRefreshToken(): String? = refreshToken
        override fun getDeviceId(): String? = deviceId
        override fun getServerOrigin(): String? = serverOrigin

        override fun replaceTokensAtomically(
            accessToken: String,
            refreshToken: String,
            expiresInSeconds: Long
        ) {
            this.accessToken = accessToken
            this.refreshToken = refreshToken
        }

        override fun clearCredentials() {
            accessToken = null
            refreshToken = null
            deviceId = null
            serverOrigin = null
        }
    }
}
