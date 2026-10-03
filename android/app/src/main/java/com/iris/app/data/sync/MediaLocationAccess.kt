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

/** The original file could not be read for an upload pinned to it; the item is re-hashed and retried. */
class OriginalRefusedException(cause: Throwable) : java.io.IOException("Original media refused", cause)

/**
 * Which version of an item to read: the original (with location) or the
 * redacted file Android serves without ACCESS_MEDIA_LOCATION.
 *
 * Every upload is pinned to the version its hash was computed from: a job
 * records it, and its chunks read that version strictly ([openExact]). One
 * upload can therefore never declare one version's hash and send the other's
 * bytes. Mixing them made the server refuse the upload, a re-read matched the
 * declared hash, and the item was failed for good.
 *
 * Hashing reads the preferred version ([openPreferred]): the original, unless
 * this item refused it recently. A refusal marks only that item, for a while,
 * so other items keep their location and a passing refusal does not cost this
 * item its location forever. The mark only chooses the version of the next
 * hash; it never changes the version of an upload in progress.
 */
internal class OriginalReads(
    private val allowed: () -> Boolean,
    private val clock: () -> Long = System::currentTimeMillis,
    private val refusalMillis: Long = REFUSAL_MILLIS,
) {
    private val refusedUntil = java.util.concurrent.ConcurrentHashMap<Any, Long>()

    fun isRefused(item: Any): Boolean {
        val until = refusedUntil[item] ?: return false
        return until > clock()
    }

    /** Reads the preferred version; returns the result and whether it was the original. */
    fun <U : Any, T> openPreferred(plain: U, original: (U) -> U, opener: (U) -> T): Pair<T, Boolean> {
        if (!allowed() || isRefused(plain)) return opener(plain) to false
        return try {
            opener(original(plain)) to true
        } catch (failure: SecurityException) {
            refuse(plain)
            opener(plain) to false
        } catch (failure: UnsupportedOperationException) {
            refuse(plain)
            opener(plain) to false
        }
    }

    /**
     * Reads exactly the version an upload was hashed from. The original that
     * can no longer be read fails with [OriginalRefusedException]; the caller
     * re-hashes the item (which then picks the redacted file) and starts over.
     */
    fun <U : Any, T> openExact(plain: U, original: (U) -> U, wantOriginal: Boolean, opener: (U) -> T): T {
        if (!wantOriginal) return opener(plain)
        if (!allowed()) throw OriginalRefusedException(SecurityException("ACCESS_MEDIA_LOCATION not granted"))
        return try {
            opener(original(plain))
        } catch (failure: SecurityException) {
            refuse(plain)
            throw OriginalRefusedException(failure)
        } catch (failure: UnsupportedOperationException) {
            refuse(plain)
            throw OriginalRefusedException(failure)
        }
    }

    private fun refuse(item: Any) {
        val now = clock()
        if (refusedUntil.size >= MAX_TRACKED) refusedUntil.entries.removeIf { it.value <= now }
        refusedUntil[item] = now + refusalMillis
    }

    private companion object {
        const val REFUSAL_MILLIS = 60L * 60 * 1000
        const val MAX_TRACKED = 1_000
    }
}
