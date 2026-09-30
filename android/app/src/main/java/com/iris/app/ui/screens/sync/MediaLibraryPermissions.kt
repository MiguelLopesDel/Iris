package com.iris.app.ui.screens.sync

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build

internal enum class MediaLibraryAccess {
    FULL,
    LIMITED,
    DENIED
}

internal fun mediaPermissionsForSdk(sdk: Int): Array<String> = when {
    sdk >= 34 -> arrayOf(
        Manifest.permission.READ_MEDIA_IMAGES,
        Manifest.permission.READ_MEDIA_VIDEO,
        Manifest.permission.READ_MEDIA_VISUAL_USER_SELECTED
    )
    sdk >= 33 -> arrayOf(
        Manifest.permission.READ_MEDIA_IMAGES,
        Manifest.permission.READ_MEDIA_VIDEO
    )
    else -> arrayOf(Manifest.permission.READ_EXTERNAL_STORAGE)
}

internal fun classifyMediaLibraryAccess(
    sdk: Int,
    imageGranted: Boolean,
    videoGranted: Boolean,
    selectedMediaGranted: Boolean,
    legacyStorageGranted: Boolean = false
): MediaLibraryAccess = when {
    sdk >= 34 && imageGranted && videoGranted -> MediaLibraryAccess.FULL
    sdk >= 34 && (selectedMediaGranted || imageGranted || videoGranted) -> MediaLibraryAccess.LIMITED
    sdk == 33 && imageGranted && videoGranted -> MediaLibraryAccess.FULL
    sdk == 33 && (imageGranted || videoGranted) -> MediaLibraryAccess.LIMITED
    sdk < 33 && legacyStorageGranted -> MediaLibraryAccess.FULL
    else -> MediaLibraryAccess.DENIED
}

internal fun currentMediaLibraryAccess(context: Context): MediaLibraryAccess {
    fun granted(permission: String) =
        context.checkSelfPermission(permission) == PackageManager.PERMISSION_GRANTED

    return classifyMediaLibraryAccess(
        sdk = Build.VERSION.SDK_INT,
        imageGranted = granted(Manifest.permission.READ_MEDIA_IMAGES),
        videoGranted = granted(Manifest.permission.READ_MEDIA_VIDEO),
        selectedMediaGranted = Build.VERSION.SDK_INT >= 34 &&
            granted(Manifest.permission.READ_MEDIA_VISUAL_USER_SELECTED),
        legacyStorageGranted = granted(Manifest.permission.READ_EXTERNAL_STORAGE)
    )
}
