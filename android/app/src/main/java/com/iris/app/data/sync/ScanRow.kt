package com.iris.app.data.sync

import java.io.IOException

/** How the scan treats one gallery row. */
internal object ScanRow {
    /**
     * Examines one row; a file that cannot be read is deferred, not fatal.
     *
     * MediaStore can list a file that is already gone or not readable yet
     * (deleted meanwhile, still being written, on removed storage). Its read
     * failed the whole scan: nothing after that row was queued, and the same
     * row failed it again on every run. Deferred, it keeps the verification
     * open so the next scan looks at it again.
     */
    suspend fun readOrDefer(examine: suspend () -> SyncUploadManager.ScanOutcome): SyncUploadManager.ScanOutcome =
        try {
            examine()
        } catch (unreadable: IOException) {
            SyncUploadManager.ScanOutcome.DEFERRED
        } catch (refused: SecurityException) {
            // Android stopped letting the app read this one item (partial
            // media access): the same as unreadable for the scan.
            SyncUploadManager.ScanOutcome.DEFERRED
        }
}
