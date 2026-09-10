package com.iris.app.data.model

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

@Serializable
data class IrisCollection(
    @SerialName("id") val id: Int,
    @SerialName("name") val name: String,
    @SerialName("count") val count: Int = 0
)

@Serializable
data class CollectionsResponse(
    @SerialName("collections") val collections: List<IrisCollection> = emptyList()
)

@Serializable
data class CollectionMembersResponse(
    // The server's public endpoint returns `records` and `db_ids`.  Keep
    // `members` as a compatibility fallback for older Iris servers.
    @SerialName("records") val records: List<MediaRecord> = emptyList(),
    @SerialName("members") val members: List<MediaRecord> = emptyList()
)

@Serializable
data class IrisConcept(
    @SerialName("id") val id: Int,
    @SerialName("name") val name: String,
    @SerialName("description") val description: String? = null,
    @SerialName("reference_count") val referenceCount: Int = 0,
    @SerialName("match_count") val matchCount: Int = 0
)

@Serializable
data class ConceptsResponse(
    @SerialName("concepts") val concepts: List<IrisConcept> = emptyList()
)
