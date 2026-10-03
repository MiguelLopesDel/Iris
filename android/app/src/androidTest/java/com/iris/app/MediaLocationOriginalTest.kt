package com.iris.app

import android.Manifest
import android.media.MediaScannerConnection
import android.net.Uri
import android.os.ParcelFileDescriptor
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.filters.SdkSuppress
import androidx.test.platform.app.InstrumentationRegistry
import com.iris.app.data.sync.MediaPayloadSource
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import java.security.MessageDigest

/**
 * A photo owned by another app is read by Iris with its GPS coordinates zeroed
 * unless Iris asks for the original. The backup must send the original.
 */
@RunWith(AndroidJUnit4::class)
// Location redaction exists from Android 10.
@SdkSuppress(minSdkVersion = 29)
class MediaLocationOriginalTest {

    private val instrumentation = InstrumentationRegistry.getInstrumentation()
    private val context = instrumentation.targetContext
    private val shell = instrumentation.uiAutomation
    private var scanned: Uri? = null
    private val path = "/sdcard/Pictures/IrisGpsTest/gps-${System.nanoTime()}.jpg"

    @After
    fun tearDown() {
        scanned?.let { shell("content delete --uri $it") }
        shell("rm -f $path")
    }

    @Test
    fun uploads_read_the_original_photo_with_its_location() = runBlocking {
        val original = photoWithLocation()
        shell.grantRuntimePermission(context.packageName, Manifest.permission.READ_MEDIA_IMAGES)
        shell.grantRuntimePermission(context.packageName, Manifest.permission.ACCESS_MEDIA_LOCATION)
        val uri = writeAsShell(original)
        scanned = uri

        // Granting fails unless the manifest declares ACCESS_MEDIA_LOCATION,
        // which is what lifts the redaction for everything the app reads.
        val content = MediaPayloadSource(context.contentResolver) { true }.computeContent(uri)

        assertEquals("The upload must hash (and send) the file as it is", sha256(original), content.sha256)
        assertEquals(original.size.toLong(), content.size)
        assertTrue("Recorded as the original, so its chunks read the same", content.original)
    }

    /**
     * A small JPEG with GPS coordinates, made at run time: the repository
     * keeps no media fixtures. The coordinates are a generic city centre.
     */
    private fun photoWithLocation(): ByteArray {
        val file = java.io.File(context.cacheDir, "gps-${System.nanoTime()}.jpg")
        val bitmap = android.graphics.Bitmap.createBitmap(64, 48, android.graphics.Bitmap.Config.ARGB_8888)
        bitmap.eraseColor(android.graphics.Color.rgb(120, 160, 200))
        file.outputStream().use { bitmap.compress(android.graphics.Bitmap.CompressFormat.JPEG, 90, it) }
        android.media.ExifInterface(file.absolutePath).apply {
            setAttribute(android.media.ExifInterface.TAG_GPS_LATITUDE, "23/1,33/1,1/1")
            setAttribute(android.media.ExifInterface.TAG_GPS_LATITUDE_REF, "S")
            setAttribute(android.media.ExifInterface.TAG_GPS_LONGITUDE, "46/1,38/1,2/1")
            setAttribute(android.media.ExifInterface.TAG_GPS_LONGITUDE_REF, "W")
            saveAttributes()
        }
        assertTrue(
            "The fixture must carry GPS",
            android.media.ExifInterface(file.absolutePath).getLatLong(FloatArray(2)),
        )
        return file.readBytes().also { file.delete() }
    }

    /** Writes [bytes] as the shell user (another app, for MediaStore) and scans it. */
    private suspend fun writeAsShell(bytes: ByteArray): Uri {
        val staged = java.io.File(context.getExternalFilesDir(null), "gps-staged.jpg").apply { writeBytes(bytes) }
        shell("mkdir -p ${path.substringBeforeLast('/')}")
        shell("cp ${staged.absolutePath} $path")
        staged.delete()
        val result = CompletableDeferred<Uri>()
        MediaScannerConnection.scanFile(context, arrayOf(path), arrayOf("image/jpeg")) { _, uri ->
            if (uri != null) result.complete(uri) else result.completeExceptionally(IllegalStateException("not scanned"))
        }
        return withTimeout(10_000) { result.await() }
    }

    private fun shell(command: String): String =
        ParcelFileDescriptor.AutoCloseInputStream(shell.executeShellCommand(command)).use { it.readBytes().decodeToString() }

    private fun sha256(bytes: ByteArray): String =
        MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }
}
