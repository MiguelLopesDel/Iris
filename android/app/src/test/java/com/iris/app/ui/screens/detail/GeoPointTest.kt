package com.iris.app.ui.screens.detail

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class GeoPointTest {
    private fun curated(json: String): JsonObject = Json.parseToJsonElement(json).jsonObject

    @Test
    fun `the server's gps becomes a point`() {
        val point = GeoPoint.from(curated("""{"gps": {"lat": 41.1579, "lon": -8.6291}, "location_label": "Porto, PT"}"""))

        assertEquals(GeoPoint(41.1579, -8.6291), point)
    }

    @Test
    fun `no gps, or a position that is not real, gives no point`() {
        assertNull(GeoPoint.from(curated("""{"gps": null}""")))
        assertNull(GeoPoint.from(curated("""{"location_label": "Porto, PT"}""")))
        assertNull(GeoPoint.from(curated("""{"gps": {"lat": 0, "lon": 0}}""")))
        assertNull(GeoPoint.from(curated("""{"gps": {"lat": 120, "lon": 10}}""")))
        assertNull(GeoPoint.from(null))
    }

    @Test
    fun `coordinates read with hemispheres in Portuguese`() {
        assertEquals("41,15790° N · 8,62910° O", GeoPoint(41.1579, -8.6291).label)
        assertEquals("22,90680° S · 43,17290° O", GeoPoint(-22.9068, -43.1729).label)
    }

    @Test
    fun `the maps link keeps decimal points and names the pin`() {
        val point = GeoPoint(41.1579, -8.6291)

        assertEquals("geo:41.157900,-8.629100?q=41.157900,-8.629100(Porto%2C%20PT)", point.geoUri("Porto, PT"))
        assertEquals("geo:41.157900,-8.629100?q=41.157900,-8.629100", point.geoUri(null))
        assertEquals("https://www.google.com/maps/search/?api=1&query=41.157900,-8.629100", point.webUrl())
    }
}
