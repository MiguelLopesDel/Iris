package com.iris.app

import com.iris.app.data.model.CollectionsResponse
import com.iris.app.data.model.PersonsResponse
import com.iris.app.data.remote.IrisApiClient
import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * The server returns `"name": null` for anything a user has not named yet.
 *
 * Decoding is all-or-nothing per response, so a single unnamed row used to take
 * down the entire screen: the Persons tab showed "Unexpected 'null' value
 * instead of string literal at path: $.persons[0].name" and rendered nothing.
 * These models need defaults for `coerceInputValues` to have somewhere to land.
 */
class NullableNamesTest {

    private val json = IrisApiClient.RESPONSE_JSON

    @Test
    fun `unnamed person decodes instead of failing the whole response`() {
        val payload = """
            {"persons":[
              {"id":5184,"name":null,"cover_face_id":2171,"media_count":12},
              {"id":91,"name":"Alguém","cover_face_id":4,"media_count":3}
            ]}
        """.trimIndent()

        val decoded = json.decodeFromString<PersonsResponse>(payload)

        assertEquals(2, decoded.persons.size)
        assertEquals("", decoded.persons[0].name)
        assertEquals("Alguém", decoded.persons[1].name)
    }

    @Test
    fun `one unnamed row does not discard the named rows around it`() {
        val payload = """
            {"collections":[
              {"id":1,"name":"Abril 2026","count":145},
              {"id":2,"name":null,"count":7}
            ]}
        """.trimIndent()

        val decoded = json.decodeFromString<CollectionsResponse>(payload)

        assertEquals(2, decoded.collections.size)
        assertEquals("Abril 2026", decoded.collections[0].name)
        assertEquals("", decoded.collections[1].name)
    }
}
