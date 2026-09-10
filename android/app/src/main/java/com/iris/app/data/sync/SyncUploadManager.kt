package com.iris.app.data.sync

import android.content.ContentResolver
import android.net.Uri
import com.iris.app.data.local.UploadDatabaseHelper
import com.iris.app.data.model.LocalUploadJob
import com.iris.app.data.model.UploadInitRequest
import com.iris.app.data.model.UploadJobState
import com.iris.app.data.remote.IrisApiService
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.withContext
import kotlinx.coroutines.sync.Mutex
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody
import okio.BufferedSink
import java.io.FileInputStream
import java.io.InputStream
import java.security.MessageDigest
import kotlin.math.min

class SyncUploadManager(
    private val contentResolver: ContentResolver,
    private val dbHelper: UploadDatabaseHelper,
    private val apiServiceProvider: () -> IrisApiService
) {

    private val uploadMutex = Mutex()

    private val _isUploading = MutableStateFlow(false)
    val isUploading: StateFlow<Boolean> = _isUploading.asStateFlow()

    private val _currentProgress = MutableStateFlow(0f)
    val currentProgress: StateFlow<Float> = _currentProgress.asStateFlow()

    suspend fun enqueueMedia(
        uri: Uri,
        filename: String,
        size: Long,
        capturedAtIso: String
    ): Long = withContext(Dispatchers.IO) {
        val uriStr = uri.toString()
        if (dbHelper.isUriEnqueued(uriStr)) {
            return@withContext -1L
        }
        val hash = computeSha256(uri)
        dbHelper.insertOrIgnoreJob(
            localUri = uriStr,
            filename = filename,
            byteSize = size,
            sha256 = hash,
            capturedAt = capturedAtIso
        )
    }

    suspend fun processQueue(): Boolean = withContext(Dispatchers.IO) {
        // Prevent multiple workers or UI buttons from running concurrent upload loops
        if (!uploadMutex.tryLock()) {
            return@withContext false
        }
        _isUploading.value = true
        var processedAny = false
        try {
            while (true) {
                val nextJob = dbHelper.claimNextPendingJob() ?: break
                processedAny = true
                val success = executeUpload(nextJob)
                if (!success) {
                    // Abort loop to allow retry later according to WorkManager backoff
                    break
                }
            }
        } finally {
            _isUploading.value = false
            _currentProgress.value = 0f
            uploadMutex.unlock()
        }
        processedAny
    }

    private suspend fun executeUpload(job: LocalUploadJob): Boolean = withContext(Dispatchers.IO) {
        val apiService = apiServiceProvider()
        var uploadId = job.uploadId
        var currentOffset = job.nextByteOffset
        val chunkSize = job.chunkSize

        try {
            // Step 1: Initiate upload if not yet started
            if (uploadId.isNullOrBlank()) {
                val initResponse = apiService.initUpload(
                    UploadInitRequest(
                        filename = job.filename,
                        size = job.byteSize,
                        sha256 = job.sha256,
                        capturedAt = job.capturedAt
                    )
                )
                uploadId = initResponse.uploadId
                currentOffset = initResponse.offset
                dbHelper.updateUploadStarted(
                    id = job.id,
                    uploadId = uploadId,
                    offset = currentOffset,
                    chunkSize = initResponse.chunkSize
                )
            }

            // Step 2: Sequential chunk upload loop
            val uri = Uri.parse(job.localUri)
            var conflictCount = 0

            while (currentOffset < job.byteSize) {
                val remaining = job.byteSize - currentOffset
                val bytesToRead = min(remaining, chunkSize.toLong())
                val requestBody = createChunkRequestBody(uri, currentOffset, bytesToRead)

                val response = apiService.uploadChunk(
                    uploadId = uploadId,
                    offset = currentOffset,
                    body = requestBody
                )

                if (response.isSuccessful) {
                    val nextOffset = response.body()?.offset ?: (currentOffset + bytesToRead)
                    currentOffset = nextOffset
                    conflictCount = 0
                    dbHelper.updateOffsetTransactionally(job.id, currentOffset)
                    _currentProgress.value = if (job.byteSize > 0) currentOffset.toFloat() / job.byteSize.toFloat() else 0f
                } else if (response.code() == 409) {
                    conflictCount++
                    if (conflictCount > 3) {
                        dbHelper.updateJobState(job.id, UploadJobState.FAILED, "Conflito persistente de offset no upload (409)")
                        return@withContext false
                    }
                    // Offset mismatch! Query server confirmed position
                    val status = apiService.getUploadStatus(uploadId)
                    when (status.state) {
                        "ready" -> {
                            dbHelper.updateJobState(job.id, UploadJobState.READY)
                            return@withContext true
                        }
                        "duplicate" -> {
                            dbHelper.updateJobState(job.id, UploadJobState.DUPLICATE)
                            return@withContext true
                        }
                        "pending_processing" -> {
                            dbHelper.updateJobState(job.id, UploadJobState.PENDING_PROCESSING)
                            return@withContext true
                        }
                        else -> {
                            currentOffset = status.offset
                            dbHelper.updateOffsetTransactionally(job.id, currentOffset)
                        }
                    }
                } else if (response.code() == 401) {
                    // Session might be refreshing; do not permanently fail, let WorkManager retry
                    return@withContext false
                } else {
                    val err = response.errorBody()?.string() ?: "Erro HTTP ${response.code()}"
                    dbHelper.updateJobState(job.id, UploadJobState.FAILED, err)
                    return@withContext false
                }
            }

            // Step 3: Complete upload and verify full SHA-256
            val completion = apiService.completeUpload(uploadId)
            when (completion.state) {
                "pending_processing" -> {
                    // Backup succeeded; indexing pending. Do not mark failed or re-upload.
                    dbHelper.updateJobState(job.id, UploadJobState.PENDING_PROCESSING)
                }
                "duplicate" -> {
                    // Original already present in private library
                    dbHelper.updateJobState(job.id, UploadJobState.DUPLICATE)
                }
                else -> {
                    dbHelper.updateJobState(job.id, UploadJobState.READY)
                }
            }
            true
        } catch (e: java.io.IOException) {
            // Transient network failure: keep job in UPLOADING state so WorkManager can retry cleanly
            false
        } catch (e: Exception) {
            dbHelper.updateJobState(
                job.id,
                UploadJobState.FAILED,
                e.localizedMessage ?: "Erro desconhecido durante envio"
            )
            false
        }
    }

    /**
     * Streaming RequestBody with O(1) kernel seek via ParcelFileDescriptor/FileChannel.
     * Uses a lightweight 64 KB buffer, eliminating 32 MB JVM heap allocations and OOM risk.
     * isOneShot() = false allows OkHttp to retry cleanly upon 401 token refresh.
     */
    private fun createChunkRequestBody(
        uri: Uri,
        offset: Long,
        length: Long
    ): RequestBody = object : RequestBody() {
        override fun contentType() = "application/octet-stream".toMediaType()
        override fun contentLength(): Long = length
        override fun isOneShot(): Boolean = false

        override fun writeTo(sink: BufferedSink) {
            val pfd = try {
                contentResolver.openFileDescriptor(uri, "r")
            } catch (_: Exception) {
                null
            }

            if (pfd != null) {
                pfd.use { fd ->
                    FileInputStream(fd.fileDescriptor).use { stream ->
                        stream.channel.position(offset)
                        val buffer = ByteArray(64 * 1024)
                        var bytesRemaining = length
                        while (bytesRemaining > 0) {
                            val toRead = min(bytesRemaining, buffer.size.toLong()).toInt()
                            val read = stream.read(buffer, 0, toRead)
                            if (read == -1) break
                            sink.write(buffer, 0, read)
                            bytesRemaining -= read
                        }
                    }
                }
            } else {
                contentResolver.openInputStream(uri)?.use { stream ->
                    skipFully(stream, offset)
                    val buffer = ByteArray(64 * 1024)
                    var bytesRemaining = length
                    while (bytesRemaining > 0) {
                        val toRead = min(bytesRemaining, buffer.size.toLong()).toInt()
                        val read = stream.read(buffer, 0, toRead)
                        if (read == -1) break
                        sink.write(buffer, 0, read)
                        bytesRemaining -= read
                    }
                } ?: throw java.io.IOException("Não foi possível abrir o arquivo da mídia local")
            }
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

    fun computeSha256(uri: Uri): String {
        val digest = MessageDigest.getInstance("SHA-256")
        val pfd = try {
            contentResolver.openFileDescriptor(uri, "r")
        } catch (_: Exception) {
            null
        }

        if (pfd != null) {
            pfd.use { fd ->
                FileInputStream(fd.fileDescriptor).use { stream ->
                    val buffer = ByteArray(64 * 1024)
                    var count: Int
                    while (stream.read(buffer).also { count = it } != -1) {
                        digest.update(buffer, 0, count)
                    }
                }
            }
        } else {
            contentResolver.openInputStream(uri)?.use { stream ->
                val buffer = ByteArray(64 * 1024)
                var count: Int
                while (stream.read(buffer).also { count = it } != -1) {
                    digest.update(buffer, 0, count)
                }
            } ?: return ""
        }
        val bytes = digest.digest()
        return bytes.joinToString("") { "%02x".format(it) }
    }
}
