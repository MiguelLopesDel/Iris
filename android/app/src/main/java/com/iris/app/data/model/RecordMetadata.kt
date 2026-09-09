package com.iris.app.data.model

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject

@Serializable
data class RecordMetadataResponse(
    @SerialName("index") val index: Int,
    @SerialName("exists") val exists: Boolean = true,
    @SerialName("curated") val curated: JsonObject? = null,
    @SerialName("full") val full: JsonObject? = null
)
