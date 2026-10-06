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

    @Test
    fun `background work does not upload queued media without backup opt in`() {
        assertFalse(
            BackgroundSyncPolicy.shouldProcessMediaQueue(
                allowedByConstraints = true,
                autoBackupEnabled = false,
                forceScan = false,
            )
        )
    }

    @Test
    fun `opted in backup processes the media queue when constraints allow it`() {
        assertTrue(
            BackgroundSyncPolicy.shouldProcessMediaQueue(
                allowedByConstraints = true,
                autoBackupEnabled = true,
                forceScan = false,
            )
        )
    }

    @Test
    fun `manual sync can process queued media without persistent backup opt in`() {
        assertTrue(
            BackgroundSyncPolicy.shouldProcessMediaQueue(
                allowedByConstraints = true,
                autoBackupEnabled = false,
                forceScan = true,
            )
        )
    }

    @Test
    fun `skipping media queue does not clear an obsolete retry`() {
        val queueWasProcessed = BackgroundSyncPolicy.shouldProcessMediaQueue(
            allowedByConstraints = true,
            autoBackupEnabled = false,
            forceScan = false,
        )

        assertFalse(
            BackgroundSyncPolicy.shouldClearObsoleteRetry(
                queueWasProcessed = queueWasProcessed,
                queueCompleted = true,
            )
        )
    }

    @Test
    fun `only a processed completed queue clears an obsolete retry`() {
        assertTrue(BackgroundSyncPolicy.shouldClearObsoleteRetry(queueWasProcessed = true, queueCompleted = true))
        assertFalse(BackgroundSyncPolicy.shouldClearObsoleteRetry(queueWasProcessed = true, queueCompleted = false))
        assertFalse(BackgroundSyncPolicy.shouldClearObsoleteRetry(queueWasProcessed = false, queueCompleted = true))
    }

    @Test
    fun `media constraints pause even manually requested queue work`() {
        assertFalse(
            BackgroundSyncPolicy.shouldProcessMediaQueue(
                allowedByConstraints = false,
                autoBackupEnabled = true,
                forceScan = true,
            )
        )
    }
}
