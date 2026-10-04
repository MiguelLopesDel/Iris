package com.iris.app.ui.screens.gallery

import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.iris.app.IrisApplication
import com.iris.app.allowCleartext
import com.iris.app.data.remote.IrisApiClient
import com.iris.app.ui.screens.sync.PickerTestActivity
import com.iris.app.ui.theme.IrisTheme
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import org.junit.After
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * The phone's case: a session from before a server reinstall. The gallery
 * first says the server has no account; once the account is created on the
 * server, the text changes without restarting the app.
 */
@RunWith(AndroidJUnit4::class)
class ServerSetupNoticeTest {

    @get:Rule
    val compose = createAndroidComposeRule<PickerTestActivity>()

    private lateinit var server: MockWebServer
    private lateinit var app: IrisApplication
    @Volatile private var status = "setup_required"

    @Before
    fun setUp() {
        server = MockWebServer()
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse =
                if (request.path == "/healthz") {
                    MockResponse().setHeader("Content-Type", "application/json")
                        .setBody("""{"status":"$status","mode":"multiuser","instance_id":"${"c".repeat(32)}"}""")
                } else {
                    MockResponse().setResponseCode(503)
                }
        }
        server.start()
        app = compose.activity.application as IrisApplication
        runBlocking { app.settingsRepository.updateServerUrl(server.url("/").toString()) }
        app.apiClient.updateBaseUrl(server.url("/").toString())
        app.allowCleartext(server)
        app.credentialsStore.saveSession(
            deviceId = "old-device",
            accessToken = "old-token",
            refreshToken = "old-refresh",
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
    fun the_notice_follows_the_server_when_its_first_account_is_created() {
        val viewModel = GalleryViewModel(app.irisRepository, app.performanceMonitor, app.galleryDataSource, app.settingsRepository)
        compose.setContent {
            IrisTheme { GalleryScreen(viewModel = viewModel, onMediaClick = {}, onSettingsClick = {}) }
        }

        compose.waitUntil(10_000) {
            compose.onAllNodes(hasText("ainda não tem contas", substring = true)).fetchSemanticsNodes().isNotEmpty()
        }

        status = "ok" // the administrator account was created on the server

        compose.waitUntil(SETUP_RECHECK_MILLIS * 3) {
            compose.onAllNodes(hasText("Entre com a conta nova", substring = true)).fetchSemanticsNodes().isNotEmpty()
        }
        compose.waitUntil(1_000) {
            compose.onAllNodes(hasText("ainda não tem contas", substring = true)).fetchSemanticsNodes().isEmpty()
        }
    }
}
