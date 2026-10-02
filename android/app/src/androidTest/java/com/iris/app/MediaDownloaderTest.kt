package com.iris.app

import android.os.Build
import android.os.Environment
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.iris.app.data.MediaDownloader
import com.iris.app.data.remote.IrisApiClient
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File

/**
 * "Baixar" on the oldest supported Android. Before scoped storage (API 29) the
 * public Downloads folder needs a runtime grant; without it the download must
 * be refused with a clear reason, and with it the file must really land there.
 *
 * The two states are set from outside, between runs (`pm revoke` / `pm grant`
 * with the app stopped): granting from inside the process under test does not
 * reproduce what the system does when a person taps "Permitir".
 */
@RunWith(AndroidJUnit4::class)
class MediaDownloaderTest {

    private lateinit var server: MockWebServer
    private lateinit var app: IrisApplication
    private val created = mutableListOf<File>()

    @Before
    fun setUp() {
        server = MockWebServer()
        server.start()
        app = InstrumentationRegistry.getInstrumentation().targetContext.applicationContext as IrisApplication
        // The app applies its saved server URL asynchronously at startup; set
        // ours only after that, or it can land later and point the client at
        // a stale port, which then (correctly) withholds the token.
        runBlocking { withTimeout(10_000) { app.isServerConfigurationReady.first { it } } }
        val serverUrl = server.url("/").toString()
        app.apiClient.updateBaseUrl(serverUrl)
        app.allowCleartext(server)
        app.credentialsStore.saveSession(
            deviceId = "instrumentation-device",
            accessToken = "download-token",
            refreshToken = "refresh-token",
            expiresInSeconds = 3600,
            username = "instrumentation",
            serverOrigin = IrisApiClient.getOrigin(serverUrl)
        )
    }

    @After
    fun tearDown() {
        created.forEach { it.delete() }
        app.credentialsStore.clearCredentials()
        app.allowCleartext(server, allowed = false)
        server.shutdown()
    }

    private fun enqueuePhoto() {
        server.enqueue(
            MockResponse().setResponseCode(200)
                .setHeader("Content-Type", "image/jpeg")
                .setBody("jpeg-bytes")
        )
    }

    private val url get() = server.url("/media/private-photo.jpg").toString()

    /** Run with the permission revoked (a fresh install, before "Permitir"). */
    @Test
    fun without_the_grant_old_android_refuses_before_any_request() = runBlocking {
        assumeTrue(Build.VERSION.SDK_INT < Build.VERSION_CODES.Q)
        assumeTrue(MediaDownloader.missingPermissions(app).isNotEmpty())

        val refused = MediaDownloader(app, app.apiClient).download(url, "iris-test.jpg")
        assertTrue(refused.toString(), refused is MediaDownloader.Result.Failed)
        assertEquals("no request without the permission", 0, server.requestCount)
    }

    /** Run with the permission granted: always the case from Android 10 on. */
    @Test
    fun with_the_grant_the_photo_lands_in_downloads() = runBlocking {
        assumeTrue(MediaDownloader.missingPermissions(app).isEmpty())
        enqueuePhoto()
        val name = "iris-test-${System.nanoTime()}.jpg"

        val saved = MediaDownloader(app, app.apiClient).download(url, name)

        assertTrue(saved.toString(), saved is MediaDownloader.Result.Saved)
        val request = server.takeRequest()
        assertEquals(
            "${request.path} ${request.headers}",
            "Bearer download-token",
            request.getHeader("Authorization"),
        )
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) {
            @Suppress("DEPRECATION")
            val file = File(
                Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS),
                (saved as MediaDownloader.Result.Saved).displayName
            )
            created += file
            assertEquals("jpeg-bytes", file.readText())
        }
    }
}
