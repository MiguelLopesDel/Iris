package com.iris.app.data.sync

import android.content.ContentResolver
import android.content.ContentUris
import android.net.Uri
import android.os.Build
import android.provider.MediaStore
import com.iris.app.data.model.DeviceMediaSource
import com.iris.app.data.model.MediaScanPolicy
import com.iris.app.data.model.UploadSource
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone

class MediaStoreScanner(
    private val contentResolver: ContentResolver,
    private val uploadManager: SyncUploadManager
) {

    private val isoFormat = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss'Z'", Locale.US).apply {
        timeZone = TimeZone.getTimeZone("UTC")
    }

    suspend fun discoverSources(): List<DeviceMediaSource> = withContext(Dispatchers.IO) {
        val sources = linkedMapOf<String, DeviceMediaSource>()
        discoverCollection(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, "image", sources)
        discoverCollection(MediaStore.Video.Media.EXTERNAL_CONTENT_URI, "video", sources)
        sources.values.sortedWith(compareBy({ it.name.lowercase(Locale.getDefault()) }, { it.mediaKind }))
    }

    suspend fun scanAndEnqueueNewMedia(policy: MediaScanPolicy = MediaScanPolicy()): Int = withContext(Dispatchers.IO) {
        var count = 0
        if (policy.includeImages) {
            count += scanCollection(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, "image", policy)
        }
        if (policy.includeVideos) {
            count += scanCollection(MediaStore.Video.Media.EXTERNAL_CONTENT_URI, "video", policy)
        }
        count
    }

    private fun projection(): Array<String> = buildList {
        add(MediaStore.MediaColumns._ID)
        add(MediaStore.MediaColumns.DISPLAY_NAME)
        add(MediaStore.MediaColumns.SIZE)
        add(MediaStore.MediaColumns.DATE_ADDED)
        add(MediaStore.MediaColumns.DATE_TAKEN)
        add(MediaStore.MediaColumns.BUCKET_ID)
        add(MediaStore.MediaColumns.BUCKET_DISPLAY_NAME)
        if (Build.VERSION.SDK_INT >= 29) {
            add(MediaStore.MediaColumns.RELATIVE_PATH)
            add(MediaStore.MediaColumns.VOLUME_NAME)
        }
        if (Build.VERSION.SDK_INT >= 30) add(MediaStore.MediaColumns.GENERATION_MODIFIED)
    }.toTypedArray()

    private fun discoverCollection(
        collectionUri: Uri,
        mediaKind: String,
        target: MutableMap<String, DeviceMediaSource>
    ) {
        contentResolver.query(collectionUri, projection(), "${MediaStore.MediaColumns.SIZE} > 0", null, null)?.use { cursor ->
            val bucketIdColumn = cursor.getColumnIndex(MediaStore.MediaColumns.BUCKET_ID)
            val bucketNameColumn = cursor.getColumnIndex(MediaStore.MediaColumns.BUCKET_DISPLAY_NAME)
            val relativePathColumn = cursor.getColumnIndex(MediaStore.MediaColumns.RELATIVE_PATH)
            val volumeColumn = cursor.getColumnIndex(MediaStore.MediaColumns.VOLUME_NAME)
            while (cursor.moveToNext()) {
                val volume = cursor.stringOrEmpty(volumeColumn, "external")
                val bucketId = cursor.stringOrEmpty(bucketIdColumn, "unknown")
                val sourceId = sourceId(volume, bucketId, mediaKind)
                val previous = target[sourceId]
                target[sourceId] = DeviceMediaSource(
                    id = sourceId,
                    name = cursor.stringOrEmpty(bucketNameColumn, "Unknown source"),
                    relativePath = cursor.stringOrEmpty(relativePathColumn, ""),
                    volume = volume,
                    mediaKind = mediaKind,
                    itemCount = (previous?.itemCount ?: 0) + 1
                )
            }
        }
    }

    private suspend fun scanCollection(collectionUri: Uri, mediaKind: String, policy: MediaScanPolicy): Int {
        val selection = "${MediaStore.MediaColumns.SIZE} > 0"
        val sortOrder = "${MediaStore.MediaColumns.DATE_ADDED} DESC"

        var enqueued = 0
        try {
            contentResolver.query(
                collectionUri,
                projection(),
                selection,
                null,
                sortOrder
            )?.use { cursor ->
                val idColumn = cursor.getColumnIndexOrThrow(MediaStore.MediaColumns._ID)
                val nameColumn = cursor.getColumnIndexOrThrow(MediaStore.MediaColumns.DISPLAY_NAME)
                val sizeColumn = cursor.getColumnIndexOrThrow(MediaStore.MediaColumns.SIZE)
                val dateAddedColumn = cursor.getColumnIndexOrThrow(MediaStore.MediaColumns.DATE_ADDED)
                val dateTakenColumn = cursor.getColumnIndex(MediaStore.MediaColumns.DATE_TAKEN)
                val bucketIdColumn = cursor.getColumnIndex(MediaStore.MediaColumns.BUCKET_ID)
                val bucketNameColumn = cursor.getColumnIndex(MediaStore.MediaColumns.BUCKET_DISPLAY_NAME)
                val relativePathColumn = cursor.getColumnIndex(MediaStore.MediaColumns.RELATIVE_PATH)
                val volumeColumn = cursor.getColumnIndex(MediaStore.MediaColumns.VOLUME_NAME)
                val generationColumn = cursor.getColumnIndex(MediaStore.MediaColumns.GENERATION_MODIFIED)

                while (cursor.moveToNext()) {
                    val id = cursor.getLong(idColumn)
                    val name = cursor.getString(nameColumn) ?: "media_$id"
                    val size = cursor.getLong(sizeColumn)
                    val dateTakenMs = if (dateTakenColumn >= 0) cursor.getLong(dateTakenColumn) else 0L
                    val timestampMs = if (dateTakenMs > 0L) {
                        dateTakenMs
                    } else {
                        val dateAddedSeconds = cursor.getLong(dateAddedColumn)
                        dateAddedSeconds * 1000L
                    }
                    val capturedAtIso = isoFormat.format(Date(timestampMs))
                    val volume = cursor.stringOrEmpty(volumeColumn, "external")
                    val bucketId = cursor.stringOrEmpty(bucketIdColumn, "unknown")
                    val sourceId = sourceId(volume, bucketId, mediaKind)
                    if (!policy.includes(sourceId, mediaKind)) continue

                    val itemUri = ContentUris.withAppendedId(collectionUri, id)
                    val jobId = uploadManager.enqueueMedia(
                        uri = itemUri,
                        filename = name,
                        size = size,
                        capturedAtIso = capturedAtIso,
                        source = UploadSource(
                            id = sourceId,
                            name = cursor.stringOrEmpty(bucketNameColumn, "Unknown source"),
                            relativePath = cursor.stringOrEmpty(relativePathColumn, ""),
                            volume = volume,
                            mediaStoreId = id.toString(),
                            generation = if (generationColumn >= 0) cursor.getLong(generationColumn) else 0L,
                            mediaKind = mediaKind
                        )
                    )
                    if (jobId > 0) {
                        enqueued++
                    }
                }
            }
        } catch (e: Exception) {
            // Log safely without sensitive info
        }
        return enqueued
    }

    private fun sourceId(volume: String, bucketId: String, mediaKind: String): String =
        "$volume:$bucketId:$mediaKind"

    private fun android.database.Cursor.stringOrEmpty(column: Int, fallback: String): String =
        if (column >= 0 && !isNull(column)) getString(column).orEmpty().ifBlank { fallback } else fallback
}
