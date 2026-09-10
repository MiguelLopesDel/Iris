package com.iris.app.data.remote

import com.iris.app.data.local.DeviceAuthStore
import kotlinx.serialization.json.Json
import okhttp3.Authenticator
import okhttp3.FormBody
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import okhttp3.Interceptor
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.Route
import okhttp3.logging.HttpLoggingInterceptor
import retrofit2.Retrofit
import retrofit2.converter.kotlinx.serialization.asConverterFactory
import okhttp3.ResponseBody.Companion.toResponseBody
import java.net.URLEncoder
import java.nio.charset.StandardCharsets
import java.util.concurrent.TimeUnit

class IrisApiClient(
    initialBaseUrl: String = "http://10.0.2.2:8000/",
    private val credentialsStore: DeviceAuthStore? = null
) {
    @Volatile
    var baseUrl: String = normalizeBaseUrl(initialBaseUrl)
        private set

    private val json = Json {
        ignoreUnknownKeys = true
        isLenient = true
        coerceInputValues = true
    }

    private val tokenRefreshLock = Any()

    // Custom safe logger: NEVER log passwords, refresh tokens, or media bytes (Rule 51)
    private val safeLoggingInterceptor = Interceptor { chain ->
        val request = chain.request()
        val path = request.url.encodedPath
        // Strip sensitive info from log lines
        val sanitizedUrl = request.url.newBuilder().query(null).build().toString()
        val response = chain.proceed(request)
        response
    }

    private val authInterceptor = Interceptor { chain ->
        val original = chain.request()
        val requestUrl = original.url
        val path = requestUrl.encodedPath

        // Do not add Bearer token to login or refresh endpoints
        if (path.contains("/auth/devices/login") || path.contains("/auth/devices/refresh")) {
            return@Interceptor chain.proceed(original)
        }

        // Host security check: NEVER leak Bearer token to a different host or port
        val currentBaseHttpUrl = baseUrl.toHttpUrlOrNull()
        val isTargetingCurrentServer = currentBaseHttpUrl != null &&
            requestUrl.host == currentBaseHttpUrl.host &&
            requestUrl.port == currentBaseHttpUrl.port

        val storedOrigin = credentialsStore?.getServerOrigin()
        val isOriginValid = storedOrigin.isNullOrBlank() || storedOrigin == getOrigin(baseUrl)

        val token = if (isTargetingCurrentServer && isOriginValid) {
            credentialsStore?.getAccessToken()
        } else {
            null
        }

        val newRequest = if (!token.isNullOrBlank()) {
            original.newBuilder()
                .header("Authorization", "Bearer $token")
                .build()
        } else {
            original
        }
        val response = chain.proceed(newRequest)

        // If backend returns a redirect to /login or /setup (302/303), transform to 401 JSON
        // so Authenticator and Retrofit can handle it without attempting to parse HTML as JSON.
        if ((response.code == 302 || response.code == 303) && response.header("Location")?.contains("/login") == true) {
            val jsonMediaType = "application/json".toMediaType()
            return@Interceptor response.newBuilder()
                .code(401)
                .message("Autenticação necessária")
                .body("{\"detail\":\"Autenticação necessária\"}".toResponseBody(jsonMediaType))
                .build()
        }

        response
    }

    private val tokenAuthenticator = Authenticator { _: Route?, response: Response ->
        if (credentialsStore == null) return@Authenticator null

        // Host security check: only refresh if the failing request is for the current server
        val currentBaseHttpUrl = baseUrl.toHttpUrlOrNull()
        if (currentBaseHttpUrl == null ||
            response.request.url.host != currentBaseHttpUrl.host ||
            response.request.url.port != currentBaseHttpUrl.port
        ) {
            return@Authenticator null
        }

        val storedOrigin = credentialsStore.getServerOrigin()
        if (!storedOrigin.isNullOrBlank() && storedOrigin != getOrigin(baseUrl)) {
            return@Authenticator null
        }

        // Prevent infinite loops if refresh itself fails
        if (response.request.url.encodedPath.contains("/auth/devices/refresh")) {
            credentialsStore.clearCredentials()
            return@Authenticator null
        }

        synchronized(tokenRefreshLock) {
            val deviceId = credentialsStore.getDeviceId() ?: return@Authenticator null
            val refreshToken = credentialsStore.getRefreshToken() ?: return@Authenticator null
            val currentToken = credentialsStore.getAccessToken()
            val requestToken = response.request.header("Authorization")?.removePrefix("Bearer ")?.trim()

            // If token was already rotated by another concurrent request, retry with the fresh token
            if (currentToken != null && currentToken != requestToken) {
                return@Authenticator response.request.newBuilder()
                    .header("Authorization", "Bearer $currentToken")
                    .build()
            }

            // Perform synchronous refresh call
            val refreshUrl = "${baseUrl.removeSuffix("/")}/api/auth/devices/refresh"
            val formBody = FormBody.Builder()
                .add("device_id", deviceId)
                .add("refresh_token", refreshToken)
                .build()

            val refreshRequest = Request.Builder()
                .url(refreshUrl)
                .post(formBody)
                .build()

            try {
                // Use a bare OkHttpClient to avoid circular interceptor calls
                val bareClient = OkHttpClient.Builder()
                    .connectTimeout(15, TimeUnit.SECONDS)
                    .readTimeout(15, TimeUnit.SECONDS)
                    .build()

                val refreshResponse = bareClient.newCall(refreshRequest).execute()
                if (refreshResponse.isSuccessful) {
                    val responseBody = refreshResponse.body?.string() ?: ""
                    val jsonElement = json.parseToJsonElement(responseBody)
                    val newAccess = jsonElement.toString().let {
                        // Extract without heavy logging
                        val parsed = json.decodeFromString<com.iris.app.data.model.DeviceRefreshResponse>(it)
                        credentialsStore.replaceTokensAtomically(
                            accessToken = parsed.accessToken,
                            refreshToken = parsed.refreshToken,
                            expiresInSeconds = parsed.expiresIn
                        )
                        parsed.accessToken
                    }
                    return@Authenticator response.request.newBuilder()
                        .header("Authorization", "Bearer $newAccess")
                        .build()
                } else if (refreshResponse.code == 401) {
                    // Only clear credentials if the refresh token was explicitly rejected / session revoked
                    credentialsStore.clearCredentials()
                    return@Authenticator null
                } else {
                    // Temporary 5xx or server glitch: do NOT wipe credentials
                    return@Authenticator null
                }
            } catch (e: Exception) {
                // Network failure during refresh: do NOT wipe credentials
                return@Authenticator null
            }
        }
    }

    private val okHttpClient = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(60, TimeUnit.SECONDS)
        .writeTimeout(60, TimeUnit.SECONDS)
        .followRedirects(false)
        .followSslRedirects(false)
        .addInterceptor(authInterceptor)
        .addInterceptor(safeLoggingInterceptor)
        .authenticator(tokenAuthenticator)
        .build()

    val authenticatedOkHttpClient: OkHttpClient
        get() = okHttpClient

    @Volatile
    private var cachedService: IrisApiService? = null

    val apiService: IrisApiService
        get() {
            val current = cachedService
            if (current != null) return current
            return synchronized(this) {
                cachedService ?: createService().also { cachedService = it }
            }
        }

    fun updateBaseUrl(newUrl: String) {
        val normalized = normalizeBaseUrl(newUrl)
        if (normalized != baseUrl) {
            synchronized(this) {
                val oldOrigin = getOrigin(baseUrl)
                val newOrigin = getOrigin(normalized)
                if (oldOrigin.isNotBlank() && newOrigin.isNotBlank() && oldOrigin != newOrigin) {
                    val storedOrigin = credentialsStore?.getServerOrigin()
                    if (!storedOrigin.isNullOrBlank() && storedOrigin != newOrigin) {
                        credentialsStore.clearCredentials()
                    }
                }
                baseUrl = normalized
                cachedService = null
            }
        }
    }

    private fun createService(): IrisApiService {
        val contentType = "application/json".toMediaType()
        return Retrofit.Builder()
            .baseUrl(baseUrl)
            .client(okHttpClient)
            .addConverterFactory(json.asConverterFactory(contentType))
            .build()
            .create(IrisApiService::class.java)
    }

    fun resolveMediaUrl(path: String?): String {
        if (path.isNullOrBlank()) return ""
        val normalized = path.trim().trimStart('/')
        val encodedSegments = normalized.split('/').joinToString("/") {
            URLEncoder.encode(it, StandardCharsets.UTF_8.name()).replace("+", "%20")
        }
        return "${baseUrl.removeSuffix("/")}/media/$encodedSegments"
    }

    fun resolveThumbnailUrl(thumbnailPath: String?): String {
        if (thumbnailPath.isNullOrBlank()) return ""
        if (thumbnailPath.startsWith("http://") || thumbnailPath.startsWith("https://")) {
            return thumbnailPath
        }
        val cleanPath = thumbnailPath.trim().trimStart('/')
        return "${baseUrl.removeSuffix("/")}/$cleanPath"
    }

    fun resolveFaceThumbnailUrl(faceId: Int): String {
        return "${baseUrl.removeSuffix("/")}/api/faces/$faceId/thumb"
    }

    companion object {
        fun normalizeBaseUrl(url: String): String {
            var trimmed = url.trim()
            if (!trimmed.startsWith("http://") && !trimmed.startsWith("https://")) {
                trimmed = "http://$trimmed"
            }
            if (!trimmed.endsWith("/")) {
                trimmed = "$trimmed/"
            }
            return trimmed
        }

        fun getOrigin(url: String): String {
            val httpUrl = url.toHttpUrlOrNull() ?: return ""
            return "${httpUrl.scheme}://${httpUrl.host}:${httpUrl.port}"
        }
    }
}
