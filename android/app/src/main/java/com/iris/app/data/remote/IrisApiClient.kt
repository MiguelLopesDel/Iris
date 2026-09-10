package com.iris.app.data.remote

import com.iris.app.data.local.DeviceAuthStore
import com.iris.app.performance.IrisPerformanceEventListener
import com.iris.app.performance.PerformanceMonitor
import kotlinx.serialization.json.Json
import okhttp3.Authenticator
import okhttp3.Dispatcher
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
    private val credentialsStore: DeviceAuthStore? = null,
    private val performanceMonitor: PerformanceMonitor? = null
) {
    @Volatile
    var baseUrl: String = normalizeBaseUrl(initialBaseUrl)
        private set

    private val json = RESPONSE_JSON

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

    /**
     * Browsing must fail promptly when a VPN path stalls. Uploads keep the
     * longer client timeout because they can legitimately transfer large files.
     */
    private val browsingTimeoutInterceptor = Interceptor { chain ->
        val request = chain.request()
        val isBrowseRequest = request.method == "GET" && (
            request.url.encodedPath.endsWith("/healthz") ||
                request.url.encodedPath.contains("/api/info") ||
                request.url.encodedPath.contains("/api/records")
            )
        if (isBrowseRequest) {
            chain
                .withConnectTimeout(8, TimeUnit.SECONDS)
                .withReadTimeout(15, TimeUnit.SECONDS)
                .proceed(request)
        } else {
            chain.proceed(request)
        }
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

        // A reverse proxy or an Iris setup/login guard can redirect an API call to
        // HTML. Convert that response into JSON-shaped 401 instead of letting
        // Retrofit report an opaque "Unexpected JSON token" parse error.
        val redirectLocation = response.header("Location")
        if ((response.code == 302 || response.code == 303 || response.code == 307 || response.code == 308) &&
            (redirectLocation?.contains("/login") == true || redirectLocation?.contains("/setup") == true)
        ) {
            val jsonMediaType = "application/json".toMediaType()
            return@Interceptor response.newBuilder()
                .code(401)
                .message("Autenticação necessária")
                .body("{\"detail\":\"Autenticação necessária\"}".toResponseBody(jsonMediaType))
                .build()
        }

        response
    }

    private val bareOkHttpClient by lazy {
        OkHttpClient.Builder()
            .connectTimeout(15, TimeUnit.SECONDS)
            .readTimeout(15, TimeUnit.SECONDS)
            .applyPerformanceMonitor()
            .build()
    }

    @Volatile
    private var lastRefreshFailedAt = 0L

    private fun responseCount(response: Response): Int {
        var count = 1
        var prior = response.priorResponse
        while (prior != null) {
            count++
            prior = prior.priorResponse
        }
        return count
    }

    private val tokenAuthenticator = Authenticator { _: Route?, response: Response ->
        if (credentialsStore == null) return@Authenticator null
        if (responseCount(response) >= 3) return@Authenticator null

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

            // Fast-fail if refresh failed in the last 10 seconds to avoid cascading timeouts for 50 concurrent requests
            if (System.currentTimeMillis() - lastRefreshFailedAt < 10_000L) {
                return@Authenticator null
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
                val refreshResponse = bareOkHttpClient.newCall(refreshRequest).execute()
                if (refreshResponse.isSuccessful) {
                    val responseBody = refreshResponse.body?.string() ?: ""
                    val jsonElement = json.parseToJsonElement(responseBody)
                    val newAccess = jsonElement.toString().let {
                        val parsed = json.decodeFromString<com.iris.app.data.model.DeviceRefreshResponse>(it)
                        credentialsStore.replaceTokensAtomically(
                            accessToken = parsed.accessToken,
                            refreshToken = parsed.refreshToken,
                            expiresInSeconds = parsed.expiresIn
                        )
                        parsed.accessToken
                    }
                    lastRefreshFailedAt = 0L
                    return@Authenticator response.request.newBuilder()
                        .header("Authorization", "Bearer $newAccess")
                        .build()
                } else if (refreshResponse.code == 401) {
                    // Only clear credentials if the refresh token was explicitly rejected / session revoked
                    credentialsStore.clearCredentials()
                    return@Authenticator null
                } else {
                    lastRefreshFailedAt = System.currentTimeMillis()
                    return@Authenticator null
                }
            } catch (e: Exception) {
                // Network failure during refresh: fast-fail other queued requests without wiping credentials
                lastRefreshFailedAt = System.currentTimeMillis()
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
        .addInterceptor(browsingTimeoutInterceptor)
        .addInterceptor(safeLoggingInterceptor)
        .authenticator(tokenAuthenticator)
        // Coil reuses this same client for every gallery thumbnail (see
        // IrisApplication.newImageLoader), so OkHttp's default of 5 concurrent
        // requests per host was shared between thumbnail downloads and the
        // paginated /api/records calls — a fast scroll saturated it with
        // thumbnails and left page-fetch requests queued behind them. This is
        // a private single-user home server, not a rate-limited public API.
        .dispatcher(Dispatcher().apply { maxRequestsPerHost = 16 })
        .applyPerformanceMonitor()
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

    private fun OkHttpClient.Builder.applyPerformanceMonitor(): OkHttpClient.Builder = apply {
        performanceMonitor?.let { eventListenerFactory(IrisPerformanceEventListener.factory(it)) }
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
        /**
         * How every server response is decoded. Exposed so tests assert against
         * the real configuration instead of a copy that can drift from it —
         * `coerceInputValues` in particular is what lets a null from the server
         * fall back to a model's default rather than failing the whole response.
         */
        val RESPONSE_JSON = Json {
            ignoreUnknownKeys = true
            isLenient = true
            coerceInputValues = true
        }

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
