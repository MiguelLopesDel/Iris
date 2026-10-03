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

/** The platform refused the original file; the read is retried later, consistently. */
class OriginalRefusedException(cause: Throwable) : java.io.IOException("Original media refused", cause)

/**
 * Chooses between the original and the redacted file for each media item.
 *
 * Deciding per open let one upload hash the original and send a chunk of the
 * redacted file after a passing refusal. The server rejected the mix, a
 * re-read of the original matched the declared hash, and the item was failed
 * for good. Now a refusal fails that read with [OriginalRefusedException] (an
 * IOException: the item is retried) and marks only that item. Its reads use
 * the redacted file until the mark expires, so its next attempt hashes and
 * sends the same bytes; other items keep their location. After the mark
 * expires, the original is tried again. If the declared hash then describes
 * the other version, the server refuses it (422) and the upload re-hashes the
 * file and starts over: it converges and never fails for good.
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

    fun <U : Any, T> open(plain: U, original: (U) -> U, opener: (U) -> T): T {
        if (!allowed() || isRefused(plain)) return opener(plain)
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
