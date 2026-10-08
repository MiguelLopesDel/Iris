package com.iris.app.ui.screens.detail

import androidx.compose.runtime.mutableFloatStateOf

/**
 * Where a finger on the information panel takes it, apart from the UI so it
 * can be tested.
 *
 * The panel opens at a base height. Dragging up grows it ([expansion]) until
 * it nearly covers the photo, as a gallery's does; after that the content
 * scrolls. Dragging down first scrolls the content back to its top (the
 * caller sends only what the content did not use), then shrinks the panel,
 * then pulls it down ([pull]) to close it. A pull past [closeDistance], or a
 * fast enough downward release, closes it; a shorter one springs back.
 *
 * Positive deltas go down the screen, as in Compose.
 */
internal class PanelDrag(
    private val maxExpansion: Float,
    private val closeDistance: Float,
    private val closeVelocity: Float = 1_500f,
) {
    // Snapshot state, so the layout follows each change; plain runtime, testable on the JVM.
    private val expansionState = mutableFloatStateOf(0f)
    private val pullState = mutableFloatStateOf(0f)

    var expansion: Float
        get() = expansionState.floatValue
        private set(value) { expansionState.floatValue = value }
    var pull: Float
        get() = pullState.floatValue
        private set(value) { pullState.floatValue = value }

    /**
     * Before the content scrolls: an upward drag lowers a pull and then grows
     * the panel. Returns the part of [dy] it used.
     */
    fun beforeScroll(dy: Float): Float {
        if (dy >= 0f) return 0f
        var left = -dy
        val fromPull = minOf(pull, left)
        pull -= fromPull
        left -= fromPull
        val grown = minOf(maxExpansion - expansion, left).coerceAtLeast(0f)
        expansion += grown
        left -= grown
        return -(-dy - left)
    }

    /**
     * After the content scrolled: what is left of a downward drag (the
     * content is at its top) shrinks the panel and then pulls it down.
     * Returns the part of [dy] it used.
     */
    fun afterScroll(dy: Float): Float {
        if (dy <= 0f) return 0f
        val shrunk = minOf(expansion, dy)
        expansion -= shrunk
        pull += dy - shrunk
        return dy
    }

    /** The finger left the panel moving at [velocity] (down is positive): whether it closes. */
    fun release(velocity: Float): Boolean =
        pull > 0f && (pull >= closeDistance || velocity >= closeVelocity)

    /** Springing back after a short pull, or starting over after closing. */
    fun settlePull(value: Float) {
        pull = value.coerceAtLeast(0f)
    }

    fun reset() {
        expansion = 0f
        pull = 0f
    }
}
