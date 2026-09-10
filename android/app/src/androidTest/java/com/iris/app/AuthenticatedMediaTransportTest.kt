package com.iris.app

import android.net.Uri
import android.graphics.Bitmap
import android.graphics.Color
import androidx.media3.datasource.DataSpec
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.iris.app.data.remote.IrisApiClient
import com.iris.app.data.remote.IrisMediaDataSourceFactory
import coil.request.ImageRequest
import coil.request.SuccessResult
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okio.Buffer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith

/**
 * Runs on an emulator. It proves Media3 uses the same authenticated client as
 * the JSON API, so private video playback cannot silently regress to 401.
 */
@RunWith(AndroidJUnit4::class)
class AuthenticatedMediaTransportTest {

    private lateinit var server: MockWebServer
    private lateinit var app: IrisApplication

    @Before
    fun setUp() {
        server = MockWebServer()
        server.start()
        app = InstrumentationRegistry.getInstrumentation().targetContext.applicationContext as IrisApplication
        val serverUrl = server.url("/").toString()
        app.apiClient.updateBaseUrl(serverUrl)
        app.credentialsStore.saveSession(
            deviceId = "instrumentation-device",
            accessToken = "video-token",
            refreshToken = "refresh-token",
            expiresInSeconds = 3600,
            username = "instrumentation",
            serverOrigin = IrisApiClient.getOrigin(serverUrl)
        )
    }

    @After
    fun tearDown() {
        app.credentialsStore.clearCredentials()
        server.shutdown()
    }

    @Test
    fun media3_sends_bearer_token_for_private_video() {
        server.enqueue(MockResponse().setResponseCode(200).setBody("tiny-video-payload"))
        val dataSource = IrisMediaDataSourceFactory(app.apiClient.authenticatedOkHttpClient).createDataSource()
        val spec = DataSpec(Uri.parse(server.url("/media/private-video.mp4").toString()))

        dataSource.open(spec)
        val buffer = ByteArray(32)
        dataSource.read(buffer, 0, buffer.size)
        dataSource.close()

        assertEquals("Bearer video-token", server.takeRequest().getHeader("Authorization"))
    }

    @Test
    fun coil_sends_bearer_token_for_private_thumbnail() = runBlocking {
        server.enqueue(
            MockResponse()
                .setResponseCode(200)
                .setHeader("Content-Type", "image/png")
                .setBody(Buffer().write(tinyPng()))
        )
        val request = ImageRequest.Builder(app)
            .data(server.url("/thumbs/private-photo.png").toString())
            .build()

        val result = app.newImageLoader().execute(request)

        assertEquals(SuccessResult::class, result::class)
        assertEquals("Bearer video-token", server.takeRequest().getHeader("Authorization"))
    }

    private fun tinyPng(): ByteArray {
        val bitmap = Bitmap.createBitmap(1, 1, Bitmap.Config.ARGB_8888)
        bitmap.eraseColor(Color.MAGENTA)
        return java.io.ByteArrayOutputStream().use { output ->
            bitmap.compress(Bitmap.CompressFormat.PNG, 100, output)
            output.toByteArray()
        }
    }
}
