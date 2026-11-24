package com.iris.app.data.model

import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Test

class DocumentKindsTest {
    @Test
    fun `the server's document kinds decode with their covers`() {
        val body = """{"total": 3, "kinds": [
            {"key": "identidade", "title": "Identidade", "count": 2, "cover": {"index": 4, "arquivo": "IMG_0001.jpg"}},
            {"key": "boletos", "title": "Boletos e contas", "count": 1, "cover": null}
        ]}"""

        val kinds = Json { ignoreUnknownKeys = true }.decodeFromString<DocumentKinds>(body)

        assertEquals(3, kinds.total)
        assertEquals(listOf("identidade", "boletos"), kinds.kinds.map { it.key })
        assertEquals(4, kinds.kinds.first().cover?.index)
    }
}
