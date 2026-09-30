package com.iris.app

import com.iris.app.data.model.DeviceMediaSource
import com.iris.app.ui.screens.sync.DeviceMediaSourceFilter
import com.iris.app.ui.screens.sync.filterDeviceMediaSources
import org.junit.Assert.assertEquals
import org.junit.Test

class DeviceMediaSourceFilterTest {
    private val sources = listOf(
        DeviceMediaSource("camera", "Camera", "DCIM/Camera/", "external", "image", 50),
        DeviceMediaSource("iris-test", "IrisPerfBatch", "Pictures/IrisPerfBatch/", "external", "image", 300),
        DeviceMediaSource("movies", "Movies", "Movies/", "external", "video", 12)
    )

    @Test
    fun `search matches folder name without case sensitivity`() {
        val result = filterDeviceMediaSources(
            sources,
            query = "irisperf",
            filter = DeviceMediaSourceFilter.ALL,
            selectedIds = emptySet()
        )

        assertEquals(listOf("iris-test"), result.map { it.id })
    }

    @Test
    fun `search matches relative path`() {
        val result = filterDeviceMediaSources(
            sources,
            query = "pictures/iris",
            filter = DeviceMediaSourceFilter.ALL,
            selectedIds = emptySet()
        )

        assertEquals(listOf("iris-test"), result.map { it.id })
    }

    @Test
    fun `kind and selected filters compose`() {
        val result = filterDeviceMediaSources(
            sources,
            query = "",
            filter = DeviceMediaSourceFilter.SELECTED,
            selectedIds = setOf("camera", "movies")
        )

        assertEquals(listOf("camera", "movies"), result.map { it.id })
        assertEquals(
            listOf("movies"),
            filterDeviceMediaSources(
                result,
                query = "",
                filter = DeviceMediaSourceFilter.VIDEOS,
                selectedIds = setOf("camera", "movies")
            ).map { it.id }
        )
    }
}
