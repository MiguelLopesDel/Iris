package com.iris.app.data.local

import com.iris.app.data.model.LocalUploadJob
import com.iris.app.data.model.UploadJobState
import com.iris.app.ui.screens.gallery.formatDuration
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class DeviceMediaDetailsTest {

    private fun job(uri: String, state: UploadJobState) = LocalUploadJob(
        localUri = uri, filename = "IMG_0001.jpg", byteSize = 1L, sha256 = "a", capturedAt = "", state = state,
    )

    @Test
    fun `an item never queued is not in Iris yet`() {
        assertEquals(DeviceBackupState.NOT_QUEUED, DeviceBackupState.of("content://media/external/images/media/7", emptyList()))
    }

    @Test
    fun `the queue state names where the item stands`() {
        val uri = "content://media/external/images/media/7"
        assertEquals(DeviceBackupState.QUEUED, DeviceBackupState.of(uri, listOf(job(uri, UploadJobState.QUEUED))))
        assertEquals(DeviceBackupState.SENDING, DeviceBackupState.of(uri, listOf(job(uri, UploadJobState.UPLOADING))))
        assertEquals(DeviceBackupState.SAVED, DeviceBackupState.of(uri, listOf(job(uri, UploadJobState.PROCESSING))))
        assertEquals(DeviceBackupState.FAILED, DeviceBackupState.of(uri, listOf(job(uri, UploadJobState.FAILED))))
        assertEquals(
            DeviceBackupState.FAILED_PROCESSING,
            DeviceBackupState.of(uri, listOf(job(uri, UploadJobState.FAILED_PROCESSING)))
        )
    }

    @Test
    fun `the same item matches across media store volume names`() {
        val queued = job("content://media/external_primary/images/media/7", UploadJobState.QUEUED)

        assertEquals(DeviceBackupState.QUEUED, DeviceBackupState.of("content://media/external/images/media/7", listOf(queued)))
    }

    @Test
    fun `an old file path gives the folder people see`() {
        assertEquals("DCIM/Screenshots", DeviceMediaDetails.folderOfPath("/storage/emulated/0/DCIM/Screenshots/a.png"))
        assertEquals("Pictures", DeviceMediaDetails.folderOfPath("/storage/1A2B-3C4D/Pictures/b.jpg"))
        assertNull(DeviceMediaDetails.folderOfPath("c.jpg"))
    }

    @Test
    fun `a video length reads like a player's`() {
        assertEquals("1:05", formatDuration(65_000))
        assertEquals("1:02:03", formatDuration(3_723_000))
    }
}
