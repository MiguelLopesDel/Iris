package com.iris.app.data.model

import com.iris.app.R
import com.iris.app.ui.text.appString
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

@Serializable
data class MediaPersonRef(
    @SerialName("id") val id: Int = 0,
    @SerialName("name") val name: String = "",
    @SerialName("face_id") val faceId: Int? = null
)

@Serializable
data class MediaRecord(
    @SerialName("index") val index: Int,
    @SerialName("db_id") val dbId: Int? = null,
    @SerialName("arquivo") val arquivo: String = "",
    @SerialName("resolved_path") val resolvedPath: String? = null,
    @SerialName("caminho") val caminho: String? = null,
    @SerialName("texto_extraido") val textoExtraido: String? = null,
    @SerialName("descricao_ia") val descricaoIa: String? = null,
    @SerialName("tags") val tags: String? = null,
    @SerialName("visual_json") val visualJson: String? = null,
    @SerialName("objects") val objects: String? = null,
    @SerialName("style") val style: String? = null,
    @SerialName("source_work") val sourceWork: String? = null,
    @SerialName("humor") val humor: String? = null,
    @SerialName("context") val context: String? = null,
    @SerialName("content_hash") val contentHash: String? = null,
    @SerialName("file_size") val fileSize: Long? = null,
    @SerialName("file_mtime") val fileMtime: Double? = null,
    @SerialName("media_type") val mediaType: String = "image",
    @SerialName("thumbnail_url") val thumbnailUrl: String? = null,
    // Inline preview painted while the thumbnail request is still in flight.
    @SerialName("thumb_hash") val thumbHash: String? = null,
    /** Marked with a heart; the server keeps it per library. */
    @SerialName("favorite") val favorite: Boolean = false,
    @SerialName("persons") val persons: List<MediaPersonRef> = emptyList(),
    @SerialName("score") val score: Float? = null,
    @SerialName("collections") val collections: List<IrisCollection> = emptyList(),
    @SerialName("concepts") val concepts: List<MediaConceptRef> = emptyList(),
    /** Device-only gallery data; these fields never cross the server JSON boundary. */
    @kotlinx.serialization.Transient val deviceUri: String? = null,
    @kotlinx.serialization.Transient val mimeType: String? = null,
    /** What MediaStore says about the device file now, to spot changes since it was hashed. */
    @kotlinx.serialization.Transient val deviceFingerprint: com.iris.app.data.sync.MediaFingerprint? = null,
    /** The backup folder id of the device file (`volume:bucket:kind`). */
    @kotlinx.serialization.Transient val deviceSourceId: String? = null,
    /** The device folder's name, for telling the user which folder is meant. */
    @kotlinx.serialization.Transient val deviceFolder: String? = null,
    /**
     * For an Iris record whose file is also on this phone: the phone's copy
     * (a MediaStore URI). It is drawn instead of downloading from the server,
     * at once and offline; [deviceUri] stays null, since the item is Iris's.
     */
    @kotlinx.serialization.Transient val localCopyUri: String? = null
) {
    val isVideo: Boolean
        get() = mediaType.equals("video", ignoreCase = true)

    val cleanFilename: String
        get() = arquivo.ifBlank {
            resolvedPath?.substringAfterLast('/') ?: appString(R.string.media_fallback_name, index)
        }

    val tagsList: List<String>
        get() = tags?.split(",")?.map { it.trim() }?.filter { it.isNotBlank() } ?: emptyList()
}

/** Compact concept membership returned by a media-detail response. */
@Serializable
data class MediaConceptRef(
    @SerialName("id") val id: Int,
    @SerialName("name") val name: String = "",
    @SerialName("category") val category: String = "",
    @SerialName("confirmed") val confirmed: Boolean = false
)

@Serializable
data class RecordsResponse(
    @SerialName("page") val page: Int = 1,
    @SerialName("per_page") val perPage: Int = 24,
    @SerialName("total") val total: Int = 0,
    @SerialName("total_pages") val totalPages: Int = 1,
    @SerialName("missing_count") val missingCount: Int = 0,
    @SerialName("records") val records: List<MediaRecord> = emptyList()
)

/** Media taken on this month and day in one earlier year. */
@Serializable
data class OnThisDayYear(
    @SerialName("year") val year: Int,
    @SerialName("years_ago") val yearsAgo: Int,
    /** How many there are that day; [records] holds at most the requested number. */
    @SerialName("total") val total: Int = 0,
    @SerialName("records") val records: List<MediaRecord> = emptyList()
)

@Serializable
data class OnThisDayResponse(
    @SerialName("date") val date: String = "",
    @SerialName("years") val years: List<OnThisDayYear> = emptyList()
)

/** An item in the server's trash: out of the library, restorable until [purgeAfter]. */
@Serializable
data class TrashedItem(
    @SerialName("id") val id: Int,
    @SerialName("name") val name: String = "",
    @SerialName("size_bytes") val sizeBytes: Long = 0,
    @SerialName("trashed_at") val trashedAt: String = "",
    @SerialName("purge_after") val purgeAfter: String = "",
    @SerialName("thumbnail_url") val thumbnailUrl: String? = null
)

@Serializable
data class TrashPage(
    @SerialName("items") val items: List<TrashedItem> = emptyList(),
    @SerialName("next_before") val nextBefore: Int? = null,
    @SerialName("trash_days") val trashDays: Int = 30
)

@Serializable
data class RestoreTrashRequest(@SerialName("ids") val ids: List<Int>)

@Serializable
data class RestoreTrashResponse(
    @SerialName("restored") val restored: List<Int> = emptyList(),
    @SerialName("conflicts") val conflicts: List<Int> = emptyList(),
    @SerialName("missing") val missing: List<Int> = emptyList()
)

/** One kind of document the server found among the photos, with the newest as its cover. */
@Serializable
data class DocumentKind(
    @SerialName("key") val key: String,
    @SerialName("title") val title: String,
    @SerialName("count") val count: Int = 0,
    @SerialName("cover") val cover: MediaRecord? = null
)

@Serializable
data class DocumentKinds(
    @SerialName("total") val total: Int = 0,
    @SerialName("kinds") val kinds: List<DocumentKind> = emptyList()
)

@Serializable
data class TrashResponse(
    @SerialName("moved") val moved: Int = 0,
    @SerialName("failed") val failed: Int = 0
)

@Serializable
data class SearchResponse(
    @SerialName("query") val query: String = "",
    @SerialName("total") val total: Int = 0,
    @SerialName("results") val results: List<MediaRecord> = emptyList()
)
