package com.iris.app.data

import android.Manifest
import android.content.ContentValues
import android.content.Context
import android.content.pm.PackageManager
import android.media.MediaScannerConnection
import android.os.Build
import android.os.Environment
import android.provider.MediaStore
import androidx.annotation.RequiresApi
import com.iris.app.data.remote.IrisApiClient
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.Request
import java.io.File

/**
 * Saves a library item into the device's Downloads folder.
 *
 * The media lives behind the server's authentication, so it is fetched with the
 * app's authenticated client rather than handed to DownloadManager, which has
 * no way to carry the device token.
 */
class MediaDownloader(
    private val context: Context,
    private val apiClient: IrisApiClient,
) {

    sealed interface Result {
        data class Saved(val displayName: String) : Result
        data class Failed(val reason: String) : Result
    }

    companion object {
        /**
         * What to request before [download]; empty from Android 10 on.
         *
         * Both, not just WRITE: on Android 8-9 each storage permission is
         * granted on its own, and the system mounts external storage writable
         * only when READ is granted as well. With WRITE alone the file write
         * still fails with "Permission denied" (verified on an API 26 emulator).
         */
        fun missingPermissions(context: Context): List<String> {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) return emptyList()
            return listOf(
                Manifest.permission.READ_EXTERNAL_STORAGE,
                Manifest.permission.WRITE_EXTERNAL_STORAGE,
            ).filter { context.checkSelfPermission(it) != PackageManager.PERMISSION_GRANTED }
        }
    }

    suspend fun download(url: String, fileName: String): Result = withContext(Dispatchers.IO) {
        if (url.isBlank()) return@withContext Result.Failed("Mídia sem endereço")
        if (missingPermissions(context).isNotEmpty()) {
            return@withContext Result.Failed("permita o acesso ao armazenamento para baixar")
        }
        try {
            val request = Request.Builder().url(url).build()
            apiClient.authenticatedOkHttpClient.newCall(request).execute().use { response ->
                if (!response.isSuccessful) {
                    return@withContext Result.Failed("O servidor respondeu ${response.code}")
                }
                val body = response.body ?: return@withContext Result.Failed("Resposta vazia")
                val mimeType = response.header("Content-Type") ?: "application/octet-stream"

                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                    saveWithMediaStore(fileName, mimeType) { output ->
                        body.byteStream().copyTo(output)
                    }
                } else {
                    // Before scoped storage there is no MediaStore Downloads
                    // collection to insert into; the public directory is the
                    // only destination, and writing to it needs the runtime
                    // storage grants checked above.
                    saveToPublicDirectory(fileName) { output ->
                        body.byteStream().copyTo(output)
                    }
                }
            }
        } catch (e: Exception) {
            Result.Failed(e.localizedMessage ?: "Falha ao baixar")
        }
    }

    // Only reachable from the SDK_INT >= Q branch; the annotation lets lint
    // prove that, instead of trusting the caller.
    @RequiresApi(Build.VERSION_CODES.Q)
    private fun saveWithMediaStore(
        fileName: String,
        mimeType: String,
        write: (java.io.OutputStream) -> Unit,
    ): Result {
        val resolver = context.contentResolver
        val values = ContentValues().apply {
            put(MediaStore.Downloads.DISPLAY_NAME, fileName)
            put(MediaStore.Downloads.MIME_TYPE, mimeType)
            put(MediaStore.Downloads.IS_PENDING, 1)
        }
        val collection = MediaStore.Downloads.EXTERNAL_CONTENT_URI
        val item = resolver.insert(collection, values)
            ?: return Result.Failed("Não foi possível criar o arquivo")

        return try {
            resolver.openOutputStream(item)?.use(write)
                ?: return Result.Failed("Não foi possível escrever o arquivo")
            values.clear()
            values.put(MediaStore.Downloads.IS_PENDING, 0)
            resolver.update(item, values, null, null)
            Result.Saved(fileName)
        } catch (e: Exception) {
            // A half-written pending entry would linger in Downloads as a file
            // the user cannot open.
            resolver.delete(item, null, null)
            Result.Failed(e.localizedMessage ?: "Falha ao gravar")
        }
    }

    @Suppress("DEPRECATION")
    private fun saveToPublicDirectory(
        fileName: String,
        write: (java.io.OutputStream) -> Unit,
    ): Result {
        val directory = Environment.getExternalStoragePublicDirectory(
            Environment.DIRECTORY_DOWNLOADS
        )
        if (!directory.exists() && !directory.mkdirs()) {
            return Result.Failed("Pasta de downloads indisponível")
        }
        val target = uniqueFile(directory, fileName)
        return try {
            target.outputStream().use(write)
            // Without a scan the file exists but no gallery or Downloads app
            // lists it until the next full media scan.
            MediaScannerConnection.scanFile(context, arrayOf(target.absolutePath), null, null)
            Result.Saved(target.name)
        } catch (e: Exception) {
            target.delete()
            Result.Failed(e.localizedMessage ?: "Falha ao gravar")
        }
    }

    /** Downloading the same photo twice should not overwrite the first copy. */
    private fun uniqueFile(directory: File, fileName: String): File {
        val base = fileName.substringBeforeLast('.', fileName)
        val extension = fileName.substringAfterLast('.', "")
        var candidate = File(directory, fileName)
        var counter = 1
        while (candidate.exists()) {
            val suffix = if (extension.isEmpty()) "" else ".$extension"
            candidate = File(directory, "$base ($counter)$suffix")
            counter++
        }
        return candidate
    }
}
