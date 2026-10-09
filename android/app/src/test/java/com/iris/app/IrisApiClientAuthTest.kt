package com.iris.app

import org.junit.Assert.assertTrue

import com.iris.app.data.remote.security.ServerSecurity

import com.iris.app.data.remote.security.ServerOrigin

import com.iris.app.data.remote.security.ServerIdentityVerifier

import com.iris.app.data.remote.security.InMemoryServerSecurityStore

import com.iris.app.data.remote.security.ConnectionSecurity

import com.iris.app.data.local.DeviceAuthStore
import com.iris.app.data.remote.IrisApiClient
import okhttp3.Request
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.tls.HandshakeCertificates
import okhttp3.tls.HeldCertificate
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

    // --- Identity: credentials only for the server holding the paired key ----------

    private fun ecKeys() = java.security.KeyPairGenerator.getInstance("EC")
        .apply { initialize(java.security.spec.ECGenParameterSpec("secp256r1")) }.generateKeyPair()

    /** Answers identity challenges with [keys]; everything else with 200. */
    private fun answerIdentityWith(keys: java.security.KeyPair, instance: String = "a".repeat(32)) {
        server.dispatcher = object : okhttp3.mockwebserver.Dispatcher() {
            override fun dispatch(request: okhttp3.mockwebserver.RecordedRequest): MockResponse {
                if (request.requestUrl?.encodedPath != "/api/identity") return MockResponse().setBody("{}")
                val nonce = request.requestUrl!!.queryParameter("nonce")!!
                val address = request.requestUrl!!.queryParameter("address")!!
                val signature = java.security.Signature.getInstance("SHA256withECDSA").run {
                    initSign(keys.private)
                    update(ServerIdentityVerifier.message(instance, nonce, address))
                    sign()
                }
                val b64 = java.util.Base64.getEncoder()
                return MockResponse().setBody(
                    """{"version":1,"algorithm":"ecdsa-p256-sha256","instance_id":"$instance",""" +
                        """"public_key":"${b64.encodeToString(keys.public.encoded)}",""" +
                        """"key_sha256":"${ConnectionSecurity.keySha256(keys.public.encoded)}",""" +
                        """"signature":"${b64.encodeToString(signature)}"}"""
                )
            }
        }
    }

    /** A client paired with [pairedKeys] for this server over HTTP. */
    private fun pairedClient(pairedKeys: java.security.KeyPair): IrisApiClient {
        val store = InMemoryServerSecurityStore()
        store.put(
            ServerOrigin.of(server.url("/")),
            ServerSecurity(cleartextAllowed = true, identityKeySha256 = ConnectionSecurity.keySha256(pairedKeys.public.encoded)),
        )
        val security = ConnectionSecurity(store, deviceCaStore = { null })
        return IrisApiClient(server.url("/").toString(), FakeCredentials(accessToken = "private-token"), connectionSecurity = security)
    }

    private fun serveHttpsWithCertificateKey(keys: java.security.KeyPair) {
        server.shutdown()
        server = MockWebServer()
        val certificate = HeldCertificate.Builder()
            .keyPair(keys)
            .commonName("Iris")
            .addSubjectAlternativeName("localhost")
            .build()
        server.useHttps(HandshakeCertificates.Builder().heldCertificate(certificate).build().sslSocketFactory(), false)
        server.start()
    }

    private fun recordedPaths(): List<Pair<String, String?>> =
        generateSequence { server.takeRequest(200, java.util.concurrent.TimeUnit.MILLISECONDS) }
            .map { (it.requestUrl?.encodedPath ?: "") to it.getHeader("Authorization") }.toList()

    @Test
    fun an_identity_paired_server_over_http_never_receives_credentials() {
        val keys = ecKeys()
        answerIdentityWith(keys)
        val client = pairedClient(keys)

        client.authenticatedOkHttpClient.newCall(Request.Builder().url(server.url("/api/records/1")).build())
            .execute().use { assertEquals(IrisApiClient.HTTPS_REQUIRED_CODE, it.code) }

        val login = Request.Builder().url(server.url("/api/auth/devices/login"))
            .post(okhttp3.FormBody.Builder().add("password", "synthetic password").build()).build()
        client.authenticatedOkHttpClient.newCall(login).execute().use {
            assertEquals(IrisApiClient.HTTPS_REQUIRED_CODE, it.code)
        }
        assertTrue("HTTP must not even receive an identity challenge", recordedPaths().isEmpty())
    }

    @Test
    fun the_paired_https_server_proves_its_key_and_then_gets_the_token() {
        val keys = ecKeys()
        serveHttpsWithCertificateKey(keys)
        answerIdentityWith(keys)
        val client = pairedClient(keys)

        client.authenticatedOkHttpClient.newCall(Request.Builder().url(server.url("/api/records/1")).build())
            .execute().use { assertEquals(200, it.code) }

        val requests = recordedPaths()
        assertEquals("/api/identity", requests.first().first)
        assertNull("the identity check carries no credential", requests.first().second)
        assertEquals("/api/records/1" to "Bearer private-token", requests.last())
    }

    @Test
    fun an_impostor_at_the_paired_address_gets_neither_token_nor_password() {
        val pairedKeys = ecKeys()
        serveHttpsWithCertificateKey(pairedKeys)
        answerIdentityWith(ecKeys()) // another machine, with a key of its own
        val client = pairedClient(pairedKeys)

        client.authenticatedOkHttpClient.newCall(Request.Builder().url(server.url("/api/records/1")).build())
            .execute().use { assertEquals(IrisApiClient.IDENTITY_MISMATCH_CODE, it.code) }
        val login = Request.Builder().url(server.url("/api/auth/devices/login"))
            .post(okhttp3.FormBody.Builder().add("password", "synthetic password").build()).build()
        client.authenticatedOkHttpClient.newCall(login).execute().use {
            assertEquals(IrisApiClient.IDENTITY_MISMATCH_CODE, it.code)
        }

        // Only identity challenges reached it; no record request, no login, no token.
        assertTrue(recordedPaths().all { (path, auth) -> path == "/api/identity" && auth == null })
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
