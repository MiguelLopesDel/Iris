package com.iris.app.data.model

/**
 * The identity of a MediaStore item, whichever collection URI names it.
 *
 * The same photo has several URIs: the sync scanner reads the typed
 * collections (`content://media/external/images/media/42`), while the device
 * gallery pages through the files collection (`content://media/external/file/42`).
 * Comparing the URIs as strings never matched, so every device cell looked
 * "only on this device" even after it had been uploaded.
 */
object MediaStoreKey {
    private val mediaStoreUri = Regex(
        "^content://media/([^/]+)/(?:file|images/media|video/media|audio/media|downloads)/(\\d+)$"
    )

    /** `volume/id` for a MediaStore URI; any other URI is its own key. */
    fun of(uri: String): String {
        val match = mediaStoreUri.find(uri) ?: return uri
        val volume = match.groupValues[1].let { if (it == "external_primary") "external" else it }
        return "$volume/${match.groupValues[2]}"
    }
}
