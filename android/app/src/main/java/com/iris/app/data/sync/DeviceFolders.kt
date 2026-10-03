package com.iris.app.data.sync

import android.content.ContentResolver
import android.net.Uri
import android.os.Build
import android.provider.MediaStore
import com.iris.app.data.model.MediaScanPolicy
import com.iris.app.data.repository.ServerSettingsRepository.AccountSyncSettings

/** The device folder a media item lives in, as backup settings name it. */
data class DeviceFolder(
    /** Same id the scanner and the folder picker use: `volume:bucket:kind`. */
    val sourceId: String,
    val name: String,
    val relativePath: String,
    val mediaKind: String,
)

object DeviceFolders {

    /** `volume:bucket:kind`, shared by the scanner, the folder picker and the gallery. */
    fun sourceId(volume: String, bucketId: String, mediaKind: String): String = "$volume:$bucketId:$mediaKind"

    /** The policy the scanner applies for these settings. */
    fun policyOf(settings: AccountSyncSettings): MediaScanPolicy = MediaScanPolicy(
        mode = settings.sourceMode,
        selectedSourceIds = settings.selectedSourceIds,
        includeImages = settings.imagesEnabled,
        includeVideos = settings.videosEnabled,
    )

    /** The folder of one MediaStore item, or null when MediaStore does not know it. */
    fun of(contentResolver: ContentResolver, uri: Uri): DeviceFolder? = runCatching {
        val columns = buildList {
            add(MediaStore.MediaColumns.BUCKET_ID)
            add(MediaStore.MediaColumns.BUCKET_DISPLAY_NAME)
            add(MediaStore.MediaColumns.MIME_TYPE)
            if (Build.VERSION.SDK_INT >= 29) {
                add(MediaStore.MediaColumns.VOLUME_NAME)
                add(MediaStore.MediaColumns.RELATIVE_PATH)
            }
        }.toTypedArray()
        contentResolver.query(uri, columns, null, null, null)?.use { cursor ->
            if (!cursor.moveToFirst()) return@use null
            fun text(column: String, fallback: String): String {
                val index = cursor.getColumnIndex(column)
                return if (index >= 0 && !cursor.isNull(index)) cursor.getString(index).orEmpty().ifBlank { fallback } else fallback
            }
            val kind = if (text(MediaStore.MediaColumns.MIME_TYPE, "").startsWith("video/")) "video" else "image"
            DeviceFolder(
                sourceId = sourceId(
                    text(MediaStore.MediaColumns.VOLUME_NAME, "external"),
                    text(MediaStore.MediaColumns.BUCKET_ID, "unknown"),
                    kind,
                ),
                name = text(MediaStore.MediaColumns.BUCKET_DISPLAY_NAME, ""),
                relativePath = text(MediaStore.MediaColumns.RELATIVE_PATH, ""),
                mediaKind = kind,
            )
        }
    }.getOrNull()
}
