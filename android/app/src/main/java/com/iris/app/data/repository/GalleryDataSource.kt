package com.iris.app.data.repository

import com.iris.app.data.catalog.MediaCatalog
import com.iris.app.data.local.DeviceGalleryPage
import com.iris.app.data.local.DeviceGalleryReader
import com.iris.app.data.model.MediaRecord
import com.iris.app.data.model.RecordsResponse

/** Data boundary for the unified device-and-server gallery. */
interface GalleryDataSource {
    suspend fun devicePage(page: Int, pageSize: Int, mediaType: String): DeviceGalleryPage
    suspend fun serverPage(page: Int, pageSize: Int, mediaType: String): Result<RecordsResponse>
    suspend fun cachedRecords(offset: Int, limit: Int, mediaType: String): List<MediaRecord>
    suspend fun cachedCount(mediaType: String): Int
    suspend fun activateSession(sessionKey: String)
    suspend fun remember(records: List<MediaRecord>, sessionKey: String, isSessionCurrent: () -> Boolean)
    suspend fun clear()
}

/** Production adapter joining the existing server, MediaStore, and mirror stores. */
class DefaultGalleryDataSource(
    private val serverRepository: IrisRepository,
    private val catalog: MediaCatalog,
    private val deviceGalleryReader: DeviceGalleryReader,
) : GalleryDataSource {
    override suspend fun devicePage(page: Int, pageSize: Int, mediaType: String): DeviceGalleryPage =
        deviceGalleryReader.page(page, pageSize, mediaType)

    override suspend fun serverPage(
        page: Int,
        pageSize: Int,
        mediaType: String,
    ): Result<RecordsResponse> = serverRepository.getRecords(
        page = page,
        perPage = pageSize,
        sortBy = "data",
        mediaType = mediaType,
    )

    override suspend fun cachedRecords(offset: Int, limit: Int, mediaType: String): List<MediaRecord> =
        catalog.cached(offset = offset, limit = limit, mediaType = mediaType)

    override suspend fun cachedCount(mediaType: String): Int = catalog.cachedCount(mediaType)

    override suspend fun activateSession(sessionKey: String) = catalog.activateSession(sessionKey)

    override suspend fun remember(
        records: List<MediaRecord>,
        sessionKey: String,
        isSessionCurrent: () -> Boolean,
    ) = catalog.remember(records, sessionKey, isSessionCurrent)

    override suspend fun clear() = catalog.clear()
}
