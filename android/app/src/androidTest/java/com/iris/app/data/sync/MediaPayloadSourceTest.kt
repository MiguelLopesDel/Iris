package com.iris.app.data.sync

import android.net.Uri
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import okio.Buffer
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File
import java.security.MessageDigest

@RunWith(AndroidJUnit4::class)
class MediaPayloadSourceTest {

    @Test
    fun hashesAndStreamsOnlyTheRequestedRange() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val media = File.createTempFile("iris-payload-", ".bin", context.cacheDir)
        try {
            val payload = ByteArray(256 * 1024 + 37) { index -> (index % 251).toByte() }
            media.writeBytes(payload)

            val source = MediaPayloadSource(context.contentResolver)
            val uri = Uri.fromFile(media)
            val expectedHash = MessageDigest.getInstance("SHA-256")
                .digest(payload)
                .joinToString("") { byte -> "%02x".format(byte) }
            assertEquals(expectedHash, source.computeSha256(uri))

            val offset = 65_531
            val length = 131_077
            val sink = Buffer()
            val requestBody = source.createChunkRequestBody(uri, offset.toLong(), length.toLong(), original = false)
            requestBody.writeTo(sink)

            assertEquals(length.toLong(), requestBody.contentLength())
            assertEquals(length.toLong(), sink.size)
            assertArrayEquals(
                payload.copyOfRange(offset, offset + length),
                sink.readByteArray(),
            )
        } finally {
            media.delete()
        }
    }
}
