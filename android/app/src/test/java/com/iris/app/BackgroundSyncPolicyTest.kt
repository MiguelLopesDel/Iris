package com.iris.app

import com.iris.app.data.sync.BackgroundSyncPolicy
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class BackgroundSyncPolicyTest {
    @Test
    fun `signed in account keeps server sync scheduled without photo backup opt in`() {
        assertTrue(BackgroundSyncPolicy.shouldSchedulePeriodicSync(isLoggedIn = true))
    }

    @Test
    fun `logged out account does not keep background server sync scheduled`() {
        assertFalse(BackgroundSyncPolicy.shouldSchedulePeriodicSync(isLoggedIn = false))
    }

    @Test
    fun `periodic checks never bypass Wi-Fi or charging preferences for media work`() {
        assertFalse(BackgroundSyncPolicy.shouldRunMediaWork(true, false, false, true))
        assertFalse(BackgroundSyncPolicy.shouldRunMediaWork(false, true, true, false))
        assertTrue(BackgroundSyncPolicy.shouldRunMediaWork(true, true, true, true))
    }
}
