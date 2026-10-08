package com.iris.app.ui.screens.sync

import com.iris.app.data.model.UploadQueueSummary
import com.iris.app.data.sync.MediaStoreScanner

/**
 * The sync screen's one progress bar: how much of the backup is saved.
 *
 * It used to show the scan while one ran and the current file otherwise, so
 * the bar filled when the scan ended and then sat there while thousands of
 * photos were still being sent. Saved over total is the question the screen
 * answers; the total grows while a scan finds more, so the bar may step back,
 * which is true. The scan's own progress is only shown before anything is
 * queued, when there is no backup to measure yet.
 */
internal object BackupProgress {
    /** The bar's fill, or null when there is nothing to show. */
    fun of(
        summary: UploadQueueSummary,
        scan: MediaStoreScanner.ScanProgress?,
        running: Boolean,
    ): Float? = when {
        summary.total > 0 && (running || summary.remaining > 0) -> summary.saved.toFloat() / summary.total
        scan != null && scan.total > 0 -> scan.examined.toFloat() / scan.total
        else -> null
    }
}
