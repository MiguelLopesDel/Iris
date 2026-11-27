package com.iris.app.data.update

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.provider.Settings
import androidx.core.content.FileProvider
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.File
import java.util.concurrent.TimeUnit

/** A published release of the app: a stable vX.Y.Z GitHub Release with an APK attached. */
data class AppRelease(val version: String, val apkUrl: String)

private val STABLE_TAG = Regex("""^v?(\d+)\.(\d+)\.(\d+)$""")

/** `0.9.0` / `v0.9.0` as numbers; pre-releases and anything else are null. */
fun parseVersion(text: String): List<Int>? =
    STABLE_TAG.matchEntire(text.trim())?.groupValues?.drop(1)?.map { it.toInt() }

/** True only when [candidate] is a stable version strictly above [current]. */
fun isNewer(candidate: String, current: String): Boolean {
    val new = parseVersion(candidate) ?: return false
    val old = parseVersion(current) ?: return false
    for (index in 0..2) {
        if (new[index] != old[index]) return new[index] > old[index]
    }
    return false
}

private val LENIENT_JSON = Json { ignoreUnknownKeys = true }

/**
 * The `releases/latest` answer from the GitHub API, reduced to what the app needs.
 * Drafts, pre-releases and releases without an APK are not updates.
 */
fun parseLatestRelease(json: String): AppRelease? {
    val root = runCatching { LENIENT_JSON.parseToJsonElement(json).jsonObject }.getOrNull() ?: return null
    if (root.flag("draft") || root.flag("prerelease")) return null
    val tag = root["tag_name"]?.jsonPrimitive?.contentOrNull ?: return null
    val version = tag.removePrefix("v")
    if (parseVersion(version) == null) return null
    val apk = root["assets"]?.let { runCatching { it.jsonArray }.getOrNull() }
        ?.mapNotNull { runCatching { it.jsonObject }.getOrNull() }
        ?.firstOrNull { it.text("name")?.endsWith(".apk") == true }
        ?.text("browser_download_url")
        ?.takeIf { it.startsWith("https://") }
        ?: return null
    return AppRelease(version, apk)
}

private fun JsonObject.flag(key: String) = this[key]?.jsonPrimitive?.booleanOrNull == true
private fun JsonObject.text(key: String) = this[key]?.jsonPrimitive?.contentOrNull

/**
 * Checks GitHub for a newer app at most every [interval] ms and remembers the answer,
 * so a launch without network still shows a release found earlier. Only release
 * builds check: lab and debug builds have another id or signing key, and the
 * release APK cannot replace them.
 */
class AppUpdates(
    private val context: Context,
    private val currentVersion: String,
    private val enabled: Boolean,
    private val client: OkHttpClient = OkHttpClient.Builder().callTimeout(20, TimeUnit.SECONDS).build(),
    private val clock: () -> Long = System::currentTimeMillis,
    private val interval: Long = 12 * 60 * 60 * 1000L,
    private val latestUrl: String = LATEST_RELEASE_URL,
) {
    private val prefs = context.getSharedPreferences("app_updates", Context.MODE_PRIVATE)
    private val _available = MutableStateFlow(remembered())
    /** A newer release the user has not dismissed, or null. */
    val available: StateFlow<AppRelease?> = _available.asStateFlow()

    private fun remembered(): AppRelease? {
        val version = prefs.getString(KEY_VERSION, null) ?: return null
        val url = prefs.getString(KEY_URL, null) ?: return null
        return AppRelease(version, url).takeIf { offerable(it) }
    }

    private fun offerable(release: AppRelease) =
        enabled && isNewer(release.version, currentVersion) && prefs.getString(KEY_DISMISSED, null) != release.version

    suspend fun refresh(force: Boolean = false) {
        if (!enabled) return
        val last = prefs.getLong(KEY_CHECKED_AT, 0L)
        if (!force && clock() - last < interval) return
        val release = withContext(Dispatchers.IO) {
            runCatching {
                val request = Request.Builder().url(latestUrl)
                    .header("Accept", "application/vnd.github+json")
                    .build()
                client.newCall(request).execute().use { response ->
                    if (response.isSuccessful) response.body?.string()?.let(::parseLatestRelease) else null
                }
            }.getOrNull()
        } ?: return // offline or rate-limited: try again on a later launch
        prefs.edit()
            .putLong(KEY_CHECKED_AT, clock())
            .putString(KEY_VERSION, release.version)
            .putString(KEY_URL, release.apkUrl)
            .apply()
        _available.value = release.takeIf { offerable(it) }
    }

    /** "Not now": hide this version until a newer one is published. */
    fun dismiss(release: AppRelease) {
        prefs.edit().putString(KEY_DISMISSED, release.version).apply()
        _available.value = null
    }

    /** Downloads the APK into the cache folder the FileProvider shares. */
    suspend fun download(release: AppRelease): File = withContext(Dispatchers.IO) {
        val folder = File(context.cacheDir, "updates").apply { mkdirs() }
        folder.listFiles()?.forEach { it.delete() }
        val target = File(folder, "iris-${release.version}.apk")
        val request = Request.Builder().url(release.apkUrl).build()
        client.newBuilder().callTimeout(10, TimeUnit.MINUTES).build()
            .newCall(request).execute().use { response ->
                check(response.isSuccessful) { "HTTP ${response.code}" }
                val body = checkNotNull(response.body)
                target.outputStream().use { body.byteStream().copyTo(it) }
            }
        target
    }

    /**
     * Hands the APK to the system installer. Android shows its own confirmation, and
     * refuses an APK signed with another key, so a tampered download cannot install.
     * The first time, the user must allow Iris to install apps.
     */
    fun install(apk: File) {
        if (!context.packageManager.canRequestPackageInstalls()) {
            context.startActivity(
                Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:${context.packageName}"))
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            )
            return
        }
        val uri = FileProvider.getUriForFile(context, "${context.packageName}.files", apk)
        context.startActivity(
            Intent(Intent.ACTION_VIEW)
                .setDataAndType(uri, "application/vnd.android.package-archive")
                .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)
        )
    }

    companion object {
        const val LATEST_RELEASE_URL = "https://api.github.com/repos/MiguelLopesDel/Iris/releases/latest"
        private const val KEY_CHECKED_AT = "checked_at"
        private const val KEY_VERSION = "latest_version"
        private const val KEY_URL = "latest_apk_url"
        private const val KEY_DISMISSED = "dismissed_version"
    }
}
