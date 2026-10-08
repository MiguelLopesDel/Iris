package com.iris.app.data.sync

import com.iris.app.data.model.ChangesResponse
import com.iris.app.data.model.SyncChange
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class ChangeFeedCursorTest {
    @Test
    fun aServerResetMovesTheCursorBackToTheFeedsEnd() {
        val page = ChangesResponse(changes = emptyList(), nextCursor = 1040, hasMore = false, reset = true)

        assertEquals(1040L, ChangeFeedCursor.rewindTo(current = 1050, response = page))
    }

    @Test
    fun anEmptyPageBehindTheDeviceMovesItBackEvenWithoutTheFlag() {
        val page = ChangesResponse(changes = emptyList(), nextCursor = 1040, hasMore = false)

        assertEquals(1040L, ChangeFeedCursor.rewindTo(current = 1050, response = page))
    }

    @Test
    fun anOrdinaryEmptyPageKeepsTheCursor() {
        val page = ChangesResponse(changes = emptyList(), nextCursor = 1050, hasMore = false)

        assertNull(ChangeFeedCursor.rewindTo(current = 1050, response = page))
    }

    @Test
    fun aPageWithChangesIsAppliedNotRewound() {
        val page = ChangesResponse(changes = listOf(SyncChange(cursor = 1041)), nextCursor = 1041)

        assertNull(ChangeFeedCursor.rewindTo(current = 1050, response = page))
    }
}
