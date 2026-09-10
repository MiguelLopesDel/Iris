package com.iris.app

import com.iris.app.data.local.DeviceAuthStore
import com.iris.app.data.model.UploadInitRequest
import com.iris.app.data.remote.IrisApiClient
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.runBlocking
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Before
import org.junit.Test
import java.net.Socket
import java.security.MessageDigest

/**
 * End-to-End laboratory test suite.
 * Validates IrisApiClient, authentication, token refresh, and upload/sync protocols
 * against a live Iris backend server running on http://127.0.0.1:8000/.
 */
class IrisServerLabTest {

    private val serverBaseUrl = "http://127.0.0.1:8000/"

    @Before
    fun checkServerAvailable() {
        var reachable = false
        try {
            Socket("127.0.0.1", 8000).use {
                reachable = true
            }
        } catch (_: Exception) {
            reachable = false
        }
        assumeTrue("Live Iris server not running on 127.0.0.1:8000, skipping lab test", reachable)
    }

    @Test
    fun test_server_health_check() = runBlocking {
        val client = IrisApiClient(serverBaseUrl)
        val health = client.apiService.getHealth()
        assertEquals("ok", health.status)
        assertEquals("multiuser", health.mode)
    }

    @Test
    fun test_device_login_and_credential_session() = runBlocking {
        val credentials = InMemoryDeviceAuthStore()
        val client = IrisApiClient(serverBaseUrl, credentials)

        val loginResponse = client.apiService.deviceLogin(
            username = "testuser",
            password = "testpassword123",
            deviceName = "LabRunnerPhone",
            platform = "android"
        )

        assertNotNull(loginResponse.accessToken)
        assertTrue(loginResponse.accessToken.isNotBlank())
        assertNotNull(loginResponse.refreshToken)
        assertTrue(loginResponse.refreshToken.isNotBlank())
        assertEquals("testuser", loginResponse.user?.username)

        credentials.saveSession(
            deviceId = loginResponse.deviceId,
            accessToken = loginResponse.accessToken,
            refreshToken = loginResponse.refreshToken,
            serverOrigin = IrisApiClient.getOrigin(serverBaseUrl)
        )

        assertEquals(loginResponse.deviceId, credentials.getDeviceId())
        assertEquals(loginResponse.accessToken, credentials.getAccessToken())
        assertEquals(loginResponse.refreshToken, credentials.getRefreshToken())
    }

    @Test
    fun test_unauthenticated_request_is_rejected_with_401() = runBlocking {
        val credentials = InMemoryDeviceAuthStore()
        val client = IrisApiClient(serverBaseUrl, credentials)

        var failedWith401 = false
        try {
            client.apiService.getChanges(cursor = 0, limit = 10)
        } catch (e: retrofit2.HttpException) {
            if (e.code() == 401) {
                failedWith401 = true
            }
        }
        assertTrue("Protected endpoint should return 401 when unauthenticated", failedWith401)
    }

    @Test
    fun test_authenticated_calls_succeed() = runBlocking {
        val credentials = InMemoryDeviceAuthStore()
        val client = IrisApiClient(serverBaseUrl, credentials)

        val login = client.apiService.deviceLogin(
            username = "testuser",
            password = "testpassword123",
            deviceName = "AuthTestDevice",
            platform = "android"
        )
        credentials.saveSession(
            deviceId = login.deviceId,
            accessToken = login.accessToken,
            refreshToken = login.refreshToken,
            serverOrigin = IrisApiClient.getOrigin(serverBaseUrl)
        )

        val records = client.apiService.getRecords(page = 1, perPage = 24)
        assertNotNull(records)

        val changes = client.apiService.getChanges(cursor = 0, limit = 10)
        assertNotNull(changes)
    }

    @Test
    fun test_automatic_token_refresh_on_401() = runBlocking {
        val credentials = InMemoryDeviceAuthStore()
        val client = IrisApiClient(serverBaseUrl, credentials)

        // Step 1: Perform genuine login
        val login = client.apiService.deviceLogin(
            username = "testuser",
            password = "testpassword123",
            deviceName = "RefreshTestDevice",
            platform = "android"
        )
        val originalRefreshToken = login.refreshToken
        val serverOrigin = IrisApiClient.getOrigin(serverBaseUrl)

        // Step 2: Inject an EXPIRED / INVALID access token, keeping the valid refresh token
        val bogusAccessToken = "expired.bogus.token"
        credentials.saveSession(
            deviceId = login.deviceId,
            accessToken = bogusAccessToken,
            refreshToken = originalRefreshToken,
            serverOrigin = serverOrigin
        )

        assertEquals(bogusAccessToken, credentials.getAccessToken())

        // Step 3: Call protected endpoint. Authenticator must catch 401, refresh, and succeed!
        val changes = client.apiService.getChanges(cursor = 0, limit = 10)
        assertNotNull(changes)

        // Step 4: Verify the token in store was rotated and is no longer bogus
        val freshAccessToken = credentials.getAccessToken()
        assertNotNull(freshAccessToken)
        assertFalse("Access token should have been refreshed", freshAccessToken == bogusAccessToken)
    }

    @Test
    fun test_high_concurrency_token_refresh_storm() = runBlocking {
        val credentials = InMemoryDeviceAuthStore()
        val client = IrisApiClient(serverBaseUrl, credentials)

        val login = client.apiService.deviceLogin(
            username = "testuser",
            password = "testpassword123",
            deviceName = "StormTestDevice",
            platform = "android"
        )
        val serverOrigin = IrisApiClient.getOrigin(serverBaseUrl)

        // Set an invalid access token
        val bogusAccessToken = "expired.bogus.token.storm"
        credentials.saveSession(
            deviceId = login.deviceId,
            accessToken = bogusAccessToken,
            refreshToken = login.refreshToken,
            serverOrigin = serverOrigin
        )

        // Launch 20 concurrent requests simultaneously
        val deferreds = (1..20).map {
            async(Dispatchers.IO) {
                client.apiService.getChanges(cursor = 0, limit = 10)
            }
        }

        val results = deferreds.awaitAll()
        assertEquals(20, results.size)
        results.forEach { assertNotNull(it) }

        // Credentials should have a valid fresh token
        val freshToken = credentials.getAccessToken()
        assertNotNull(freshToken)
        assertFalse(freshToken == bogusAccessToken)
    }

    @Test
    fun test_resumable_chunked_upload_and_change_feed() = runBlocking {
        val credentials = InMemoryDeviceAuthStore()
        val client = IrisApiClient(serverBaseUrl, credentials)

        val login = client.apiService.deviceLogin(
            username = "testuser",
            password = "testpassword123",
            deviceName = "UploadTestDevice",
            platform = "android"
        )
        credentials.saveSession(
            deviceId = login.deviceId,
            accessToken = login.accessToken,
            refreshToken = login.refreshToken,
            serverOrigin = IrisApiClient.getOrigin(serverBaseUrl)
        )

        // Prepare 512 bytes test media content
        val testPayload = ByteArray(512) { (it % 256).toByte() }
        val sha256 = MessageDigest.getInstance("SHA-256").digest(testPayload).joinToString("") { "%02x".format(it) }

        // Step 1: Init upload
        val initResponse = client.apiService.initUpload(
            UploadInitRequest(
                filename = "lab_test_meme.jpg",
                size = testPayload.size.toLong(),
                sha256 = sha256,
                capturedAt = "2026-09-10T04:00:00Z"
            )
        )
        assertNotNull(initResponse.uploadId)

        // Step 2: Check initial upload status
        val statusBefore = client.apiService.getUploadStatus(initResponse.uploadId)
        assertEquals(0L, statusBefore.offset)

        // Step 3: Upload chunk
        val octetStreamMediaType = "application/octet-stream".toMediaType()
        val chunkBody = testPayload.toRequestBody(octetStreamMediaType)
        val uploadChunkResponse = client.apiService.uploadChunk(
            uploadId = initResponse.uploadId,
            offset = 0L,
            body = chunkBody
        )
        assertTrue("Upload chunk response should be successful", uploadChunkResponse.isSuccessful)
        assertEquals(testPayload.size.toLong(), uploadChunkResponse.body()?.offset)

        // Step 4: Complete upload
        val completeResponse = client.apiService.completeUpload(initResponse.uploadId)
        assertNotNull(completeResponse.state)
        assertTrue(
            "State must be ready, duplicate, or pending_processing",
            completeResponse.state in listOf("ready", "duplicate", "pending_processing")
        )

        // Step 5: Check change feed
        val changes = client.apiService.getChanges(cursor = 0, limit = 50)
        assertTrue("Changes response should be available", changes.changes.isNotEmpty() || completeResponse.cursor >= 0)
    }

    private class InMemoryDeviceAuthStore : DeviceAuthStore {
        private var accessToken: String? = null
        private var refreshToken: String? = null
        private var deviceId: String? = null
        private var serverOrigin: String? = null

        fun saveSession(deviceId: String, accessToken: String, refreshToken: String, serverOrigin: String) {
            this.deviceId = deviceId
            this.accessToken = accessToken
            this.refreshToken = refreshToken
            this.serverOrigin = serverOrigin
        }

        override fun getAccessToken(): String? = accessToken
        override fun getRefreshToken(): String? = refreshToken
        override fun getDeviceId(): String? = deviceId
        override fun getServerOrigin(): String? = serverOrigin

        override fun replaceTokensAtomically(accessToken: String, refreshToken: String, expiresInSeconds: Long) {
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
