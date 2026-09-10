package com.iris.app.data.model

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

@Serializable
data class Person(
    @SerialName("id") val id: Int,
    // An unnamed person comes back as null. Without a default there is nothing
    // for coerceInputValues to fall back to, and one unnamed person fails the
    // decode of the entire persons response.
    @SerialName("name") val name: String = "",
    @SerialName("cover_face_id") val coverFaceId: Int? = null,
    @SerialName("media_count") val mediaCount: Int = 0
)

@Serializable
data class PersonsResponse(
    @SerialName("persons") val persons: List<Person> = emptyList()
)

@Serializable
data class PersonMediaResponse(
    @SerialName("person_id") val personId: Int,
    @SerialName("person_name") val personName: String = "",
    @SerialName("total") val total: Int = 0,
    @SerialName("results") val results: List<MediaRecord> = emptyList()
)
