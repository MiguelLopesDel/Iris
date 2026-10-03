package com.iris.app.data.local

import android.content.ContentResolver
import android.database.ContentObserver
import android.os.Handler
import android.os.Looper
import android.provider.MediaStore
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.callbackFlow
import kotlinx.coroutines.flow.conflate

/**
 * Emits whenever MediaStore reports a change: a photo taken, saved, copied,
 * edited or deleted by any app. The device gallery listens while it is open,
 * so new media shows up as it does in the system gallery, without a manual
 * refresh. Consume it through [reloadAtMostEvery].
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

    /**
     * Runs [reload] after each change, at most once per [intervalMillis].
     *
     * Not a debounce: on some devices (MIUI's gallery and cloud indexer)
     * MediaStore never stays quiet, and a debounce waiting for silence never
     * reloaded at all. Here a change waits [intervalMillis] (so a camera's
     * insert-then-publish lands in one reload), changes arriving meanwhile
     * fold into the next one, and a steady stream still reloads regularly.
     */
    suspend fun reloadAtMostEvery(changes: Flow<Unit>, intervalMillis: Long, reload: suspend () -> Unit) {
        changes.conflate().collect {
            delay(intervalMillis)
            reload()
        }
    }
}
