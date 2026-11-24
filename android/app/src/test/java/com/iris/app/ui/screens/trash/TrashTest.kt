package com.iris.app.ui.screens.trash

import com.iris.app.data.model.TrashPage
import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Test
import java.time.OffsetDateTime

class TrashTest {
    private val now = OffsetDateTime.parse("2026-11-18T12:00:00+00:00")

    @Test
    fun `days left count down to the last day`() {
        assertEquals(12, daysLeft("2026-11-30T13:00:00+00:00", now))
        assertEquals(0, daysLeft("2026-11-18T20:00:00+00:00", now))
        assertEquals(0, daysLeft("2026-11-01T00:00:00+00:00", now))
        assertEquals(0, daysLeft("not a date", now))
    }

    @Test
    fun `labels say how long until the photo is gone`() {
        assertEquals("Último dia", daysLeftLabel(0))
        assertEquals("1 dia", daysLeftLabel(1))
        assertEquals("12 dias", daysLeftLabel(12))
    }

    @Test
    fun `the server's trash page decodes`() {
        val page = Json { ignoreUnknownKeys = true }.decodeFromString<TrashPage>(
            """{"items": [{"id": 3, "name": "IMG_0001.jpg", "size_bytes": 10, "trashed_at": "x",
               "purge_after": "2026-12-01T00:00:00+00:00", "thumbnail_url": "/api/trash/items/3/thumbnail"}],
               "next_before": null, "trash_days": 30}"""
        )
        assertEquals(3, page.items.single().id)
        assertEquals(30, page.trashDays)
    }
}
