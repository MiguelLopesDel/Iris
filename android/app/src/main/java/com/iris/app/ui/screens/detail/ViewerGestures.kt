package com.iris.app.ui.screens.detail

import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import kotlin.math.abs

/**
 * The arithmetic behind the viewer's gestures, apart from Compose so it can
 * be tested. The image is scaled around its centre and then translated.
 */
internal object ViewerGestures {
    const val MAX_SCALE = 5f

    /** Double tap zooms to this, the step a gallery app takes. */
    const val DOUBLE_TAP_SCALE = 2.5f

    /** Largest translation that keeps the scaled image covering the screen. */
    fun clampOffset(offset: Offset, size: Size, scale: Float): Offset {
        if (scale <= 1f) return Offset.Zero
        val maxX = size.width * (scale - 1f) / 2f
        val maxY = size.height * (scale - 1f) / 2f
        return Offset(offset.x.coerceIn(-maxX, maxX), offset.y.coerceIn(-maxY, maxY))
    }

    /** The translation that keeps the tapped point under the finger at [scale]. */
    fun offsetKeeping(point: Offset, size: Size, scale: Float): Offset {
        val center = Offset(size.width / 2f, size.height / 2f)
        return clampOffset((center - point) * (scale - 1f), size, scale)
    }

    enum class Swipe { CLOSE, SHOW_INFO, NONE }

    /**
     * What a vertical drag of [dragY] pixels means once released: down far
     * enough closes the viewer, up far enough opens the information panel,
     * anything shorter snaps back.
     */
    fun swipeVerdict(dragY: Float, threshold: Float): Swipe = when {
        dragY >= threshold -> Swipe.CLOSE
        dragY <= -threshold -> Swipe.SHOW_INFO
        else -> Swipe.NONE
    }

    /** A drag is vertical once it moved past [slop] mostly up or down; sideways is the pager's. */
    fun isVertical(total: Offset, slop: Float): Boolean =
        abs(total.y) > slop && abs(total.y) > abs(total.x) * 1.5f
}
