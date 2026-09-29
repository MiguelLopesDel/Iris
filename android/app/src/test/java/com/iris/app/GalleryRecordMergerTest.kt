package com.iris.app

import com.iris.app.data.model.MediaRecord
import com.iris.app.ui.screens.gallery.GalleryRecordMerger
import org.junit.Assert.assertEquals
import org.junit.Test

class GalleryRecordMergerTest {
    private val merger = GalleryRecordMerger()

    @Test
    fun `matching content hash hides local copy regardless of hash case`() {
        val remote = record(index = 1, hash = "ab12", mtime = 10.0)
        val localDuplicate = record(index = 2, hash = "AB12", mtime = 20.0, deviceUri = "content://local/2")

        assertEquals(listOf(remote), merger.merge(listOf(remote), listOf(localDuplicate)))
    }

    @Test
    fun `local copy remains visible until matching remote page is present`() {
        val local = record(index = 2, hash = "ab12", mtime = 20.0, deviceUri = "content://local/2")

        assertEquals(listOf(local), merger.merge(emptyList(), listOf(local)))
    }

    @Test
    fun `records without hashes remain visible and all sources sort newest first`() {
        val olderRemote = record(index = 1, hash = "remote", mtime = 10.0)
        val newestLocal = record(index = 2, hash = null, mtime = 30.0, deviceUri = "content://local/2")
        val noTimestamp = record(index = 3, hash = "", mtime = null, deviceUri = "content://local/3")

        assertEquals(
            listOf(newestLocal, olderRemote, noTimestamp),
            merger.merge(listOf(olderRemote), listOf(noTimestamp, newestLocal))
        )
    }

    private fun record(
        index: Int,
        hash: String?,
        mtime: Double?,
        deviceUri: String? = null,
    ) = MediaRecord(
        index = index,
        contentHash = hash,
        fileMtime = mtime,
        deviceUri = deviceUri,
    )
}
