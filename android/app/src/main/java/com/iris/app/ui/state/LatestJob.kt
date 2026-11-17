package com.iris.app.ui.state

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch

/**
 * One request at a time for a piece of screen state: starting a new one
 * cancels the one still running, so an older response can never land over a
 * newer one (a slow search finishing after the next, a page of the previous
 * filter arriving after the filter changed).
 *
 * Relies on the repositories rethrowing cancellation instead of turning it
 * into a failed result, which they do.
 */
class LatestJob {
    private var job: Job? = null

    val isActive: Boolean get() = job?.isActive == true

    fun launch(scope: CoroutineScope, block: suspend CoroutineScope.() -> Unit): Job {
        job?.cancel()
        return scope.launch(block = block).also { job = it }
    }

    fun cancel() {
        job?.cancel()
        job = null
    }
}
