package com.iris.app.data.sync

import org.junit.Assert.assertEquals
import org.junit.Test

class SyncNotificationsTest {
    @Test
    fun a_run_that_has_not_sent_anything_is_still_preparing() {
        assertEquals(SyncNotifications.Content.Preparing, SyncNotifications.content(UploadSpeedSnapshot(), 500L))
        assertEquals(
            SyncNotifications.Content.Preparing,
            SyncNotifications.content(UploadSpeedSnapshot(running = true, runNumber = 1), 500L),
        )
        // A finished run's totals are not a live upload.
        assertEquals(
            SyncNotifications.Content.Preparing,
            SyncNotifications.content(UploadSpeedSnapshot(running = false, runBytes = 9L, runItems = 1L), 0L),
        )
    }

    @Test
    fun an_uploading_run_reports_its_live_rate_items_and_what_is_left() {
        val speed = UploadSpeedSnapshot(running = true, currentBytesPerSecond = 17_000_000.0, runBytes = 1L, runItems = 12L)
        assertEquals(
            SyncNotifications.Content.Uploading(17_000_000.0, 12L, 3_000_000_000L),
            SyncNotifications.content(speed, 3_000_000_000L),
        )
    }
}
