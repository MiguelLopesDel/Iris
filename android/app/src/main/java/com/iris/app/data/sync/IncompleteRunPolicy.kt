package com.iris.app.data.sync

/**
 * What follows a sync run that did not empty the queue.
 *
 * A plain retry waited WorkManager's exponential backoff on top of the
 * network constraint, so a sync cut by a Wi-Fi switch or a short drop sat
 * idle for minutes after the network was back. A continuation that only
 * waits for the network resumes as soon as it returns. It is kept for runs
 * that make progress: a server that answers but keeps failing the uploads
 * would otherwise be retried in a tight loop, so after a few continuations
 * in a row without progress, or with the network up and the server down, the
 * backoff applies again.
 */
internal object IncompleteRunPolicy {
    enum class Next {
        /** Run again as soon as the network allows it (at once if it already does). */
        CONTINUE_WHEN_CONNECTED,
        /** Leave it to WorkManager's retry backoff. */
        BACK_OFF,
    }

    const val MAX_CONTINUATIONS_WITHOUT_PROGRESS = 3

    /** [attemptsWithoutProgress] counts this run when it confirmed nothing. */
    fun after(networkAvailable: Boolean, serverReachable: Boolean, attemptsWithoutProgress: Int): Next = when {
        attemptsWithoutProgress >= MAX_CONTINUATIONS_WITHOUT_PROGRESS -> Next.BACK_OFF
        !networkAvailable -> Next.CONTINUE_WHEN_CONNECTED
        serverReachable -> Next.CONTINUE_WHEN_CONNECTED
        else -> Next.BACK_OFF
    }
}
