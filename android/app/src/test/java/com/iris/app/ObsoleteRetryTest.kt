package com.iris.app

import com.iris.app.data.sync.ObsoleteRetry
import com.iris.app.data.sync.ObsoleteRetry.Action
import com.iris.app.data.sync.ObsoleteRetry.Work
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ObsoleteRetryTest {

    private val waitingToRetry = Work(enqueued = true, runAttemptCount = 8)
    private val scheduledNormally = Work(enqueued = true, runAttemptCount = 0)
    private val running = Work(enqueued = false, runAttemptCount = 3)

    @Test
    fun `a manual run that completes puts a periodic sync stuck in backoff back on schedule`() {
        // The device's case: the periodic sync retried eight times and waited
        // hours, while manual syncs completed and the screen said "not finished".
        assertEquals(Action.RESET_PERIODIC, ObsoleteRetry.after(completedRunWasPeriodic = false, listOf(waitingToRetry)))
    }

    @Test
    fun `a periodic run that completes drops a manual sync waiting to retry`() {
        assertEquals(Action.CANCEL_ONE_TIME, ObsoleteRetry.after(completedRunWasPeriodic = true, listOf(waitingToRetry)))
    }

    @Test
    fun `work that is not waiting to retry is left alone`() {
        assertEquals(Action.NONE, ObsoleteRetry.after(false, listOf(scheduledNormally)))
        assertEquals(Action.NONE, ObsoleteRetry.after(true, listOf(running)))
        assertEquals(Action.NONE, ObsoleteRetry.after(false, emptyList()))
    }

    @Test
    fun `the screen reports a pending retry only for enqueued work with failed attempts`() {
        assertTrue(ObsoleteRetry.inBackoff(listOf(scheduledNormally, waitingToRetry)))
        assertFalse(ObsoleteRetry.inBackoff(listOf(scheduledNormally, running)))
    }
}
