package com.iris.app.data.sync

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ChunkProgressTest {

    @Test
    fun `an offset that never moves stops the upload after a few answers`() {
        val progress = ChunkProgress(maxStalls = 3)

        repeat(3) { assertTrue(progress.accept(100, 100)) }
        assertFalse(progress.accept(100, 100))
    }

    @Test
    fun `moving on resets the count`() {
        val progress = ChunkProgress(maxStalls = 1)

        assertTrue(progress.accept(0, 0))
        assertTrue(progress.accept(0, 10))
        assertTrue(progress.accept(10, 10))
    }

    @Test
    fun `a reservation without a chunk size still sends bytes`() {
        assertEquals(ChunkProgress.FALLBACK_CHUNK_BYTES, ChunkProgress.usableChunkSize(0))
        assertEquals(1024, ChunkProgress.usableChunkSize(1024))
    }
}
