package com.iris.app.data.sync

import android.content.ContentResolver
import android.net.Uri
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody
import okio.BufferedSink
import java.io.FileInputStream
import java.io.InputStream
import java.security.MessageDigest
import kotlin.math.min

/** Reads media bytes from a provider with bounded memory for hashing and upload. */
internal class MediaPayloadSource(private val contentResolver: ContentResolver) {

    fun createChunkRequestBody(uri: Uri, offset: Long, length: Long): RequestBody =
        object : RequestBody() {
            override fun contentType() = "application/octet-stream".toMediaType()
            override fun contentLength(): Long = length
            override fun isOneShot(): Boolean = false

            override fun writeTo(sink: BufferedSink) {
                val descriptor = openFileDescriptor(uri)
                if (descriptor != null) {
                    descriptor.use { fileDescriptor ->
                        FileInputStream(fileDescriptor.fileDescriptor).use { stream ->
                            stream.channel.position(offset)
                            copyRange(stream, sink, length)
                        }
                    }
                    return
                }

                contentResolver.openInputStream(uri)?.use { stream ->
                    skipFully(stream, offset)
                    copyRange(stream, sink, length)
                } ?: throw java.io.IOException("Não foi possível abrir o arquivo da mídia local")
            }
        }

    fun computeSha256(uri: Uri): String {
        val digest = MessageDigest.getInstance("SHA-256")
        val descriptor = openFileDescriptor(uri)
        if (descriptor != null) {
            descriptor.use { fileDescriptor ->
                FileInputStream(fileDescriptor.fileDescriptor).use { stream ->
                    updateDigest(stream, digest)
                }
            }
        } else {
            contentResolver.openInputStream(uri)?.use { stream ->
                updateDigest(stream, digest)
            } ?: return ""
        }
        return digest.digest().toHex()
    }

    /**
     * False when the media is gone from the device: deleted, or its volume
     * removed. A permission problem is not treated as absence, because it is
     * fixed by granting access, not by giving up on the item.
     */
    fun isAvailable(uri: Uri): Boolean = try {
        contentResolver.openFileDescriptor(uri, "r")?.use { true } ?: false
    } catch (_: java.io.FileNotFoundException) {
        false
    } catch (_: Exception) {
        // Not proof of deletion (a permission or provider problem); keep the item queued.
        true
    }

    private fun openFileDescriptor(uri: Uri) = try {
        contentResolver.openFileDescriptor(uri, "r")
    } catch (_: Exception) {
        null
    }

    private fun copyRange(stream: InputStream, sink: BufferedSink, length: Long) {
        val buffer = ByteArray(BUFFER_SIZE_BYTES)
        var remaining = length
        while (remaining > 0) {
            val toRead = min(remaining, buffer.size.toLong()).toInt()
            val read = stream.read(buffer, 0, toRead)
            if (read == -1) break
            sink.write(buffer, 0, read)
            remaining -= read
        }
    }

    private fun skipFully(stream: InputStream, bytesToSkip: Long) {
        var remaining = bytesToSkip
        while (remaining > 0) {
            val skipped = stream.skip(remaining)
            if (skipped <= 0) {
                if (stream.read() == -1) break
                remaining -= 1
            } else {
                remaining -= skipped
            }
        }
    }

    private fun updateDigest(stream: InputStream, digest: MessageDigest) {
        val buffer = ByteArray(BUFFER_SIZE_BYTES)
        var count: Int
        while (stream.read(buffer).also { count = it } != -1) {
            digest.update(buffer, 0, count)
        }
    }

    private fun ByteArray.toHex(): String = joinToString("") { byte -> "%02x".format(byte) }

    private companion object {
        const val BUFFER_SIZE_BYTES = 64 * 1024
    }
}
