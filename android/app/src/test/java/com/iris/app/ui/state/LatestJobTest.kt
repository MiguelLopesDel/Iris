package com.iris.app.ui.state

import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.test.advanceUntilIdle
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class LatestJobTest {

    @Test
    fun `a slow older request never lands over a newer one`() = runTest {
        val latest = LatestJob()
        val slow = CompletableDeferred<String>()
        val shown = mutableListOf<String>()

        latest.launch(this) { shown += slow.await() }
        latest.launch(this) { shown += "new" }
        advanceUntilIdle()
        slow.complete("old")
        advanceUntilIdle()

        assertEquals(listOf("new"), shown)
    }

    @Test
    fun `cancel stops the running request`() = runTest {
        val latest = LatestJob()
        val never = CompletableDeferred<Unit>()
        var finished = false

        latest.launch(this) { never.await(); finished = true }
        latest.cancel()
        never.complete(Unit)
        advanceUntilIdle()

        assertFalse(finished)
        assertFalse(latest.isActive)
    }
}
