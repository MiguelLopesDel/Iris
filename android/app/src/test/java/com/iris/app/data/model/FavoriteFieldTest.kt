package com.iris.app.data.model

import kotlinx.serialization.json.Json
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class FavoriteFieldTest {
    private val json = Json { ignoreUnknownKeys = true }

    @Test
    fun `a record says whether it is a favourite`() {
        val hearted = json.decodeFromString<MediaRecord>("""{"index": 1, "favorite": true}""")
        assertTrue(hearted.favorite)
    }

    @Test
    fun `an older server without the field means not a favourite`() {
        val record = json.decodeFromString<MediaRecord>("""{"index": 1}""")
        assertFalse(record.favorite)
    }
}
