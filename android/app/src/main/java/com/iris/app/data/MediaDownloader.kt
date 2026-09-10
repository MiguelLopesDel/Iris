package com.iris.app.data

import android.content.ContentValues
import android.content.Context
import android.os.Build
import android.os.Environment
import android.provider.MediaStore
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

    suspend fun download(url: String, fileName: String): Result = withContext(Dispatchers.IO) {
        if (url.isBlank()) return@withContext Result.Failed("Mídia sem endereço")
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
                    // only destination, and READ/WRITE is granted at install.
                    saveToPublicDirectory(fileName) { output ->
                        body.byteStream().copyTo(output)
                    }
                }
            }
        } catch (e: Exception) {
            Result.Failed(e.localizedMessage ?: "Falha ao baixar")
        }
    }

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
