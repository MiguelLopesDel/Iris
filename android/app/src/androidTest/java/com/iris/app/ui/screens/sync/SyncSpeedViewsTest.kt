package com.iris.app.ui.screens.sync

import android.graphics.Bitmap
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.hasClickAction
import androidx.compose.runtime.setValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.getValue
import androidx.compose.ui.graphics.asAndroidBitmap
import androidx.compose.ui.test.captureToImage
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.onRoot
import androidx.compose.ui.unit.dp
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import androidx.work.WorkInfo
import com.iris.app.data.model.SyncRun
import com.iris.app.data.model.SyncRunOutcome
import com.iris.app.data.model.SyncRunTrigger
import com.iris.app.data.sync.UploadSpeedSnapshot
import com.iris.app.ui.theme.IrisTheme
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File

@RunWith(AndroidJUnit4::class)
class SyncSpeedViewsTest {
    @get:Rule
    val compose = createAndroidComposeRule<PickerTestActivity>()

    private fun run(
        id: Long,
        outcome: SyncRunOutcome,
        foreground: Boolean,
        bytes: Long = 0L,
        items: Long = 0L,
        uploadMillis: Long = 0L,
        stopReason: Int? = null,
        detail: String? = null,
    ) = SyncRun(
        id = id,
        startedAtMillis = 1_790_000_000_000L + id * 60_000L,
        endedAtMillis = if (outcome == SyncRunOutcome.RUNNING) null else 1_790_000_000_000L + id * 60_000L + uploadMillis,
        trigger = if (foreground) SyncRunTrigger.MANUAL else SyncRunTrigger.AUTOMATIC,
        startedInForeground = foreground,
        bytes = bytes,
        items = items,
        uploadMillis = uploadMillis,
        outcome = outcome,
        stopReason = stopReason,
        detail = detail,
    )

    @Test
    fun speed_panel_and_history_show_rates_sizes_and_why_runs_ended() {
        compose.setContent {
            IrisTheme {
                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .background(MaterialTheme.colorScheme.background)
                        .padding(16.dp),
                    verticalArrangement = Arrangement.spacedBy(10.dp)
                ) {
                    UploadSpeedPanel(
                        speed = UploadSpeedSnapshot(
                            running = true,
                            currentBytesPerSecond = 21_400_000.0,
                            averageBytesPerSecond = 18_000_000.0,
                            runBytes = 540_000_000L,
                            runItems = 87L,
                            runElapsedMillis = 30_000L,
                            runNumber = 3L,
                        ),
                        remainingBytes = 1_800_000_000L,
                    )
                    SyncRunCard(
                        run(4, SyncRunOutcome.RUNNING, foreground = false, bytes = 120_000_000L, items = 20, uploadMillis = 6_000L),
                        activeRunIds = setOf(4L),
                    )
                    SyncRunCard(
                        run(
                            3, SyncRunOutcome.STOPPED, foreground = false, bytes = 3_100_000_000L, items = 410,
                            uploadMillis = 600_000L, stopReason = WorkInfo.STOP_REASON_TIMEOUT,
                        ),
                        activeRunIds = setOf(4L),
                    )
                    SyncRunCard(
                        run(2, SyncRunOutcome.RUNNING, foreground = false, bytes = 80_000_000L, items = 9, uploadMillis = 4_000L),
                        activeRunIds = setOf(4L),
                    )
                    SyncRunCard(
                        run(1, SyncRunOutcome.COMPLETED, foreground = true, bytes = 900_000_000L, items = 150, uploadMillis = 45_000L),
                        activeRunIds = setOf(4L),
                    )
                }
            }
        }

        // Rates are decimal MB/s with the Mbps a speed test would report.
        compose.onNodeWithText("21.4 MB/s · 171 Mbps", substring = true, useUnmergedTree = true)
            .assertExistsLocalized("21,4 MB/s · 171 Mbps")
        compose.onNodeWithText("1.80 GB", substring = true).assertExistsLocalized("1,80 GB")
        // 1.8 GB at the 18 MB/s average is 100 s.
        compose.onNodeWithText("1 min 40 s", substring = true).assertExists()
        // History averages: 3.1 GB in 10 min is 5.2 MB/s.
        compose.onNodeWithText("5.2 MB/s", substring = true).assertExistsLocalized("5,2 MB/s")
        // The live rate, the run average and one average per history card.
        assertEquals(6, compose.onAllNodesWithText("Mbps", substring = true).fetchSemanticsNodes().size)

        saveScreenshotIfRequested("sync-speed-views.png")
    }

    @Test
    fun background_access_card_offers_only_what_is_missing() {
        var batteryClicks = 0
        var notificationClicks = 0
        var access by mutableStateOf(BackgroundSyncAccess(batteryUnrestricted = false, notificationsAllowed = false))
        compose.setContent {
            IrisTheme {
                BackgroundSyncAccessCard(
                    access = access,
                    onAllowBattery = { batteryClicks++ },
                    onAllowNotifications = { notificationClicks++ },
                )
            }
        }
        val buttons = compose.onAllNodes(hasClickAction())
        assertEquals(2, buttons.fetchSemanticsNodes().size)
        buttons[0].performClick()
        buttons[1].performClick()
        assertEquals(1, batteryClicks)
        assertEquals(1, notificationClicks)

        access = BackgroundSyncAccess(batteryUnrestricted = true, notificationsAllowed = false)
        compose.waitForIdle()
        assertEquals(1, compose.onAllNodes(hasClickAction()).fetchSemanticsNodes().size)
    }

    /** Same assertion in whichever decimal separator the device locale uses. */
    private fun androidx.compose.ui.test.SemanticsNodeInteraction.assertExistsLocalized(commaVariant: String) {
        try {
            assertExists()
        } catch (_: AssertionError) {
            compose.onNodeWithText(commaVariant, substring = true, useUnmergedTree = true).assertExists()
        }
    }

    private fun saveScreenshotIfRequested(name: String) {
        val dir = InstrumentationRegistry.getArguments().getString("screenshotDir") ?: return
        val bitmap = compose.onRoot().captureToImage().asAndroidBitmap()
        val target = File(InstrumentationRegistry.getInstrumentation().targetContext.getExternalFilesDir(null), dir)
        target.mkdirs()
        File(target, name).outputStream().use { bitmap.compress(Bitmap.CompressFormat.PNG, 100, it) }
    }
}
