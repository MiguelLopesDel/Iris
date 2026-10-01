package com.iris.app.data.sync

import androidx.work.WorkInfo
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

/** The account's unique one-time sync work, as [OneTimeSyncScheduling] needs to see and change it. */
internal interface OneTimeSyncQueue<R> {
    suspend fun states(): List<WorkInfo.State>

    /** Runs [request] after the current work; returns once WorkManager has recorded it. */
    suspend fun append(request: R)

    /** Replaces the current work with [request]; returns once WorkManager has recorded it. */
    suspend fun replace(request: R)
}

/**
 * Queues a one-time sync without interrupting one that is uploading:
 * replacing a running sync cancelled it and restarted it from its last
 * acknowledged chunk each time the app started or a setting changed.
 * Instead the request runs after it, so media that appeared after its scan
 * is still found; one follow-up is enough. With nothing running, a sync
 * still waiting for constraints is replaced, so a changed Wi-Fi or charging
 * choice applies.
 *
 * Reading the state and enqueueing are one step under a process-wide lock,
 * and each enqueue is awaited inside it. Otherwise app start, login, setting
 * changes and the sync button, arriving together, could each see "running,
 * nothing queued" and each append a follow-up.
 */
internal object OneTimeSyncScheduling {
    private val mutex = Mutex()

    enum class Decision { KEEP, APPEND, REPLACE }

    fun decide(states: List<WorkInfo.State>): Decision {
        val running = WorkInfo.State.RUNNING in states
        val followUpQueued = states.any { it == WorkInfo.State.ENQUEUED || it == WorkInfo.State.BLOCKED }
        return when {
            running && followUpQueued -> Decision.KEEP
            running -> Decision.APPEND
            else -> Decision.REPLACE
        }
    }

    suspend fun <R> enqueue(queue: OneTimeSyncQueue<R>, request: R): Decision = mutex.withLock {
        val decision = decide(queue.states())
        when (decision) {
            Decision.KEEP -> Unit
            Decision.APPEND -> queue.append(request)
            Decision.REPLACE -> queue.replace(request)
        }
        decision
    }
}
