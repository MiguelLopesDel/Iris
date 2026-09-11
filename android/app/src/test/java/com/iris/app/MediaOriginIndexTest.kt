package com.iris.app

import com.iris.app.data.model.LocalUploadJob
import com.iris.app.data.model.MediaOrigin
import com.iris.app.data.model.MediaOriginIndex
import com.iris.app.data.model.UploadJobState
import org.junit.Assert.assertEquals
import org.junit.Test

class MediaOriginIndexTest {

    private fun job(sha256: String, state: UploadJobState, id: Long = 1L) = LocalUploadJob(
        id = id,
        localUri = "content://media/external/images/media/$id",
        filename = "IMG_$id.jpg",
        byteSize = 1_024L,
        sha256 = sha256,
        capturedAt = "2026-09-10T12:00:00Z",
        state = state
    )

    @Test
    fun `an item this device never uploaded is reported as server only`() {
        val index = MediaOriginIndex.from(listOf(job("aaa", UploadJobState.READY)))

        assertEquals(MediaOrigin.IRIS_ONLY, index.originOf("bbb"))
    }

    @Test
    fun `a record without a content hash cannot be claimed as local`() {
        val index = MediaOriginIndex.from(listOf(job("aaa", UploadJobState.READY)))

        assertEquals(MediaOrigin.IRIS_ONLY, index.originOf(null))
        assertEquals(MediaOrigin.IRIS_ONLY, index.originOf(""))
    }

    @Test
    fun `hash comparison ignores case so the server spelling does not matter`() {
        val index = MediaOriginIndex.from(listOf(job("ABCDEF", UploadJobState.READY)))

        assertEquals(MediaOrigin.ON_DEVICE, index.originOf("abcdef"))
    }

    @Test
    fun `queued and uploading rows both read as uploading`() {
        val index = MediaOriginIndex.from(
            listOf(job("aaa", UploadJobState.QUEUED), job("bbb", UploadJobState.UPLOADING, id = 2L))
        )

        assertEquals(MediaOrigin.UPLOADING, index.originOf("aaa"))
        assertEquals(MediaOrigin.UPLOADING, index.originOf("bbb"))
    }

    @Test
    fun `an accepted upload still being indexed reads as processing`() {
        val index = MediaOriginIndex.from(listOf(job("aaa", UploadJobState.PENDING_PROCESSING)))

        assertEquals(MediaOrigin.PROCESSING, index.originOf("aaa"))
    }

    @Test
    fun `a duplicate counts as present on the device`() {
        // The server rejected the bytes because it already had them, which still
        // means this phone holds a copy.
        val index = MediaOriginIndex.from(listOf(job("aaa", UploadJobState.DUPLICATE)))

        assertEquals(MediaOrigin.ON_DEVICE, index.originOf("aaa"))
    }

    @Test
    fun `a failed retry does not overwrite an upload that already succeeded`() {
        // Rescans enqueue the same file again, so one hash can own several rows.
        // Reporting the later failure would tell the user their photo is not in
        // Iris when it demonstrably is.
        val index = MediaOriginIndex.from(
            listOf(
                job("aaa", UploadJobState.READY, id = 1L),
                job("aaa", UploadJobState.FAILED, id = 2L)
            )
        )

        assertEquals(MediaOrigin.ON_DEVICE, index.originOf("aaa"))
    }

    @Test
    fun `a failure stands when nothing better ever happened to that file`() {
        val index = MediaOriginIndex.from(
            listOf(
                job("aaa", UploadJobState.FAILED, id = 1L),
                job("aaa", UploadJobState.FAILED_PROCESSING, id = 2L)
            )
        )

        assertEquals(MediaOrigin.FAILED, index.originOf("aaa"))
    }

    @Test
    fun `an empty queue leaves every item server only`() {
        assertEquals(MediaOrigin.IRIS_ONLY, MediaOriginIndex.EMPTY.originOf("aaa"))
        assertEquals(MediaOrigin.IRIS_ONLY, MediaOriginIndex.from(emptyList()).originOf("aaa"))
    }
}
