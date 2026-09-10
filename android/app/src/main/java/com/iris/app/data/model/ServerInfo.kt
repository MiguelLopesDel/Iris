package com.iris.app.data.model

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

@Serializable
data class ServerInfo(
    @SerialName("total_records") val totalRecords: Int = 0,
    @SerialName("records") val legacyRecords: Int = 0,
    @SerialName("model_name") val modelName: String = "",
    @SerialName("model") val legacyModel: String = "",
    @SerialName("device") val device: String = "",
    @SerialName("db_path") val dbPath: String = "",
    @SerialName("db") val legacyDb: String = "",
    @SerialName("multiuser") val multiuser: Boolean = false,
    @SerialName("faiss_index_exists") val faissIndexExists: Boolean = false,
    @SerialName("florence_model") val florenceModel: String = "",
    @SerialName("version") val version: String = "1.0.0"
) {
    val records: Int
        get() = if (totalRecords > 0) totalRecords else legacyRecords

    val model: String
        get() = modelName.ifBlank { legacyModel }

    val db: String
        get() = dbPath.ifBlank { legacyDb }
}

@Serializable
data class HealthResponse(
    @SerialName("status") val status: String = "ok",
    @SerialName("mode") val mode: String = "legacy"
)

