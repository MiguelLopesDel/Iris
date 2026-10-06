package com.iris.app.data.model

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject

@Serializable
data class UserInfo(
    @SerialName("id") val id: Int = 0,
    @SerialName("username") val username: String = "",
    @SerialName("display_name") val displayName: String = "",
    @SerialName("is_admin") val isAdmin: Boolean = false
)

@Serializable
data class DeviceLoginResponse(
    @SerialName("user") val user: UserInfo? = null,
    @SerialName("access_token") val accessToken: String,
    @SerialName("refresh_token") val refreshToken: String,
    @SerialName("token_type") val tokenType: String = "Bearer",
    @SerialName("expires_in") val expiresIn: Long = 900L,
    @SerialName("device_id") val deviceId: String
)

@Serializable
data class DeviceRefreshResponse(
    @SerialName("access_token") val accessToken: String,
    @SerialName("refresh_token") val refreshToken: String,
    @SerialName("token_type") val tokenType: String = "Bearer",
    @SerialName("expires_in") val expiresIn: Long = 900L,
    @SerialName("device_id") val deviceId: String
)

@Serializable
data class UploadInitRequest(
    @SerialName("filename") val filename: String,
    @SerialName("size") val size: Long,
    @SerialName("sha256") val sha256: String,
    @SerialName("captured_at") val capturedAt: String,
    @SerialName("source") val source: UploadSource? = null
)

@Serializable
data class UploadInitBatchItemRequest(
    @SerialName("client_upload_id") val clientUploadId: String,
    @SerialName("filename") val filename: String,
    @SerialName("size") val size: Long,
    @SerialName("sha256") val sha256: String,
    @SerialName("captured_at") val capturedAt: String,
    @SerialName("source") val source: UploadSource? = null
)

@Serializable
data class UploadInitBatchRequest(
    @SerialName("uploads") val uploads: List<UploadInitBatchItemRequest>
)

@Serializable
data class UploadInitBatchItemResponse(
    @SerialName("client_upload_id") val clientUploadId: String,
    @SerialName("upload_id") val uploadId: String? = null,
    @SerialName("offset") val offset: Long = 0L,
    @SerialName("chunk_size") val chunkSize: Int = 32 * 1024 * 1024,
    @SerialName("state") val state: String = "uploading",
    @SerialName("error_code") val errorCode: Int? = null,
    @SerialName("error_message") val errorMessage: String? = null
)

@Serializable
data class UploadInitBatchResponse(
    @SerialName("uploads") val uploads: List<UploadInitBatchItemResponse>
)

@Serializable
data class UploadCompleteBatchItemRequest(
    @SerialName("upload_id") val uploadId: String,
)

@Serializable
data class UploadCompleteBatchRequest(
    @SerialName("uploads") val uploads: List<UploadCompleteBatchItemRequest>,
)

@Serializable
data class UploadCompleteBatchItemResponse(
    @SerialName("upload_id") val uploadId: String,
    @SerialName("state") val state: String? = null,
    @SerialName("media_id") val mediaId: Int? = null,
    @SerialName("cursor") val cursor: Long? = null,
    @SerialName("error_code") val errorCode: Int? = null,
    @SerialName("error_message") val errorMessage: String? = null,
)

@Serializable
data class UploadCompleteBatchResponse(
    @SerialName("uploads") val uploads: List<UploadCompleteBatchItemResponse>,
)

@Serializable
data class UploadSource(
    @SerialName("id") val id: String,
    @SerialName("name") val name: String,
    @SerialName("relative_path") val relativePath: String = "",
    @SerialName("volume") val volume: String = "",
    @SerialName("media_store_id") val mediaStoreId: String = "",
    @SerialName("generation") val generation: Long = 0L,
    @SerialName("media_kind") val mediaKind: String
)

data class DeviceMediaSource(
    val id: String,
    val name: String,
    val relativePath: String,
    val volume: String,
    val mediaKind: String,
    val itemCount: Int
)

data class MediaScanPolicy(
    val mode: String = "selected",
    val selectedSourceIds: Set<String> = emptySet(),
    val includeImages: Boolean = true,
    val includeVideos: Boolean = true
) {
    fun includes(sourceId: String, mediaKind: String): Boolean {
        val kindEnabled = if (mediaKind == "video") includeVideos else includeImages
        return kindEnabled && (mode == "all" || sourceId in selectedSourceIds)
    }
}

@Serializable
data class UploadInitResponse(
    @SerialName("upload_id") val uploadId: String,
    @SerialName("offset") val offset: Long = 0L,
    @SerialName("chunk_size") val chunkSize: Int = 32 * 1024 * 1024,
    @SerialName("state") val state: String = "uploading"
)

@Serializable
data class UploadStatusResponse(
    @SerialName("upload_id") val uploadId: String,
    @SerialName("offset") val offset: Long = 0L,
    @SerialName("size") val size: Long = 0L,
    @SerialName("state") val state: String = ""
)

@Serializable
data class UploadChunkResponse(
    @SerialName("upload_id") val uploadId: String,
    @SerialName("offset") val offset: Long
)

@Serializable
data class SpeedTestResponse(
    @SerialName("bytes") val bytes: Long,
    @SerialName("server_seconds") val serverSeconds: Double,
    @SerialName("mode") val mode: String
)

@Serializable
data class UploadCompleteResponse(
    @SerialName("upload_id") val uploadId: String,
    @SerialName("state") val state: String,
    @SerialName("cursor") val cursor: Long = 0L,
    @SerialName("path") val path: String? = null,
    @SerialName("media_id") val mediaId: Int? = null
)

@Serializable
data class SyncChange(
    @SerialName("cursor") val cursor: Long = 0L,
    @SerialName("entity_type") val entityType: String = "",
    @SerialName("entity_id") val entityId: String = "",
    @SerialName("operation") val operation: String = "",
    @SerialName("revision") val revision: Int = 1,
    @SerialName("version") val version: Int = 1,
    @SerialName("created_at") val createdAt: String = "",
    @SerialName("payload") val payload: JsonObject? = null
)

@Serializable
data class ChangesResponse(
    @SerialName("changes") val changes: List<SyncChange> = emptyList(),
    @SerialName("next_cursor") val nextCursor: Long = 0L,
    @SerialName("has_more") val hasMore: Boolean = false
)

enum class UploadJobState {
    QUEUED,
    UPLOADING,
    PENDING_PROCESSING,
    PROCESSING,
    READY,
    DUPLICATE,
    FAILED,
    FAILED_PROCESSING
}

data class LocalUploadJob(
    val id: Long = 0L,
    val localUri: String,
    val filename: String,
    val byteSize: Long,
    val sha256: String,
    val capturedAt: String,
    val source: UploadSource? = null,
    val uploadId: String? = null,
    val nextByteOffset: Long = 0L,
    val chunkSize: Int = 32 * 1024 * 1024,
    val state: UploadJobState = UploadJobState.QUEUED,
    val errorMessage: String? = null,
    val updatedAt: Long = System.currentTimeMillis(),
    /** MediaStore DATE_MODIFIED when the hash was taken; null on rows from before it was recorded. */
    val sourceDateModified: Long? = null,
    /** The hash this row had before the local file was edited, if it was. */
    val previousSha256: String? = null,
    /** When the hash was last confirmed against the file. */
    val verifiedAt: Long? = null,
    /** MediaStore's SIZE when hashed, part of the fingerprint; byteSize is the real file size sent. */
    val sourceSize: Long? = null,
    /** Whether [sha256] is of the original file (with location) or the redacted one; chunks read the same. */
    val hashedOriginal: Boolean = false,
    /** Whether ACCESS_MEDIA_LOCATION was granted when [sha256] was computed. */
    val hashedWithLocation: Boolean = false,
)

/** Um mês do acervo e onde ele começa na listagem ordenada por data. */
@Serializable
data class TimelineBucket(
    @SerialName("month") val month: String = "",
    @SerialName("count") val count: Int = 0,
    @SerialName("offset") val offset: Int = 0
)

@Serializable
data class TimelineResponse(
    @SerialName("total") val total: Int = 0,
    @SerialName("buckets") val buckets: List<TimelineBucket> = emptyList()
)
