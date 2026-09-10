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
    @SerialName("captured_at") val capturedAt: String
)

@Serializable
data class UploadInitResponse(
    @SerialName("upload_id") val uploadId: String,
    @SerialName("offset") val offset: Long = 0L,
    @SerialName("chunk_size") val chunkSize: Int = 32 * 1024 * 1024
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
    val uploadId: String? = null,
    val nextByteOffset: Long = 0L,
    val chunkSize: Int = 32 * 1024 * 1024,
    val state: UploadJobState = UploadJobState.QUEUED,
    val errorMessage: String? = null,
    val updatedAt: Long = System.currentTimeMillis()
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
