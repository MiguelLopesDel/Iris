package com.iris.app.data.model

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/**
 * A shared space as the server lists it: a gallery of its own, shared with
 * other accounts of the same server. [role] decides what the app offers; the
 * server enforces it regardless.
 */
@Serializable
data class SpaceSummary(
    @SerialName("id") val id: Int,
    @SerialName("name") val name: String = "",
    @SerialName("role") val role: String = "viewer",
) {
    val canAdd: Boolean get() = role == "contributor" || role == "manager"
    val roleLabel: String
        get() = when (role) {
            "manager" -> "Gestor"
            "contributor" -> "Colaborador"
            else -> "Visualizador"
        }
}

@Serializable
data class SpacesResponse(
    @SerialName("spaces") val spaces: List<SpaceSummary> = emptyList(),
)

@Serializable
data class SpaceResponse(
    @SerialName("space") val space: SpaceSummary,
)

@Serializable
data class CreateSpaceRequest(
    @SerialName("name") val name: String,
)

@Serializable
data class SpaceItem(
    @SerialName("id") val id: Int,
    @SerialName("name") val name: String = "",
    @SerialName("media_type") val mediaType: String = "image",
    @SerialName("added_by_username") val addedByUsername: String? = null,
    @SerialName("added_at") val addedAt: String = "",
    @SerialName("can_remove") val canRemove: Boolean = false,
    @SerialName("thumbnail_url") val thumbnailUrl: String? = null,
    @SerialName("original_url") val originalUrl: String? = null,
) {
    val isVideo: Boolean get() = mediaType == "video"
}

@Serializable
data class SpaceItemsResponse(
    @SerialName("items") val items: List<SpaceItem> = emptyList(),
    // The last id of this page; pass it back as `before` for the next one.
    @SerialName("next_before") val nextBefore: Int? = null,
)

@Serializable
data class AddSpaceItemRequest(
    // A database id of the caller's own library; the server resolves it only
    // there, so it cannot name anybody else's photo.
    @SerialName("record_id") val recordId: Int,
)

@Serializable
data class AddSpaceItemResponse(
    @SerialName("item") val item: SpaceItem,
    @SerialName("created") val created: Boolean = true,
)

@Serializable
data class SaveSpaceItemResponse(
    // "pending_processing" when queued, "duplicate" when already in the library.
    @SerialName("state") val state: String = "",
)

@Serializable
data class SpaceMember(
    @SerialName("user_id") val userId: Int,
    @SerialName("username") val username: String = "",
    @SerialName("display_name") val displayName: String = "",
    @SerialName("role") val role: String = "viewer",
    @SerialName("is_you") val isYou: Boolean = false,
)

@Serializable
data class SpaceMembersResponse(
    @SerialName("members") val members: List<SpaceMember> = emptyList(),
)

@Serializable
data class SpaceAlbum(
    @SerialName("id") val id: Int,
    @SerialName("name") val name: String = "",
    @SerialName("count") val count: Int = 0,
    @SerialName("cover_url") val coverUrl: String? = null,
)

@Serializable
data class SpaceAlbumsResponse(
    @SerialName("albums") val albums: List<SpaceAlbum> = emptyList(),
)

@Serializable
data class SpaceSearchResponse(
    @SerialName("items") val items: List<SpaceItem> = emptyList(),
    // False when the server has no model loaded: the results are text matches.
    @SerialName("semantic") val semantic: Boolean = false,
)
