package com.iris.app.data.local

import android.content.Context
import android.os.Build
import android.provider.Settings

/**
 * How this phone is named on the server's list of devices: the name its owner
 * gave it in Android (Settings → About phone), or its maker and model.
 */
object DeviceName {

    fun of(context: Context): String =
        Settings.Global.getString(context.contentResolver, Settings.Global.DEVICE_NAME)
            ?.trim()
            ?.takeIf { it.isNotEmpty() }
            ?: model(Build.MANUFACTURER, Build.MODEL)

    /** "Samsung SM-S911B" from the maker and model; "motorola edge 40" does not repeat the maker. */
    fun model(manufacturer: String?, model: String?): String {
        val maker = manufacturer?.trim().orEmpty().replaceFirstChar { it.titlecase() }
        val name = model?.trim().orEmpty()
        return when {
            name.isEmpty() && maker.isEmpty() -> "Android"
            name.isEmpty() -> maker
            maker.isEmpty() || name.startsWith(maker, ignoreCase = true) -> name
            else -> "$maker $name"
        }
    }
}
