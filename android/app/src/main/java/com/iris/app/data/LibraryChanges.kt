package com.iris.app.data

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/**
 * Tells screens that keep a copy of the library (the gallery) that the app
 * itself changed it, for instance by moving a photo to the trash. A counter
 * rather than an event, so a screen that was not listening at that moment
 * still sees it changed when it looks.
 */
object LibraryChanges {
    private val _version = MutableStateFlow(0)
    val version: StateFlow<Int> = _version.asStateFlow()

    fun changed() {
        _version.value += 1
    }
}
