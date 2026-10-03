package com.iris.app.data.sync

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.provider.MediaStore

/**
 * Reading photos with their location metadata intact.
 *
 * Since Android 10 a photo read through MediaStore has its GPS coordinates
 * zeroed unless the app holds ACCESS_MEDIA_LOCATION and asks for the original
 * file. A backup made without it loses where every photo was taken.
 */
object MediaLocationAccess {

    fun granted(context: Context): Boolean =
        Build.VERSION.SDK_INT < Build.VERSION_CODES.Q ||
            context.checkSelfPermission(Manifest.permission.ACCESS_MEDIA_LOCATION) == PackageManager.PERMISSION_GRANTED

    /**
     * The URI that opens the unredacted file, when the platform redacts and
     * the permission allows the original; [uri] itself otherwise.
     */
    fun originalOf(uri: Uri, granted: Boolean): Uri =
        if (granted && Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q && uri.authority == MediaStore.AUTHORITY) {
            MediaStore.setRequireOriginal(uri)
        } else {
            uri
        }
}
