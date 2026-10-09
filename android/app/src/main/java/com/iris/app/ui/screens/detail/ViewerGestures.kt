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

    /**
     * Double tap and drag ("quick zoom"): the scale follows the finger of the
     * second tap, down to enlarge and up to shrink, as galleries do. A drag of
     * a quarter of the screen height doubles or halves the scale.
     */
    fun quickZoomScale(startScale: Float, dragY: Float, height: Float): Float {
        if (height <= 0f) return startScale
        val factor = Math.pow(2.0, (dragY / (height / 4f)).toDouble()).toFloat()
        return (startScale * factor).coerceIn(1f, MAX_SCALE)
    }

    /**
     * Panning a zoomed photo past its edge: far enough, it moves to the next
     * photo (dragging left past the right edge, -1 from the drag, so +1) or
     * the previous one; 0 keeps it.
     */
    fun edgePage(overscrollX: Float, threshold: Float): Int = when {
        overscrollX <= -threshold -> 1
        overscrollX >= threshold -> -1
        else -> 0
    }

    /** How a photo dragged down to close looks: it shrinks and fades as it goes. */
    fun closingLook(dragY: Float, height: Float): Pair<Float, Float> {
        if (height <= 0f || dragY <= 0f) return 1f to 1f
        val progress = (dragY / height).coerceIn(0f, 1f)
        return (1f - 0.35f * progress) to (1f - 0.6f * progress)
    }
}

