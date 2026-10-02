package com.iris.app.data.sync

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class TransientFailureStreakTest {

    @Test
    fun `isolated failures do not stop the queue`() {
        val streak = TransientFailureStreak(3)
        repeat(10) {
            assertFalse(streak.recordFailure())
            assertFalse(streak.recordFailure())
            streak.reset()
        }
    }

    @Test
    fun `consecutive failures stop the queue`() {
        val streak = TransientFailureStreak(3)
        assertFalse(streak.recordFailure())
        assertFalse(streak.recordFailure())
        assertTrue(streak.recordFailure())
        assertTrue(streak.recordFailure())
    }
}
