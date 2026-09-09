package com.iris.app.data.sync

import android.content.ContentResolver
import android.content.ContentUris
import android.net.Uri
import android.provider.MediaStore
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

    suspend fun scanAndEnqueueNewMedia(): Int = withContext(Dispatchers.IO) {
        var count = 0
        count += scanCollection(MediaStore.Images.Media.EXTERNAL_CONTENT_URI)
        count += scanCollection(MediaStore.Video.Media.EXTERNAL_CONTENT_URI)
        count
    }

    private suspend fun scanCollection(collectionUri: Uri): Int {
        val projection = arrayOf(
            MediaStore.MediaColumns._ID,
            MediaStore.MediaColumns.DISPLAY_NAME,
            MediaStore.MediaColumns.SIZE,
            MediaStore.MediaColumns.DATE_ADDED
        )
        val selection = "${MediaStore.MediaColumns.SIZE} > 0"
        val sortOrder = "${MediaStore.MediaColumns.DATE_ADDED} DESC"

        var enqueued = 0
        try {
            contentResolver.query(
                collectionUri,
                projection,
                selection,
                null,
                sortOrder
            )?.use { cursor ->
                val idColumn = cursor.getColumnIndexOrThrow(MediaStore.MediaColumns._ID)
                val nameColumn = cursor.getColumnIndexOrThrow(MediaStore.MediaColumns.DISPLAY_NAME)
                val sizeColumn = cursor.getColumnIndexOrThrow(MediaStore.MediaColumns.SIZE)
                val dateColumn = cursor.getColumnIndexOrThrow(MediaStore.MediaColumns.DATE_ADDED)

                while (cursor.moveToNext()) {
                    val id = cursor.getLong(idColumn)
                    val name = cursor.getString(nameColumn) ?: "media_$id"
                    val size = cursor.getLong(sizeColumn)
                    val dateAddedSeconds = cursor.getLong(dateColumn)
                    val capturedAtIso = isoFormat.format(Date(dateAddedSeconds * 1000L))

                    val itemUri = ContentUris.withAppendedId(collectionUri, id)
                    val jobId = uploadManager.enqueueMedia(
                        uri = itemUri,
                        filename = name,
                        size = size,
                        capturedAtIso = capturedAtIso
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
}
