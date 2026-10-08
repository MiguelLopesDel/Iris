package com.iris.app.ui.screens.detail

/**
 * The photos the viewer swipes through: those of the grid it was opened
 * from, in that grid's order (the gallery, a search, an album, a person).
 *
 * Held in memory between the grid and the viewer. After the process is
 * recreated it is gone, and the viewer shows the one photo it was opened on.
 */
internal object ViewerSequence {
    @Volatile
    private var indices: List<Int> = emptyList()

    fun set(indices: List<Int>) {
        this.indices = indices.distinct()
    }

    /** The sequence to page through, always containing [start]. */
    fun around(start: Int): List<Int> = indices.takeIf { start in it } ?: listOf(start)
}
