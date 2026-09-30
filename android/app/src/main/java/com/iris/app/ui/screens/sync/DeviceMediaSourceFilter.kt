package com.iris.app.ui.screens.sync

import com.iris.app.data.model.DeviceMediaSource
import java.util.Locale

internal enum class DeviceMediaSourceFilter {
    ALL,
    PHOTOS,
    VIDEOS,
    SELECTED
}

internal fun filterDeviceMediaSources(
    sources: List<DeviceMediaSource>,
    query: String,
    filter: DeviceMediaSourceFilter,
    selectedIds: Set<String>
): List<DeviceMediaSource> {
    val normalizedQuery = query.trim().lowercase(Locale.ROOT)
    return sources.filter { source ->
        val matchesQuery = normalizedQuery.isEmpty() ||
            source.name.lowercase(Locale.ROOT).contains(normalizedQuery) ||
            source.relativePath.lowercase(Locale.ROOT).contains(normalizedQuery)
        val matchesFilter = when (filter) {
            DeviceMediaSourceFilter.ALL -> true
            DeviceMediaSourceFilter.PHOTOS -> source.mediaKind == "image"
            DeviceMediaSourceFilter.VIDEOS -> source.mediaKind == "video"
            DeviceMediaSourceFilter.SELECTED -> source.id in selectedIds
        }
        matchesQuery && matchesFilter
    }
}
