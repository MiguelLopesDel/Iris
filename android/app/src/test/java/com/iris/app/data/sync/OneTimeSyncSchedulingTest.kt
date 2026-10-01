package com.iris.app.data.sync

import androidx.work.WorkInfo
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.delay
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.Collections

class OneTimeSyncSchedulingTest {

    /** A unique-work chain whose reads and writes yield, so concurrent callers interleave. */
    private class FakeQueue(initial: List<WorkInfo.State>) : OneTimeSyncQueue<String> {
        val states = Collections.synchronizedList(initial.toMutableList())
        val appended = Collections.synchronizedList(mutableListOf<String>())
        val replaced = Collections.synchronizedList(mutableListOf<String>())

        override suspend fun states(): List<WorkInfo.State> {
            val snapshot = synchronized(states) { states.toList() }
            delay(1)
            return snapshot
        }

        override suspend fun append(request: String) {
            delay(1)
            appended += request
            states += WorkInfo.State.BLOCKED
        }

        override suspend fun replace(request: String) {
            delay(1)
            replaced += request
            synchronized(states) {
                states.clear()
                states += WorkInfo.State.ENQUEUED
            }
        }
    }

    @Test
    fun decisions_never_interrupt_a_running_sync() {
        val running = WorkInfo.State.RUNNING
        assertEquals(OneTimeSyncScheduling.Decision.APPEND, OneTimeSyncScheduling.decide(listOf(running)))
        assertEquals(
            OneTimeSyncScheduling.Decision.KEEP,
            OneTimeSyncScheduling.decide(listOf(running, WorkInfo.State.BLOCKED)),
        )
        assertEquals(
            OneTimeSyncScheduling.Decision.KEEP,
            OneTimeSyncScheduling.decide(listOf(running, WorkInfo.State.ENQUEUED)),
        )
        // Only a sync still waiting for constraints is replaced, so new choices apply.
        assertEquals(OneTimeSyncScheduling.Decision.REPLACE, OneTimeSyncScheduling.decide(listOf(WorkInfo.State.ENQUEUED)))
        assertEquals(OneTimeSyncScheduling.Decision.REPLACE, OneTimeSyncScheduling.decide(listOf(WorkInfo.State.SUCCEEDED)))
        assertEquals(OneTimeSyncScheduling.Decision.REPLACE, OneTimeSyncScheduling.decide(emptyList()))
    }

    @Test
    fun concurrent_requests_queue_at_most_one_follow_up() = runBlocking(Dispatchers.Default) {
        val queue = FakeQueue(listOf(WorkInfo.State.RUNNING))

        val decisions = (1..50).map { index ->
            async { OneTimeSyncScheduling.enqueue(queue, "request-$index") }
        }.awaitAll()

        assertEquals(1, decisions.count { it == OneTimeSyncScheduling.Decision.APPEND })
        assertEquals(49, decisions.count { it == OneTimeSyncScheduling.Decision.KEEP })
        assertEquals(1, queue.appended.size)
        assertTrue(queue.replaced.isEmpty())
    }

    @Test
    fun the_same_requests_without_the_lock_do_race() = runBlocking(Dispatchers.Default) {
        // Guards this test's power: the fake must expose the race the lock prevents.
        val queue = FakeQueue(listOf(WorkInfo.State.RUNNING))

        (1..50).map { index ->
            async {
                when (OneTimeSyncScheduling.decide(queue.states())) {
                    OneTimeSyncScheduling.Decision.APPEND -> queue.append("request-$index")
                    OneTimeSyncScheduling.Decision.REPLACE -> queue.replace("request-$index")
                    OneTimeSyncScheduling.Decision.KEEP -> Unit
                }
            }
        }.awaitAll()

        assertTrue("expected the unlocked read-then-write to append more than once", queue.appended.size > 1)
    }
}
