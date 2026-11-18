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
import java.util.UUID

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

    suspend fun prepare(url: String, fileName: String): Result = prepareAll(listOf(url to fileName))

    /** Fetches every item, then hands them over in one share: a chat gets them all at once. */
    suspend fun prepareAll(items: List<Pair<String, String>>): Result = withContext(Dispatchers.IO) {
        if (items.isEmpty()) return@withContext Result.Failed("Nada para compartilhar")
        if (items.any { it.first.isBlank() }) return@withContext Result.Failed("Mídia sem endereço")
        try {
            val folder = SharedMediaFile.createShareDirectory(
                File(context.cacheDir, SHARE_DIR),
                System.currentTimeMillis(),
            )
            val fetched = items.mapIndexed { position, (url, fileName) ->
                val request = Request.Builder().url(url).build()
                apiClient.authenticatedOkHttpClient.newCall(request).execute().use { response ->
                    if (!response.isSuccessful) {
                        return@withContext Result.Failed("O servidor respondeu ${response.code}")
                    }
                    val body = response.body ?: return@withContext Result.Failed("Resposta vazia")
                    val mimeType = SharedMediaFile.mimeType(response.header("Content-Type"), fileName)
                    // Two photos may share a name; each keeps its own file.
                    val name = SharedMediaFile.safeName(fileName)
                    val file = File(folder, if (items.size == 1) name else "${position + 1}-$name")
                    file.outputStream().use { output -> body.byteStream().copyTo(output) }
                    contentUriOf(file) to mimeType
                }
            }
            Result.Ready(intentFor(fetched))
        } catch (e: Exception) {
            Result.Failed(e.localizedMessage ?: "Falha ao preparar")
        }
    }

    private fun contentUriOf(file: File): Uri =
        FileProvider.getUriForFile(context, "${context.packageName}.files", file)

    private fun intentFor(files: List<Pair<Uri, String>>): Intent {
        val uris = files.map { it.first }
        val send = if (uris.size == 1) {
            Intent(Intent.ACTION_SEND).putExtra(Intent.EXTRA_STREAM, uris.single())
        } else {
            Intent(Intent.ACTION_SEND_MULTIPLE).putParcelableArrayListExtra(Intent.EXTRA_STREAM, ArrayList(uris))
        }.apply {
            type = SharedMediaFile.commonMimeType(files.map { it.second })
            // The chooser passes the grant on only when the URIs are also in ClipData.
            clipData = ClipData.newRawUri(null, uris.first()).apply {
                uris.drop(1).forEach { addItem(ClipData.Item(it)) }
            }
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
    /** One type for a share of several files: theirs when equal, the family wildcard when alike, else any. */
    fun commonMimeType(types: List<String>): String {
        val distinct = types.distinct()
        if (distinct.size == 1) return distinct.single()
        val families = distinct.map { it.substringBefore('/') }.distinct()
        return if (families.size == 1) "${families.single()}/*" else "*/*"
    }

    private val unsafe = Regex("[\\\\/:*?\"<>|\\u0000-\\u001f]")
    private val shareDirectoryName = Regex("[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
    private const val SHARE_RETENTION_MS = 24L * 60 * 60 * 1000

    /** Creates an isolated path per share and prunes only expired entries from this cache folder. */
    fun createShareDirectory(root: File, nowMillis: Long = System.currentTimeMillis()): File {
        root.mkdirs()
        val cutoff = nowMillis - SHARE_RETENTION_MS
        root.listFiles()?.forEach { entry ->
            if (entry.lastModified() < cutoff &&
                (!entry.isDirectory || shareDirectoryName.matches(entry.name))
            ) {
                if (entry.isDirectory) entry.deleteRecursively() else entry.delete()
            }
        }
        return File(root, UUID.randomUUID().toString()).apply {
            check(mkdirs()) { "Could not create isolated share directory" }
        }
    }

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
