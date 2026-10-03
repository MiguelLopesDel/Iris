package com.iris.app

import com.iris.app.data.local.DeviceMediaChanges
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class DeviceMediaChangesTest {

    @Test
    fun `a never quiet stream of changes still reloads regularly`() = runTest {
        // MIUI-like: a change every 100 ms for 5 s, never 700 ms of silence.
        val changes = flow { repeat(50) { emit(Unit); delay(100) } }
        var reloads = 0
        val job = launch { DeviceMediaChanges.reloadAtMostEvery(changes, 700) { reloads++ } }
        advanceTimeBy(5_000)
        job.cancel()
        assertTrue("reloaded $reloads times", reloads in 5..8)
    }

    @Test
    fun `a burst from one shot becomes a single reload`() = runTest {
        val changes = flow { repeat(4) { emit(Unit); delay(50) } }
        var reloads = 0
        val job = launch { DeviceMediaChanges.reloadAtMostEvery(changes, 700) { reloads++ } }
        advanceTimeBy(3_000)
        job.cancel()
        // The first change starts the wait; the rest fold into the next reload at most.
        assertTrue("reloaded $reloads times", reloads in 1..2)
    }

    @Test
    fun `a reload slower than the interval still completes while changes keep coming`() = runTest {
        // The reload must be awaited by the collector (not launched and cancelled
        // by the next change): every one that starts here also finishes.
        val changes = flow { repeat(100) { emit(Unit); delay(100) } }
        var started = 0
        var finished = 0
        val job = launch {
            DeviceMediaChanges.reloadAtMostEvery(changes, 700) {
                started++
                delay(1_500)
                finished++
            }
        }
        advanceTimeBy(10_000)
        job.cancel()
        assertTrue("finished $finished of $started", finished >= 3 && started - finished <= 1)
    }

    @Test
    fun `no change means no reload`() = runTest {
        var reloads = 0
        DeviceMediaChanges.reloadAtMostEvery(flow { }, 700) { reloads++ }
        assertEquals(0, reloads)
    }
}
