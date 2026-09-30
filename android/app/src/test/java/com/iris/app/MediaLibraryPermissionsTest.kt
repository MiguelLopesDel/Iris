package com.iris.app

import android.Manifest
import com.iris.app.ui.screens.sync.MediaLibraryAccess
import com.iris.app.ui.screens.sync.classifyMediaLibraryAccess
import com.iris.app.ui.screens.sync.mediaPermissionsForSdk
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Test

class MediaLibraryPermissionsTest {
    @Test
    fun android14_requests_full_and_selected_media_permissions_together() {
        assertArrayEquals(
            arrayOf(
                Manifest.permission.READ_MEDIA_IMAGES,
                Manifest.permission.READ_MEDIA_VIDEO,
                Manifest.permission.READ_MEDIA_VISUAL_USER_SELECTED
            ),
            mediaPermissionsForSdk(34)
        )
    }

    @Test
    fun android13_requests_image_and_video_permissions() {
        assertArrayEquals(
            arrayOf(Manifest.permission.READ_MEDIA_IMAGES, Manifest.permission.READ_MEDIA_VIDEO),
            mediaPermissionsForSdk(33)
        )
    }

    @Test
    fun android14_distinguishes_full_partial_and_denied_access() {
        assertEquals(
            MediaLibraryAccess.FULL,
            classifyMediaLibraryAccess(34, imageGranted = true, videoGranted = true, selectedMediaGranted = false)
        )
        assertEquals(
            MediaLibraryAccess.LIMITED,
            classifyMediaLibraryAccess(34, imageGranted = false, videoGranted = false, selectedMediaGranted = true)
        )
        assertEquals(
            MediaLibraryAccess.DENIED,
            classifyMediaLibraryAccess(34, imageGranted = false, videoGranted = false, selectedMediaGranted = false)
        )
    }

    @Test
    fun android13_with_only_one_media_type_is_not_reported_as_full() {
        assertEquals(
            MediaLibraryAccess.LIMITED,
            classifyMediaLibraryAccess(33, imageGranted = true, videoGranted = false, selectedMediaGranted = false)
        )
    }
}
