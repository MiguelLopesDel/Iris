package com.iris.app.data.model

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
    @SerialName("persons") val persons: List<MediaPersonRef> = emptyList(),
    @SerialName("score") val score: Float? = null,
    @SerialName("collections") val collections: List<String> = emptyList(),
    @SerialName("concepts") val concepts: List<String> = emptyList()
) {
    val isVideo: Boolean
        get() = mediaType.equals("video", ignoreCase = true)

    val cleanFilename: String
        get() = arquivo.ifBlank {
            resolvedPath?.substringAfterLast('/') ?: "Mídia #$index"
        }

    val tagsList: List<String>
        get() = tags?.split(",")?.map { it.trim() }?.filter { it.isNotBlank() } ?: emptyList()
}

@Serializable
data class RecordsResponse(
    @SerialName("page") val page: Int = 1,
    @SerialName("per_page") val perPage: Int = 24,
    @SerialName("total") val total: Int = 0,
    @SerialName("total_pages") val totalPages: Int = 1,
    @SerialName("missing_count") val missingCount: Int = 0,
    @SerialName("records") val records: List<MediaRecord> = emptyList()
)

@Serializable
data class SearchResponse(
    @SerialName("query") val query: String = "",
    @SerialName("total") val total: Int = 0,
    @SerialName("results") val results: List<MediaRecord> = emptyList()
)
