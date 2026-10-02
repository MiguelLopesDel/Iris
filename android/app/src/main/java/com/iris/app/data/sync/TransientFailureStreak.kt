package com.iris.app.data.sync

import java.util.concurrent.atomic.AtomicInteger

/**
 * Counts transient upload failures in a row across concurrent workers. One
 * failure says little (a slow file, a dropped request); several in a row with
 * no success between them say the link or the server is down, and then
 * claiming more work only produces more failures.
 */
internal class TransientFailureStreak(private val limit: Int) {
    private val count = AtomicInteger(0)

    init {
        require(limit > 0) { "limit must be positive" }
    }

    /** Records one failure; true once [limit] failures happened with no success between them. */
    fun recordFailure(): Boolean = count.incrementAndGet() >= limit

    fun reset() = count.set(0)
}
