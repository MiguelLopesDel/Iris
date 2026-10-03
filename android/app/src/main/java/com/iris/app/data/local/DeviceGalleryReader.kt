package com.iris.app.data.local

import android.Manifest
import android.content.ContentResolver
import android.content.Context
import android.database.Cursor
import android.os.Build
import android.os.Bundle
import android.provider.MediaStore
import androidx.core.content.ContextCompat
import android.content.pm.PackageManager
import android.content.ContentUris
import com.iris.app.data.model.MediaRecord
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/** Reads the phone's gallery without coupling it to an Iris account or upload. */
class DeviceGalleryReader(
    private val context: Context,
    private val contentResolver: ContentResolver = context.contentResolver
) {
    fun hasMediaPermission(): Boolean {
        val permissions = when {
            Build.VERSION.SDK_INT >= 34 -> listOf(
                Manifest.permission.READ_MEDIA_IMAGES,
                Manifest.permission.READ_MEDIA_VIDEO,
                Manifest.permission.READ_MEDIA_VISUAL_USER_SELECTED
            )
            Build.VERSION.SDK_INT >= 33 -> listOf(
                Manifest.permission.READ_MEDIA_IMAGES,
                Manifest.permission.READ_MEDIA_VIDEO
            )
            else -> listOf(Manifest.permission.READ_EXTERNAL_STORAGE)
        }
        return permissions.any {
            ContextCompat.checkSelfPermission(context, it) == PackageManager.PERMISSION_GRANTED
        }
    }

    suspend fun page(page: Int, pageSize: Int, mediaType: String): DeviceGalleryPage =
        withContext(Dispatchers.IO) {
            if (!hasMediaPermission()) return@withContext DeviceGalleryPage(permissionGranted = false)

            val selection = buildString {
                append("${MediaStore.MediaColumns.SIZE} > 0 AND ${MediaStore.Files.FileColumns.MEDIA_TYPE} IN (?, ?)")
                when (mediaType) {
                    "image" -> append(" AND ${MediaStore.Files.FileColumns.MEDIA_TYPE} = ${MediaStore.Files.FileColumns.MEDIA_TYPE_IMAGE}")
                    "video" -> append(" AND ${MediaStore.Files.FileColumns.MEDIA_TYPE} = ${MediaStore.Files.FileColumns.MEDIA_TYPE_VIDEO}")
                }
            }
            val selectionArgs = arrayOf(
                MediaStore.Files.FileColumns.MEDIA_TYPE_IMAGE.toString(),
                MediaStore.Files.FileColumns.MEDIA_TYPE_VIDEO.toString()
            )
            val collection = MediaStore.Files.getContentUri("external")
            val projection = arrayOf(
                MediaStore.MediaColumns._ID,
                MediaStore.MediaColumns.DISPLAY_NAME,
                MediaStore.MediaColumns.MIME_TYPE,
                MediaStore.MediaColumns.SIZE,
                MediaStore.MediaColumns.DATE_TAKEN,
                MediaStore.MediaColumns.DATE_ADDED,
                MediaStore.MediaColumns.DATE_MODIFIED,
                MediaStore.MediaColumns.BUCKET_ID,
                MediaStore.MediaColumns.BUCKET_DISPLAY_NAME,
                MediaStore.Files.FileColumns.MEDIA_TYPE
            ) + (if (android.os.Build.VERSION.SDK_INT >= 29) arrayOf(MediaStore.MediaColumns.VOLUME_NAME) else emptyArray()) +
                (if (android.os.Build.VERSION.SDK_INT >= 30) arrayOf(MediaStore.MediaColumns.GENERATION_MODIFIED) else emptyArray())

            try {
                val total = contentResolver.query(
                    collection,
                    arrayOf(MediaStore.MediaColumns._ID),
                    selection,
                    selectionArgs,
                    null
                )?.use(Cursor::getCount) ?: 0
                val queryArgs = Bundle().apply {
                    putString(ContentResolver.QUERY_ARG_SQL_SELECTION, selection)
                    putStringArray(ContentResolver.QUERY_ARG_SQL_SELECTION_ARGS, selectionArgs)
                    putString(ContentResolver.QUERY_ARG_SQL_SORT_ORDER, NEWEST_FIRST)
                    putInt(ContentResolver.QUERY_ARG_LIMIT, pageSize)
                    putInt(ContentResolver.QUERY_ARG_OFFSET, (page - 1).coerceAtLeast(0) * pageSize)
                }
                val records = contentResolver.query(collection, projection, queryArgs, null)
                    ?.use { cursor -> cursor.toRecords(collection) }
                    .orEmpty()
                DeviceGalleryPage(
                    records = records,
                    total = total,
                    totalPages = ((total + pageSize - 1) / pageSize).coerceAtLeast(1),
                    permissionGranted = true
                )
            } catch (_: SecurityException) {
                DeviceGalleryPage(permissionGranted = false)
            }
        }

    private fun Cursor.toRecords(collection: android.net.Uri): List<MediaRecord> {
        val idIndex = getColumnIndexOrThrow(MediaStore.MediaColumns._ID)
        val nameIndex = getColumnIndexOrThrow(MediaStore.MediaColumns.DISPLAY_NAME)
        val mimeIndex = getColumnIndex(MediaStore.MediaColumns.MIME_TYPE)
        val sizeIndex = getColumnIndex(MediaStore.MediaColumns.SIZE)
        val takenIndex = getColumnIndex(MediaStore.MediaColumns.DATE_TAKEN)
        val addedIndex = getColumnIndex(MediaStore.MediaColumns.DATE_ADDED)
        val modifiedIndex = getColumnIndex(MediaStore.MediaColumns.DATE_MODIFIED)
        val generationIndex = getColumnIndex(MediaStore.MediaColumns.GENERATION_MODIFIED)
        val bucketIdIndex = getColumnIndex(MediaStore.MediaColumns.BUCKET_ID)
        val bucketNameIndex = getColumnIndex(MediaStore.MediaColumns.BUCKET_DISPLAY_NAME)
        val volumeIndex = getColumnIndex(MediaStore.MediaColumns.VOLUME_NAME)
        fun textAt(index: Int, fallback: String): String =
            if (index >= 0 && !isNull(index)) getString(index).orEmpty().ifBlank { fallback } else fallback
        val typeIndex = getColumnIndexOrThrow(MediaStore.Files.FileColumns.MEDIA_TYPE)
        return buildList {
            while (moveToNext()) {
                val id = getLong(idIndex)
                val type = getInt(typeIndex)
                val dateTaken = if (takenIndex >= 0) getLong(takenIndex) else 0L
                val dateAdded = if (addedIndex >= 0) getLong(addedIndex) else 0L
                val timestampSeconds = if (dateTaken > 0L) dateTaken / 1000L else dateAdded
                add(
                    MediaRecord(
                        index = -id.coerceAtMost(Int.MAX_VALUE.toLong()).toInt().coerceAtLeast(1),
                        arquivo = getString(nameIndex).orEmpty().ifBlank { "media_$id" },
                        fileSize = if (sizeIndex >= 0) getLong(sizeIndex) else null,
                        fileMtime = timestampSeconds.toDouble(),
                        mediaType = if (type == MediaStore.Files.FileColumns.MEDIA_TYPE_VIDEO) "video" else "image",
                        deviceUri = ContentUris.withAppendedId(collection, id).toString(),
                        mimeType = if (mimeIndex >= 0) getString(mimeIndex) else null,
                        deviceSourceId = com.iris.app.data.sync.DeviceFolders.sourceId(
                            textAt(volumeIndex, "external"),
                            textAt(bucketIdIndex, "unknown"),
                            if (type == MediaStore.Files.FileColumns.MEDIA_TYPE_VIDEO) "video" else "image",
                        ),
                        deviceFolder = textAt(bucketNameIndex, "").ifBlank { null },
                        deviceFingerprint = if (sizeIndex >= 0) {
                            com.iris.app.data.sync.MediaFingerprint(
                                size = getLong(sizeIndex),
                                dateModifiedSeconds = if (modifiedIndex >= 0) getLong(modifiedIndex) else 0L,
                                generation = if (generationIndex >= 0) getLong(generationIndex) else 0L,
                            )
                        } else {
                            null
                        }
                    )
                )
            }
        }
    }
}

/**
 * Newest first by the date the gallery shows: when it was taken, or when it
 * was added for media without a capture date (downloads, WhatsApp). Sorting
 * Same rule as the record's fileMtime (a zero capture date counts as absent).
 * Sorting by the two columns with QUERY_ARG_SORT_DIRECTION produced
 * "datetaken, date_added DESC": only date_added descended, so media without a
 * capture date came first and photos followed oldest first, and a photo
 * just taken landed on the last page, never loaded.
 */
internal val NEWEST_FIRST =
    "CASE WHEN ${MediaStore.MediaColumns.DATE_TAKEN} > 0 THEN ${MediaStore.MediaColumns.DATE_TAKEN} " +
        "ELSE ${MediaStore.MediaColumns.DATE_ADDED} * 1000 END DESC, ${MediaStore.MediaColumns._ID} DESC"

data class DeviceGalleryPage(
    val records: List<MediaRecord> = emptyList(),
    val total: Int = 0,
    val totalPages: Int = 1,
    val permissionGranted: Boolean = true
)
