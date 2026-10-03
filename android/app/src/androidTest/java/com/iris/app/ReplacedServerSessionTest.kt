package com.iris.app

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.iris.app.data.remote.IrisApiClient
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith

/** A server reinstalled at the same address must end the phone's old session. */
@RunWith(AndroidJUnit4::class)
class ReplacedServerSessionTest {

    private lateinit var server: MockWebServer
    private lateinit var app: IrisApplication
    @Volatile private var health = """{"status":"ok","mode":"multiuser","instance_id":"${"a".repeat(32)}"}"""

    @Before
    fun setUp() {
        server = MockWebServer()
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse =
                if (request.path == "/healthz") {
                    MockResponse().setHeader("Content-Type", "application/json").setBody(health)
                } else {
                    MockResponse().setResponseCode(503)
                }
        }
        server.start()
        app = InstrumentationRegistry.getInstrumentation().targetContext.applicationContext as IrisApplication
        runBlocking { app.settingsRepository.updateServerUrl(server.url("/").toString()) }
        app.apiClient.updateBaseUrl(server.url("/").toString())
        app.allowCleartext(server)
        app.credentialsStore.saveSession(
            deviceId = "replaced-device",
            accessToken = "replaced-token",
            refreshToken = "replaced-refresh",
            expiresInSeconds = 3600,
            username = "miguel",
            serverOrigin = IrisApiClient.getOrigin(server.url("/").toString()),
            userId = 1,
        )
    }

    @After
    fun tearDown() {
        app.credentialsStore.clearCredentials()
        app.allowCleartext(server, allowed = false)
        server.shutdown()
    }

    @Test
    fun the_first_check_records_the_installation_and_keeps_the_session() = runBlocking {
        app.irisRepository.checkServerHealth()

        assertNotNull(app.credentialsStore.sessionIdentity.value)
        assertEquals("a".repeat(32), app.credentialsStore.serverInstanceId())
        assertNull(app.credentialsStore.signOutReason.value)
    }

    @Test
    fun a_reinstalled_server_ends_the_session_and_says_why() = runBlocking {
        app.irisRepository.checkServerHealth()
        health = """{"status":"ok","mode":"multiuser","instance_id":"${"b".repeat(32)}"}"""

        app.irisRepository.checkServerHealth()

        assertNull(app.credentialsStore.sessionIdentity.value)
        assertNotNull(app.credentialsStore.signOutReason.value)
    }

    @Test
    fun a_server_asking_for_its_first_account_ends_any_session() = runBlocking {
        // The phone's case: a session from before the check, against a wiped server.
        health = """{"status":"setup_required","mode":"multiuser","instance_id":"${"c".repeat(32)}"}"""

        app.irisRepository.checkServerHealth()

        assertNull(app.credentialsStore.sessionIdentity.value)
        assertNotNull(app.credentialsStore.signOutReason.value)
        // A new login clears the reason.
        app.credentialsStore.saveSession("d", "t", "r", 3600, "miguel", "", 1)
        assertNull(app.credentialsStore.signOutReason.value)
    }
}
