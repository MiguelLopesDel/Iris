package com.iris.app.data

import android.content.ClipData
import android.content.Context
import android.content.Intent
import android.net.Uri
import androidx.core.content.FileProvider
import com.iris.app.data.remote.IrisApiClient
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.Request
import java.io.File

/**
 * Shares a library item itself, the way a gallery does: the receiving app
 * (a chat, an e-mail) gets the photo or video, not a link to the server.
 *
 * The media sits behind the server's authentication, which the receiving app
 * does not have, so it is fetched with the app's client into a cache folder
 * and handed over through a FileProvider with a read grant.
 */
class MediaSharer(
    private val context: Context,
    private val apiClient: IrisApiClient,
) {
    sealed interface Result {
        data class Ready(val intent: Intent) : Result
        data class Failed(val reason: String) : Result
    }

    suspend fun prepare(url: String, fileName: String): Result = withContext(Dispatchers.IO) {
        if (url.isBlank()) return@withContext Result.Failed("Mídia sem endereço")
        try {
            val request = Request.Builder().url(url).build()
            apiClient.authenticatedOkHttpClient.newCall(request).execute().use { response ->
                if (!response.isSuccessful) {
                    return@withContext Result.Failed("O servidor respondeu ${response.code}")
                }
                val body = response.body ?: return@withContext Result.Failed("Resposta vazia")
                val mimeType = SharedMediaFile.mimeType(response.header("Content-Type"), fileName)
                val folder = File(context.cacheDir, SHARE_DIR).apply { mkdirs() }
                // One shared item at a time: the previous one has been handed over already.
                folder.listFiles()?.forEach { it.delete() }
                val file = File(folder, SharedMediaFile.safeName(fileName))
                file.outputStream().use { output -> body.byteStream().copyTo(output) }
                Result.Ready(intentFor(contentUriOf(file), mimeType))
            }
        } catch (e: Exception) {
            Result.Failed(e.localizedMessage ?: "Falha ao preparar")
        }
    }

    private fun contentUriOf(file: File): Uri =
        FileProvider.getUriForFile(context, "${context.packageName}.files", file)

    private fun intentFor(uri: Uri, mimeType: String): Intent {
        val send = Intent(Intent.ACTION_SEND).apply {
            type = mimeType
            putExtra(Intent.EXTRA_STREAM, uri)
            // The chooser passes the grant on only when the URI is also in ClipData.
            clipData = ClipData.newRawUri(null, uri)
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        }
        return Intent.createChooser(send, "Compartilhar").apply {
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        }
    }

    private companion object {
        const val SHARE_DIR = "shared"
    }
}

/** Naming and typing of a shared file, apart from Android so it can be tested. */
internal object SharedMediaFile {
    private val unsafe = Regex("[\\\\/:*?\"<>|\\u0000-\\u001f]")

    /** The file name the receiving app shows; never a path. */
    fun safeName(fileName: String): String {
        val cleaned = fileName.substringAfterLast('/').replace(unsafe, "_").trim().trimStart('.')
        return cleaned.ifBlank { "midia" }.take(120)
    }

    /** The server's type when it names one, else guessed from the extension. */
    fun mimeType(contentType: String?, fileName: String): String {
        val declared = contentType?.substringBefore(';')?.trim()?.lowercase()
        if (!declared.isNullOrBlank() && declared != "application/octet-stream") return declared
        return when (fileName.substringAfterLast('.', "").lowercase()) {
            "jpg", "jpeg" -> "image/jpeg"
            "png" -> "image/png"
            "gif" -> "image/gif"
            "webp" -> "image/webp"
            "heic", "heif" -> "image/heif"
            "mp4", "m4v" -> "video/mp4"
            "mov" -> "video/quicktime"
            "webm" -> "video/webm"
            "mp3" -> "audio/mpeg"
            else -> "application/octet-stream"
        }
    }
}
