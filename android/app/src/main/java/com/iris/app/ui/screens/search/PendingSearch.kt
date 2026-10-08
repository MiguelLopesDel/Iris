package com.iris.app.ui.screens.search

/**
 * A query handed to the search screen from elsewhere ("Buscar no Iris" on the
 * text of a photo). Held in memory and taken once by the screen that runs it.
 */
internal object PendingSearch {
    @Volatile
    private var query: String? = null

    fun set(text: String) {
        // One line, short enough for the search field: the start of a long text is what matters.
        query = text.lineSequence().map { it.trim() }.filter { it.isNotEmpty() }
            .joinToString(" ").take(MAX_LENGTH).trim().ifEmpty { null }
    }

    fun take(): String? = query.also { query = null }

    private const val MAX_LENGTH = 120
}
