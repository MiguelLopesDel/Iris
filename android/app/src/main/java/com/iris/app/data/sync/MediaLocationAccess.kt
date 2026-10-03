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
 * Chooses between the original and the redacted file for every read of one
 * process, consistently.
 *
 * Deciding per open let one upload hash the original and send a chunk of the
 * redacted file after a passing refusal: the server rejected the mix, a
 * re-read of the original matched the declared hash, and the item was failed
 * for good. Now a refusal fails that read as an [OriginalRefusedException]
 * (an IOException: the item is retried), and every later read uses the
 * redacted file. The next attempt then hashes and sends the same bytes.
 */
internal class OriginalReads(private val allowed: () -> Boolean) {
    @Volatile
    var refused: Boolean = false
        private set

    fun <U, T> open(plain: U, original: (U) -> U, opener: (U) -> T): T {
        if (refused || !allowed()) return opener(plain)
        return try {
            opener(original(plain))
        } catch (failure: SecurityException) {
            refused = true
            throw OriginalRefusedException(failure)
        } catch (failure: UnsupportedOperationException) {
            refused = true
            throw OriginalRefusedException(failure)
        }
    }
}
