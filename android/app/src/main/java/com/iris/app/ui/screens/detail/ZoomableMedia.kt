package com.iris.app.ui.screens.detail

import androidx.compose.animation.core.animate
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.gestures.calculatePan
import androidx.compose.foundation.gestures.calculateZoom
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material3.Icon
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clipToBounds
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.toSize
import coil.compose.AsyncImage
import coil.request.ImageRequest
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/** What a page shows: a photo, or a video's still with a play button until it plays. */
internal data class MediaContent(
    val key: String,
    /** What Coil loads: an URL for Iris's items, a content URI for the phone's. */
    val image: Any?,
    val placeholder: ImageBitmap? = null,
    val isVideo: Boolean = false,
    val description: String = "",
)

/**
 * A photo or video filling its page, with the gestures of a phone gallery:
 *
 * - tap: show or hide the controls (after the double-tap window, so a double
 *   tap is not taken for two taps);
 * - double tap: zoom in at that point, or back out;
 * - double tap and drag: zoom with one finger, down to enlarge, up to shrink;
 * - two fingers: zoom; once zoomed, one finger pans, and pushing past the
 *   photo's edge turns the page;
 * - unzoomed, sideways drags are the pager's; dragging down shrinks the photo
 *   and closes the viewer, dragging up opens the information panel.
 */
@Composable
internal fun ZoomableMedia(
    content: MediaContent,
    isCurrent: Boolean,
    onTap: () -> Unit,
    onZoomChanged: (Boolean) -> Unit,
    onSwipe: (ViewerGestures.Swipe) -> Unit,
    onTurnPage: (Int) -> Unit,
    video: @Composable () -> Unit,
) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val density = LocalDensity.current
    val swipeThreshold = with(density) { 96.dp.toPx() }
    val edgeThreshold = with(density) { 72.dp.toPx() }

    var scale by remember(content.key) { mutableFloatStateOf(1f) }
    var offset by remember(content.key) { mutableStateOf(Offset.Zero) }
    var dragY by remember(content.key) { mutableFloatStateOf(0f) }
    var size by remember { mutableStateOf(Size.Zero) }
    var playing by remember(content.key) { mutableStateOf(false) }
    // The previous tap, to tell a double tap from two taps.
    var lastTapAt by remember { mutableLongStateOf(0L) }
    var lastTapPosition by remember { mutableStateOf(Offset.Zero) }
    var pendingTap by remember { mutableStateOf<Job?>(null) }
    val zoomable = !content.isVideo

    // Leaving a page puts it back the way it opened.
    LaunchedEffect(isCurrent) {
        if (!isCurrent) {
            scale = 1f
            offset = Offset.Zero
            playing = false
        }
    }

    fun zoomTo(target: Float, point: Offset) {
        val startScale = scale
        val startOffset = offset
        scope.launch {
            animate(startScale, target) { value, _ ->
                scale = value
                offset = if (target > startScale) {
                    ViewerGestures.offsetKeeping(point, size, value)
                } else {
                    // Zooming out: shrink the translation along with the scale.
                    val progress = if (startScale > 1f) (value - 1f) / (startScale - 1f) else 0f
                    startOffset * progress
                }
            }
            onZoomChanged(scale > 1f)
        }
    }

    Box(
        modifier = Modifier
            .fillMaxSize()
            // A zoomed image stays inside the page, off the status bar and the next page.
            .clipToBounds()
            .onSizeChanged { size = it.toSize() }
            .pointerInput(content.key, playing) {
                if (playing) return@pointerInput
                val doubleTapTimeout = viewConfiguration.doubleTapTimeoutMillis
                val slop = viewConfiguration.touchSlop
                awaitEachGesture {
                    val down = awaitFirstDown(requireUnconsumed = false)
                    val secondTap = zoomable &&
                        down.uptimeMillis - lastTapAt <= doubleTapTimeout &&
                        (down.position - lastTapPosition).getDistance() < slop * 6
                    if (secondTap) pendingTap?.cancel()

                    var mode = Mode.NONE
                    var total = Offset.Zero
                    var overscrollX = 0f
                    val startScale = scale
                    var upAt = down.uptimeMillis
                    var consumedElsewhere = false
                    do {
                        val event = awaitPointerEvent()
                        val pressed = event.changes.count { it.pressed }
                        upAt = event.changes.maxOf { it.uptimeMillis }
                        if (event.changes.any { it.isConsumed }) consumedElsewhere = true
                        if (zoomable && pressed >= 2) {
                            mode = Mode.TRANSFORM
                            scale = (scale * event.calculateZoom()).coerceIn(1f, ViewerGestures.MAX_SCALE)
                            offset = if (scale > 1f) {
                                ViewerGestures.clampOffset(offset + event.calculatePan(), size, scale)
                            } else {
                                Offset.Zero
                            }
                            onZoomChanged(scale > 1f)
                            event.changes.forEach { it.consume() }
                            continue
                        }
                        if (mode == Mode.TRANSFORM) continue
                        val pan = event.calculatePan()
                        total += pan
                        when {
                            mode == Mode.QUICK_ZOOM || (secondTap && mode == Mode.NONE && total.getDistance() > slop) -> {
                                mode = Mode.QUICK_ZOOM
                                scale = ViewerGestures.quickZoomScale(startScale, total.y, size.height)
                                offset = if (scale > 1f) ViewerGestures.offsetKeeping(down.position, size, scale) else Offset.Zero
                                event.changes.forEach { it.consume() }
                            }
                            scale > 1f -> {
                                // A still finger is a tap (the double tap that zooms back out).
                                if (mode == Mode.PAN || total.getDistance() > slop) {
                                    mode = Mode.PAN
                                    val wanted = offset + pan
                                    val clamped = ViewerGestures.clampOffset(wanted, size, scale)
                                    overscrollX += wanted.x - clamped.x
                                    offset = clamped
                                    event.changes.forEach { it.consume() }
                                }
                            }
                            else -> {
                                if (mode == Mode.NONE && ViewerGestures.isVertical(total, slop)) mode = Mode.VERTICAL
                                if (mode == Mode.VERTICAL) {
                                    dragY = total.y
                                    event.changes.forEach { it.consume() }
                                } else if (total.getDistance() > slop) {
                                    // Sideways: the pager's, not a tap.
                                    mode = Mode.SIDEWAYS
                                }
                            }
                        }
                    } while (event.changes.any { it.pressed })

                    when (mode) {
                        Mode.VERTICAL -> {
                            val verdict = ViewerGestures.swipeVerdict(dragY, swipeThreshold)
                            val from = dragY
                            if (verdict == ViewerGestures.Swipe.CLOSE) {
                                // Carry on down, shrinking and fading, then close.
                                scope.launch {
                                    animate(from, size.height * 0.6f, animationSpec = tween(160)) { value, _ -> dragY = value }
                                    onSwipe(verdict)
                                }
                            } else {
                                onSwipe(verdict)
                                scope.launch { animate(from, 0f) { value, _ -> dragY = value } }
                            }
                        }
                        Mode.PAN -> ViewerGestures.edgePage(overscrollX, edgeThreshold).takeIf { it != 0 }?.let(onTurnPage)
                        Mode.QUICK_ZOOM, Mode.TRANSFORM -> {
                            if (scale <= 1f) offset = Offset.Zero
                            onZoomChanged(scale > 1f)
                            lastTapAt = 0L
                        }
                        Mode.SIDEWAYS -> Unit
                        Mode.NONE -> when {
                            consumedElsewhere -> Unit // a button on the page (play) took it
                            secondTap -> {
                                lastTapAt = 0L
                                if (scale > 1f) zoomTo(1f, down.position) else zoomTo(ViewerGestures.DOUBLE_TAP_SCALE, down.position)
                            }
                            !zoomable -> onTap()
                            else -> {
                                lastTapAt = upAt
                                lastTapPosition = down.position
                                pendingTap = scope.launch {
                                    delay(doubleTapTimeout)
                                    onTap()
                                }
                            }
                        }
                    }
                }
            }
            .graphicsLayer {
                val (look, fade) = ViewerGestures.closingLook(dragY, size.height)
                // Follows the finger down while closing; the panel takes over going up.
                translationY = dragY.coerceAtLeast(0f)
                scaleX = look
                scaleY = look
                alpha = fade
            },
        contentAlignment = Alignment.Center
    ) {
        if (content.isVideo) {
            if (playing) {
                video()
            } else {
                // A still with a large play button: plain to see, and the pager
                // can still be swiped, which a live player view would capture.
                AsyncImage(
                    model = content.image,
                    contentDescription = content.description,
                    contentScale = ContentScale.Fit,
                    modifier = Modifier.fillMaxSize()
                )
                Box(
                    modifier = Modifier
                        .size(80.dp)
                        .background(Color.Black.copy(alpha = 0.55f), CircleShape)
                        .pointerInput(Unit) { detectTapGestures(onTap = { playing = true }) },
                    contentAlignment = Alignment.Center
                ) {
                    Icon(
                        Icons.Default.PlayArrow,
                        contentDescription = "Reproduzir vídeo",
                        tint = Color.White,
                        modifier = Modifier.size(48.dp)
                    )
                }
            }
        } else {
            Box(
                modifier = Modifier
                    .fillMaxSize()
                    .graphicsLayer {
                        scaleX = scale
                        scaleY = scale
                        translationX = offset.x
                        translationY = offset.y
                    },
                contentAlignment = Alignment.Center
            ) {
                content.placeholder?.let {
                    androidx.compose.foundation.Image(
                        bitmap = it,
                        contentDescription = null,
                        contentScale = ContentScale.Fit,
                        modifier = Modifier.fillMaxSize()
                    )
                }
                AsyncImage(
                    model = ImageRequest.Builder(context).data(content.image).crossfade(true).build(),
                    contentDescription = content.description,
                    contentScale = ContentScale.Fit,
                    modifier = Modifier.fillMaxSize()
                )
            }
        }
    }
}

private enum class Mode { NONE, TRANSFORM, PAN, QUICK_ZOOM, VERTICAL, SIDEWAYS }
