package com.iris.app.data.sync

/**
 * What a sync run that emptied the queue does about the other unique work's
 * pending retry.
 *
 * Periodic and manual syncs are separate works. When one of them returns
 * "retry", it waits in exponential backoff, and the sync screen says the last
 * sync did not finish while any work waits like that. A later run of the other
 * work that empties the queue makes that retry obsolete, yet left it waiting
 * for hours, and the screen kept saying the sync had not finished.
 */
internal object ObsoleteRetry {

    enum class Action {
        NONE,
        /** Put the periodic work back on its normal schedule, without the backoff. */
        RESET_PERIODIC,
        /** Drop the manual work waiting to retry; the queue it retried is done. */
        CANCEL_ONE_TIME,
    }

    /** The parts of a WorkInfo that matter here. */
    data class Work(val enqueued: Boolean, val runAttemptCount: Int)

    fun inBackoff(works: List<Work>): Boolean = works.any { it.enqueued && it.runAttemptCount > 0 }

    fun after(completedRunWasPeriodic: Boolean, otherWork: List<Work>): Action = when {
        !inBackoff(otherWork) -> Action.NONE
        completedRunWasPeriodic -> Action.CANCEL_ONE_TIME
        else -> Action.RESET_PERIODIC
    }
}
