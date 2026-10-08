package com.iris.app.data.sync

import com.iris.app.data.sync.IncompleteRunPolicy.Next
import org.junit.Assert.assertEquals
import org.junit.Test

class IncompleteRunPolicyTest {
    @Test
    fun aRunCutByANetworkDropWaitsOnlyForTheNetwork() {
        assertEquals(Next.CONTINUE_WHEN_CONNECTED, IncompleteRunPolicy.after(networkAvailable = false, serverReachable = false, attemptsWithoutProgress = 0))
    }

    @Test
    fun aRunCutByANetworkSwitchContinuesAtOnce() {
        assertEquals(Next.CONTINUE_WHEN_CONNECTED, IncompleteRunPolicy.after(networkAvailable = true, serverReachable = true, attemptsWithoutProgress = 1))
    }

    @Test
    fun aServerThatIsDownKeepsTheBackoff() {
        assertEquals(Next.BACK_OFF, IncompleteRunPolicy.after(networkAvailable = true, serverReachable = false, attemptsWithoutProgress = 0))
    }

    @Test
    fun runsThatKeepFailingWithoutProgressFallBackToTheBackoff() {
        val limit = IncompleteRunPolicy.MAX_CONTINUATIONS_WITHOUT_PROGRESS
        assertEquals(Next.CONTINUE_WHEN_CONNECTED, IncompleteRunPolicy.after(true, true, limit - 1))
        assertEquals(Next.BACK_OFF, IncompleteRunPolicy.after(true, true, limit))
        assertEquals(Next.BACK_OFF, IncompleteRunPolicy.after(false, false, limit))
    }
}
