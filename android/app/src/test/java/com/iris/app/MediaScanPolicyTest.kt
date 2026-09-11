package com.iris.app

import com.iris.app.data.model.MediaScanPolicy
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class MediaScanPolicyTest {

    @Test
    fun `selected mode includes only selected sources and enabled media kinds`() {
        val policy = MediaScanPolicy(
            mode = "selected",
            selectedSourceIds = setOf("camera:image", "camera:video"),
            includeImages = true,
            includeVideos = false
        )

        assertTrue(policy.includes("camera:image", "image"))
        assertFalse(policy.includes("downloads:image", "image"))
        assertFalse(policy.includes("camera:video", "video"))
    }

    @Test
    fun `all mode still respects media kind switches`() {
        val policy = MediaScanPolicy(mode = "all", includeImages = false, includeVideos = true)

        assertFalse(policy.includes("any:image", "image"))
        assertTrue(policy.includes("any:video", "video"))
    }
}
