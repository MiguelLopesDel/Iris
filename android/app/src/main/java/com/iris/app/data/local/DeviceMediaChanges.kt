package com.iris.app.data.local

import android.content.ContentResolver
import android.database.ContentObserver
import android.os.Handler
import android.os.Looper
import android.provider.MediaStore
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.callbackFlow
import kotlinx.coroutines.flow.conflate

/**
 * Emits whenever MediaStore reports a change: a photo taken, saved, copied,
 * edited or deleted by any app. The device gallery listens while it is open,
 * so new media shows up as it does in the system gallery, without a manual
 * refresh. One action produces bursts (a camera inserts a pending row, then
 * publishes it), so collectors should debounce.
 */
object DeviceMediaChanges {

    fun of(contentResolver: ContentResolver): Flow<Unit> = callbackFlow {
        val observer = object : ContentObserver(Handler(Looper.getMainLooper())) {
            override fun onChange(selfChange: Boolean) {
                trySend(Unit)
            }
        }
        contentResolver.registerContentObserver(MediaStore.AUTHORITY_URI, true, observer)
        awaitClose { contentResolver.unregisterContentObserver(observer) }
    }.conflate()
}
