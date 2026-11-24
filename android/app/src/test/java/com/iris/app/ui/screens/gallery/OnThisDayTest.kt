package com.iris.app.ui.screens.gallery

import com.iris.app.data.model.OnThisDayResponse
import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Test

class OnThisDayTest {
    @Test
    fun `years ago reads naturally`() {
        assertEquals("Há 1 ano", yearsAgoLabel(1))
        assertEquals("Há 4 anos", yearsAgoLabel(4))
    }

    @Test
    fun `the server response decodes`() {
        val body = """
            {"date": "2026-11-18", "years": [
              {"year": 2023, "years_ago": 3, "total": 5, "records": [
                {"index": 7, "arquivo": "IMG_0001.jpg", "caminho": "<masked>", "score": 0.0,
                 "file_mtime": 1700300000.0, "media_type": "image", "thumbnail_url": "/api/thumb/7"}
              ]}
            ]}
        """.trimIndent()

        val response = Json { ignoreUnknownKeys = true }.decodeFromString<OnThisDayResponse>(body)

        assertEquals("2026-11-18", response.date)
        val year = response.years.single()
        assertEquals(listOf(2023, 3, 5), listOf(year.year, year.yearsAgo, year.total))
        assertEquals(7, year.records.single().index)
    }
}
