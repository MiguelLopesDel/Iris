package com.iris.app.data.sync

import android.content.Context
import android.os.Build
import android.provider.MediaStore
import android.util.Log

/**
 * MediaStore's opaque version per external volume. It changes when the
 * platform rebuilt its media database (cleared "Media Storage" data, some
 * system updates, a restore), after which ids and generations restart and a
 * stored id may name another item.
 *
 * Android 11+ reports it per volume; Android 10 only for all volumes
 * together; older versions not at all, where the size and modification time
 * in each item's fingerprint are the only defence.
 */
object MediaStoreVersions {
    private const val TAG = "MediaStoreVersions"

    fun read(context: Context): Map<String, String> = try {
        when {
            Build.VERSION.SDK_INT >= Build.VERSION_CODES.R ->
                MediaStore.getExternalVolumeNames(context).mapNotNull { volume ->
                    runCatching { volume to MediaStore.getVersion(context, volume) }.getOrNull()
                }.toMap()
            Build.VERSION.SDK_INT == Build.VERSION_CODES.Q ->
                mapOf(MediaStore.VOLUME_EXTERNAL to MediaStore.getVersion(context))
            else -> emptyMap()
        }
    } catch (failure: Exception) {
        Log.w(TAG, "MediaStore version unavailable error=${failure.javaClass.simpleName}")
        emptyMap()
    }
}
