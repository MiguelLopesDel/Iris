package com.iris.app.ui.components

import android.graphics.Bitmap
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import java.util.Base64
import kotlin.math.sqrt

/** A decoded inline preview: a square grid of ARGB pixels. */
data class ThumbGrid(val side: Int, val pixels: IntArray) {
    override fun equals(other: Any?): Boolean =
        other is ThumbGrid && side == other.side && pixels.contentEquals(other.pixels)

    override fun hashCode(): Int = 31 * side + pixels.contentHashCode()
}

/**
 * Parses the inline preview the server ships inside each record's JSON
 * (see core/thumb_hash.py in the Iris server).
 *
 * The encoding is self-describing: the square's side comes from the decoded
 * byte length rather than a constant agreed with the server, so the server can
 * change its grid resolution without stranding clients already installed.
 *
 * Kept free of Android framework types so the cross-language contract with the
 * Python encoder can be tested on the JVM. Returns null for anything it cannot
 * decode — a missing placeholder is cosmetic and must never surface as an error.
 */
fun decodeThumbGrid(encoded: String?): ThumbGrid? {
    if (encoded.isNullOrBlank()) return null
    return try {
        val raw = Base64.getDecoder().decode(encoded)
        val pixelCount = raw.size / 3
        val side = sqrt(pixelCount.toDouble()).toInt()
        if (side <= 0 || side * side * 3 != raw.size) return null

        val pixels = IntArray(pixelCount) { i ->
            val r = raw[i * 3].toInt() and 0xFF
            val g = raw[i * 3 + 1].toInt() and 0xFF
            val b = raw[i * 3 + 2].toInt() and 0xFF
            (0xFF shl 24) or (r shl 16) or (g shl 8) or b
        }
        ThumbGrid(side, pixels)
    } catch (_: Exception) {
        null
    }
}

/** The grid as a bitmap the gallery can paint before the real thumbnail lands. */
fun decodeThumbHash(encoded: String?): ImageBitmap? {
    val grid = decodeThumbGrid(encoded) ?: return null
    return try {
        Bitmap.createBitmap(grid.pixels, grid.side, grid.side, Bitmap.Config.ARGB_8888)
            .asImageBitmap()
    } catch (_: Exception) {
        null
    }
}
