package com.iris.app.data.sync

import com.iris.app.data.model.ChangesResponse

/** Where the stored change-feed cursor moves after one page of the feed. */
internal object ChangeFeedCursor {
    /**
     * The cursor to store when the server sends this device back, or null for
     * an ordinary page. A server whose feed lost its last entries (a power
     * loss before they reached the disk; recovery records them again) or was
     * restored answers with no changes and a next_cursor behind the device's.
     * Keeping the device's cursor would skip the entries recorded again.
     */
    fun rewindTo(current: Long, response: ChangesResponse): Long? =
        if (response.changes.isEmpty() && (response.reset || response.nextCursor < current)) {
            response.nextCursor
        } else {
            null
        }
}
