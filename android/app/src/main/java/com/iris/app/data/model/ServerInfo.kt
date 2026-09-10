package com.iris.app.data.model

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

@Serializable
data class ServerInfo(
    @SerialName("records") val records: Int = 0,
    @SerialName("model") val model: String = "",
    @SerialName("device") val device: String = "",
    @SerialName("db") val db: String = "",
    @SerialName("faiss_index_exists") val faissIndexExists: Boolean = false,
    @SerialName("florence_model") val florenceModel: String = "",
    @SerialName("version") val version: String = "1.0.0"
)

@Serializable
data class HealthResponse(
    @SerialName("status") val status: String = "ok",
    @SerialName("mode") val mode: String = "legacy"
)

