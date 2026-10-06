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
internal class MediaPayloadSource(
    private val contentResolver: ContentResolver,
    /** Whether originals (with location metadata) may be opened; see [MediaLocationAccess]. */
    private val canReadOriginals: () -> Boolean = { false },
) {

    /** Original vs redacted file, per item; see [OriginalReads]. */
    private val originals = OriginalReads(canReadOriginals)

    private fun originalOf(uri: Uri): Uri = MediaLocationAccess.originalOf(uri, granted = true)

    /**
     * The upload body for one chunk, read from exactly the version the job was
     * hashed from ([original]): the original, or the redacted file. When the
     * original cannot be read the request fails with OriginalRefusedException.
     */
    fun createChunkRequestBody(uri: Uri, offset: Long, length: Long, original: Boolean): RequestBody =
        object : RequestBody() {
            override fun contentType() = "application/octet-stream".toMediaType()
            override fun contentLength(): Long = length
            override fun isOneShot(): Boolean = false

            override fun writeTo(sink: BufferedSink) {
                originals.openExact(uri, ::originalOf, original) { target ->
                    val descriptor = openDescriptor(target)
                    if (descriptor != null) {
                        descriptor.use { fileDescriptor ->
                            FileInputStream(fileDescriptor.fileDescriptor).use { stream ->
                                stream.channel.position(offset)
                                copyRange(stream, sink, length)
                            }
                        }
                    } else {
                        contentResolver.openInputStream(target)?.use { stream ->
                            skipFully(stream, offset)
                            copyRange(stream, sink, length)
                        } ?: throw java.io.IOException("Não foi possível abrir o arquivo da mídia local")
                    }
                }
            }
        }

    fun computeSha256(uri: Uri): String = computeContent(uri).sha256

    /**
     * The hash and size of the bytes as they are read, which is what an
     * upload sends, and whether they are the original file (with location)
     * or the redacted one. The job records that version and its chunks read
     * the same one.
     */
    data class Content(
        val sha256: String,
        val size: Long,
        val original: Boolean = false,
        /** Whether location access was granted while hashing: it decides the bytes any read returns. */
        val withLocation: Boolean = false,
    )

    /**
     * Hashes the preferred version of the file and counts its bytes in one
     * read. The count, not MediaStore's SIZE, is what the upload declares: an
     * app can rewrite a file without MediaStore noticing, and its SIZE then
     * trails the real file.
     */
    fun computeContent(uri: Uri): Content {
        val withLocation = locationAccessGranted()
        val (hashed, original) = originals.openPreferred(uri, ::originalOf) { target -> hashOf(target) }
        return Content(hashed.first, hashed.second, original, withLocation)
    }

    /** Whether reads return photos with their location now; see [ResumableUploadTransfer]. */
    fun locationAccessGranted(): Boolean = canReadOriginals()

    private fun hashOf(target: Uri): Pair<String, Long> {
        val digest = MessageDigest.getInstance("SHA-256")
        val descriptor = openDescriptor(target)
        val size = if (descriptor != null) {
            descriptor.use { fileDescriptor ->
                FileInputStream(fileDescriptor.fileDescriptor).use { stream -> updateDigest(stream, digest) }
            }
        } else {
            contentResolver.openInputStream(target)?.use { stream -> updateDigest(stream, digest) }
                ?: return "" to 0L
        }
        return digest.digest().toHex() to size
    }

    /** The file's current size without reading it, or null when unknown. Both versions have the same size. */
    fun sizeOf(uri: Uri): Long? = try {
        contentResolver.openFileDescriptor(uri, "r")?.use { it.statSize.takeIf { size -> size >= 0 } }
    } catch (_: Exception) {
        null
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

    /**
     * A descriptor for exactly [target], or null to read it as a stream. A
     * refusal (SecurityException) propagates: falling back to a stream of the
     * other version would mix them.
     */
    private fun openDescriptor(target: Uri) = try {
        contentResolver.openFileDescriptor(target, "r")
    } catch (refused: SecurityException) {
        throw refused
    } catch (refused: UnsupportedOperationException) {
        throw refused
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

    private fun updateDigest(stream: InputStream, digest: MessageDigest): Long {
        val buffer = ByteArray(BUFFER_SIZE_BYTES)
        var count: Int
        var total = 0L
        while (stream.read(buffer).also { count = it } != -1) {
            digest.update(buffer, 0, count)
            total += count
        }
        return total
    }

    private fun ByteArray.toHex(): String = joinToString("") { byte -> "%02x".format(byte) }

    private companion object {
        const val BUFFER_SIZE_BYTES = 64 * 1024
    }
}
