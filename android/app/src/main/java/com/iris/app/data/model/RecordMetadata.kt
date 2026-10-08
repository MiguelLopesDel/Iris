package com.iris.app.data.model

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject

/** `GET /api/records/{idx}/metadata`, as the server's RecordMetadataOut describes it. */
@Serializable
data class RecordMetadataResponse(
    @SerialName("curated") val curated: JsonObject? = null,
    @SerialName("full") val full: JsonObject? = null,
    @SerialName("path_exists") val pathExists: Boolean = false,
)
