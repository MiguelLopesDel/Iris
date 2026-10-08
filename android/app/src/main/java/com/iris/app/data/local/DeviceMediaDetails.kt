package com.iris.app.data.local

import android.content.ContentResolver
import android.net.Uri
import android.os.Build
import android.provider.MediaStore
import com.iris.app.data.model.LocalUploadJob
import com.iris.app.data.model.MediaStoreKey
import com.iris.app.data.model.UploadJobState

/**
 * What the phone's media store knows about one item: what the information
 * panel shows for a photo that exists on the device, whether or not it has
 * reached the server.
 */
data class DeviceMediaDetails(
    val name: String,
    val sizeBytes: Long?,
    val width: Int?,
    val height: Int?,
    /** Epoch milliseconds the photo was taken, or added when the store has no capture time. */
    val takenAtMillis: Long?,
    /** The folder as people see it, e.g. "DCIM/Screenshots". */
    val folder: String?,
    val mimeType: String?,
    val durationMillis: Long?,
    /** The package that created the file (Android 10 and later), e.g. a camera or screenshot app. */
    val ownerPackage: String?,
) {
    val isVideo: Boolean get() = mimeType?.startsWith("video/") == true

    companion object {
        /** Reads [uri] from the media store; null when the item is gone or unreadable. */
        fun read(resolver: ContentResolver, uri: Uri): DeviceMediaDetails? = try {
            val columns = buildList {
                add(MediaStore.MediaColumns.DISPLAY_NAME)
                add(MediaStore.MediaColumns.SIZE)
                add(MediaStore.MediaColumns.WIDTH)
                add(MediaStore.MediaColumns.HEIGHT)
                add(MediaStore.MediaColumns.DATE_ADDED)
                add(MediaStore.MediaColumns.MIME_TYPE)
                add(MediaStore.MediaColumns.DATE_TAKEN)
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                    add(MediaStore.MediaColumns.RELATIVE_PATH)
                    add(MediaStore.MediaColumns.DURATION)
                    add(MediaStore.MediaColumns.OWNER_PACKAGE_NAME)
                } else {
                    @Suppress("DEPRECATION") add(MediaStore.MediaColumns.DATA)
                }
            }.toTypedArray()
            resolver.query(uri, columns, null, null, null)?.use { cursor ->
                if (!cursor.moveToFirst()) return@use null
                fun string(column: String) = cursor.getColumnIndex(column).takeIf { it >= 0 && !cursor.isNull(it) }
                    ?.let(cursor::getString)?.ifBlank { null }
                fun long(column: String) = cursor.getColumnIndex(column).takeIf { it >= 0 && !cursor.isNull(it) }
                    ?.let(cursor::getLong)
                val taken = long(MediaStore.MediaColumns.DATE_TAKEN)?.takeIf { it > 0L }
                    ?: long(MediaStore.MediaColumns.DATE_ADDED)?.takeIf { it > 0L }?.times(1000L)
                @Suppress("DEPRECATION")
                val folder = string(MediaStore.MediaColumns.RELATIVE_PATH)?.trimEnd('/')
                    ?: string(MediaStore.MediaColumns.DATA)?.let(::folderOfPath)
                DeviceMediaDetails(
                    name = string(MediaStore.MediaColumns.DISPLAY_NAME) ?: uri.lastPathSegment.orEmpty(),
                    sizeBytes = long(MediaStore.MediaColumns.SIZE)?.takeIf { it > 0L },
                    width = long(MediaStore.MediaColumns.WIDTH)?.toInt()?.takeIf { it > 0 },
                    height = long(MediaStore.MediaColumns.HEIGHT)?.toInt()?.takeIf { it > 0 },
                    takenAtMillis = taken,
                    folder = folder,
                    mimeType = string(MediaStore.MediaColumns.MIME_TYPE) ?: resolver.getType(uri),
                    durationMillis = long(MediaStore.MediaColumns.DURATION)?.takeIf { it > 0L },
                    ownerPackage = string(MediaStore.MediaColumns.OWNER_PACKAGE_NAME),
                )
            }
        } catch (_: SecurityException) {
            null
        } catch (_: IllegalArgumentException) {
            null
        }

        /** "/storage/emulated/0/DCIM/Camera/IMG.jpg" -> "DCIM/Camera", for stores without RELATIVE_PATH. */
        internal fun folderOfPath(path: String): String? {
            val dir = path.substringBeforeLast('/', "").ifEmpty { return null }
            val storageRoot = Regex("^/storage/(?:emulated/\\d+|[^/]+)/")
            return dir.replace(storageRoot, "").trim('/').ifEmpty { null }
        }
    }
}

/** Where a photo that is on the device stands with the backup, as its information panel says it. */
enum class DeviceBackupState {
    /** Waiting in the queue for the next sync. */
    QUEUED,

    /** Being sent now. */
    SENDING,

    /** The server has it; processing may still be running. */
    SAVED,

    /** The last attempt failed; it stays only on the phone. */
    FAILED,

    /** Not queued yet: the next scan picks it up, or its folder is left out of the backup. */
    NOT_QUEUED;

    companion object {
        /** The state of the device item at [uri], from this account's upload history. */
        fun of(uri: String, jobs: List<LocalUploadJob>): DeviceBackupState {
            val key = MediaStoreKey.of(uri)
            val job = jobs.lastOrNull { MediaStoreKey.of(it.localUri) == key } ?: return NOT_QUEUED
            return when (job.state) {
                UploadJobState.QUEUED -> QUEUED
                UploadJobState.UPLOADING -> SENDING
                UploadJobState.PENDING_PROCESSING, UploadJobState.PROCESSING,
                UploadJobState.READY, UploadJobState.DUPLICATE -> SAVED
                UploadJobState.FAILED, UploadJobState.FAILED_PROCESSING -> FAILED
            }
        }
    }
}
