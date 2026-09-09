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
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.InputStream
import java.security.MessageDigest
import kotlin.math.min

class SyncUploadManager(
    private val contentResolver: ContentResolver,
    private val dbHelper: UploadDatabaseHelper,
    private val apiServiceProvider: () -> IrisApiService
) {

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
        val hash = computeSha256(uri)
        dbHelper.insertOrIgnoreJob(
            localUri = uri.toString(),
            filename = filename,
            byteSize = size,
            sha256 = hash,
            capturedAt = capturedAtIso
        )
    }

    suspend fun processQueue(): Boolean = withContext(Dispatchers.IO) {
        _isUploading.value = true
        var processedAny = false
        try {
            while (true) {
                val nextJob = dbHelper.getNextPendingJob() ?: break
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
            val octetStreamMediaType = "application/octet-stream".toMediaType()

            while (currentOffset < job.byteSize) {
                val remaining = job.byteSize - currentOffset
                val bytesToRead = min(remaining, chunkSize.toLong()).toInt()
                val chunkBytes = readChunkBytes(uri, currentOffset, bytesToRead)

                val requestBody = chunkBytes.toRequestBody(octetStreamMediaType)
                val response = apiService.uploadChunk(
                    uploadId = uploadId,
                    offset = currentOffset,
                    body = requestBody
                )

                if (response.isSuccessful) {
                    val nextOffset = response.body()?.offset ?: (currentOffset + chunkBytes.size)
                    currentOffset = nextOffset
                    dbHelper.updateOffsetTransactionally(job.id, currentOffset)
                    _currentProgress.value = if (job.byteSize > 0) currentOffset.toFloat() / job.byteSize.toFloat() else 0f
                } else if (response.code() == 409) {
                    // Offset mismatch! Never guess it; query server confirmed position
                    val status = apiService.getUploadStatus(uploadId)
                    currentOffset = status.offset
                    dbHelper.updateOffsetTransactionally(job.id, currentOffset)
                } else if (response.code() == 401) {
                    // Session expired or revoked
                    dbHelper.updateJobState(job.id, UploadJobState.FAILED, "Autenticação revogada (401)")
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
        } catch (e: Exception) {
            dbHelper.updateJobState(
                job.id,
                UploadJobState.FAILED,
                e.localizedMessage ?: "Erro desconhecido durante envio"
            )
            false
        }
    }

    private fun readChunkBytes(uri: Uri, offset: Long, length: Int): ByteArray {
        contentResolver.openInputStream(uri)?.use { stream ->
            skipFully(stream, offset)
            val buffer = ByteArray(length)
            var bytesReadTotal = 0
            while (bytesReadTotal < length) {
                val count = stream.read(buffer, bytesReadTotal, length - bytesReadTotal)
                if (count == -1) break
                bytesReadTotal += count
            }
            return if (bytesReadTotal == length) {
                buffer
            } else {
                buffer.copyOf(bytesReadTotal)
            }
        } ?: throw IllegalStateException("Não foi possível abrir o arquivo da mídia local")
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
        contentResolver.openInputStream(uri)?.use { stream ->
            val buffer = ByteArray(1024 * 1024)
            var count: Int
            while (stream.read(buffer).also { count = it } != -1) {
                digest.update(buffer, 0, count)
            }
        } ?: return ""
        val bytes = digest.digest()
        return bytes.joinToString("") { "%02x".format(it) }
    }
}
