package com.iris.app.ui.screens.detail

import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.net.Uri
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.doubleOrNull
import java.net.URLEncoder
import java.util.Locale
import kotlin.math.abs

/**
 * Where a photo or video was taken, from the GPS the server read in its file
 * (EXIF for photos, the container's location for videos), and how to show
 * that place in Google Maps.
 */
internal data class GeoPoint(val latitude: Double, val longitude: Double) {

    /** "41,15790° N · 8,62910° O", the way a person reads coordinates. */
    val label: String
        get() {
            val br = Locale("pt", "BR")
            val lat = String.format(br, "%.5f° %s", abs(latitude), if (latitude >= 0) "N" else "S")
            val lon = String.format(br, "%.5f° %s", abs(longitude), if (longitude >= 0) "L" else "O")
            return "$lat · $lon"
        }

    /** Opens the point in the Maps app, with [place] as the pin's name. */
    fun geoUri(place: String?): String {
        val point = "${format(latitude)},${format(longitude)}"
        val name = place?.takeIf { it.isNotBlank() }
            ?.let { "(${URLEncoder.encode(it, Charsets.UTF_8.name()).replace("+", "%20")})" }
            .orEmpty()
        return "geo:$point?q=$point$name"
    }

    /** The same point on the web, for a phone without the Maps app. */
    fun webUrl(): String = "https://www.google.com/maps/search/?api=1&query=${format(latitude)},${format(longitude)}"

    private fun format(value: Double) = String.format(Locale.US, "%.6f", value)

    companion object {
        private const val MAPS_PACKAGE = "com.google.android.apps.maps"

        /**
         * The point in the server's curated metadata (`"gps": {"lat": …, "lon": …}`),
         * or null when there is none or it is not a real position (out of
         * range, or 0,0, which cameras write when they have no fix).
         */
        fun from(curated: JsonObject?): GeoPoint? {
            val gps = curated?.get("gps") as? JsonObject ?: return null
            val lat = (gps["lat"] as? JsonPrimitive)?.doubleOrNull ?: return null
            val lon = (gps["lon"] as? JsonPrimitive)?.doubleOrNull ?: return null
            if (lat !in -90.0..90.0 || lon !in -180.0..180.0) return null
            if (lat == 0.0 && lon == 0.0) return null
            return GeoPoint(lat, lon)
        }

        /**
         * Shows [point] in Google Maps: the app when it is installed, else the
         * same place on the web. The position leaves the phone only here, when
         * the person asks for it.
         */
        fun openInGoogleMaps(context: Context, point: GeoPoint, place: String?) {
            val app = Intent(Intent.ACTION_VIEW, Uri.parse(point.geoUri(place))).setPackage(MAPS_PACKAGE)
            try {
                context.startActivity(app)
            } catch (_: ActivityNotFoundException) {
                context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(point.webUrl())))
            }
        }
    }
}
