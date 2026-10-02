package com.iris.app.data.remote

import com.iris.app.data.local.DeviceAuthStore
import com.iris.app.performance.IrisPerformanceEventListener
import com.iris.app.performance.PerformanceMonitor
import kotlinx.serialization.json.Json
import com.iris.app.data.remote.security.ConnectionSecurity
import okhttp3.Authenticator
import okhttp3.ConnectionPool
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
    private val performanceMonitor: PerformanceMonitor? = null,
    /** Per-server TLS trust and HTTP consent. The app passes its persisted policy; tests may omit it. */
    val connectionSecurity: ConnectionSecurity = ConnectionSecurity.Unrestricted,
) {
    // One pool for every client, so a change of trust can drop all open connections at once.
    private val connectionPool = ConnectionPool()

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

    private fun authInterceptor(expectedSessionIdentity: String?) = Interceptor { chain ->
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

        val sessionCredentials = if (expectedSessionIdentity == null) null else {
            credentialsStore?.getSessionCredentials(expectedSessionIdentity)
                ?: return@Interceptor sessionChangedResponse(original)
        }
        val storedOrigin = sessionCredentials?.serverOrigin ?: credentialsStore?.getServerOrigin()
        val isOriginValid = storedOrigin.isNullOrBlank() || storedOrigin == getOrigin(baseUrl)

        val token = if (isTargetingCurrentServer && isOriginValid) {
            sessionCredentials?.accessToken ?: if (expectedSessionIdentity == null) credentialsStore?.getAccessToken() else null
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

    private fun sessionChangedResponse(request: Request): Response = Response.Builder()
        .request(request)
        .protocol(okhttp3.Protocol.HTTP_1_1)
        .code(409)
        .message("Device session changed")
        .body("{\"detail\":\"Device session changed\"}".toResponseBody("application/json".toMediaType()))
        .build()

    private val bareOkHttpClient by lazy {
        OkHttpClient.Builder()
            .connectTimeout(15, TimeUnit.SECONDS)
            .readTimeout(15, TimeUnit.SECONDS)
            .connectionPool(connectionPool)
            .applyConnectionSecurity()
            .applyPerformanceMonitor()
            .build()
    }

    /** Close every open connection: the next request handshakes again under the current policy. */
    fun resetConnections() = connectionPool.evictAll()

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

    private fun tokenAuthenticator(expectedSessionIdentity: String?): Authenticator = Authenticator { _: Route?, response ->
        val store = credentialsStore ?: return@Authenticator null
        if (expectedSessionIdentity != null && store.getSessionIdentity() != expectedSessionIdentity) {
            return@Authenticator null
        }
        if (responseCount(response) >= 3) return@Authenticator null

        val currentBaseHttpUrl = baseUrl.toHttpUrlOrNull()
        if (currentBaseHttpUrl == null ||
            response.request.url.host != currentBaseHttpUrl.host ||
            response.request.url.port != currentBaseHttpUrl.port
        ) return@Authenticator null

        val initialCredentials = if (expectedSessionIdentity == null) null else {
            store.getSessionCredentials(expectedSessionIdentity) ?: return@Authenticator null
        }
        val storedOrigin = initialCredentials?.serverOrigin ?: store.getServerOrigin()
        if (!storedOrigin.isNullOrBlank() && storedOrigin != getOrigin(baseUrl)) return@Authenticator null

        if (response.request.url.encodedPath.contains("/auth/devices/refresh")) {
            if (expectedSessionIdentity == null) store.clearCredentials()
            else store.clearCredentialsIfSession(expectedSessionIdentity)
            return@Authenticator null
        }

        synchronized(tokenRefreshLock) {
            if (expectedSessionIdentity != null && store.getSessionIdentity() != expectedSessionIdentity) {
                return@synchronized null
            }
            val credentials = if (expectedSessionIdentity == null) null else {
                store.getSessionCredentials(expectedSessionIdentity) ?: return@synchronized null
            }
            val deviceId = credentials?.deviceId ?: store.getDeviceId() ?: return@synchronized null
            val refreshToken = credentials?.refreshToken ?: store.getRefreshToken() ?: return@synchronized null
            val currentToken = credentials?.accessToken ?: store.getAccessToken()
            val requestToken = response.request.header("Authorization")?.removePrefix("Bearer ")?.trim()

            if (currentToken != null && currentToken != requestToken) {
                return@synchronized response.request.newBuilder()
                    .header("Authorization", "Bearer $currentToken")
                    .build()
            }
            if (System.currentTimeMillis() - lastRefreshFailedAt < 10_000L) return@synchronized null

            val refreshRequest = Request.Builder()
                .url("${baseUrl.removeSuffix("/")}/api/auth/devices/refresh")
                .post(
                    FormBody.Builder()
                        .add("device_id", deviceId)
                        .add("refresh_token", refreshToken)
                        .build()
                )
                .build()

            try {
                val refreshResponse = bareOkHttpClient.newCall(refreshRequest).execute()
                if (refreshResponse.isSuccessful) {
                    val body = refreshResponse.body?.string() ?: ""
                    val parsed = json.decodeFromString<com.iris.app.data.model.DeviceRefreshResponse>(
                        json.parseToJsonElement(body).toString()
                    )
                    val replaced = if (expectedSessionIdentity == null) {
                        store.replaceTokensAtomically(parsed.accessToken, parsed.refreshToken, parsed.expiresIn)
                        true
                    } else {
                        store.replaceTokensAtomicallyForSession(
                            expectedSessionIdentity,
                            parsed.accessToken,
                            parsed.refreshToken,
                            parsed.expiresIn
                        )
                    }
                    if (!replaced) return@synchronized null
                    lastRefreshFailedAt = 0L
                    return@synchronized response.request.newBuilder()
                        .header("Authorization", "Bearer ${parsed.accessToken}")
                        .build()
                }
                if (refreshResponse.code == 401) {
                    if (expectedSessionIdentity == null) store.clearCredentials()
                    else store.clearCredentialsIfSession(expectedSessionIdentity)
                } else {
                    lastRefreshFailedAt = System.currentTimeMillis()
                }
                null
            } catch (_: Exception) {
                lastRefreshFailedAt = System.currentTimeMillis()
                null
            }
        }
    }

    private fun createOkHttpClient(expectedSessionIdentity: String?): OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(60, TimeUnit.SECONDS)
        .writeTimeout(60, TimeUnit.SECONDS)
        .followRedirects(false)
        .followSslRedirects(false)
        .addInterceptor(authInterceptor(expectedSessionIdentity))
        .addInterceptor(browsingTimeoutInterceptor)
        .addInterceptor(safeLoggingInterceptor)
        .authenticator(tokenAuthenticator(expectedSessionIdentity))
        // Keep the same request concurrency limit as the browsing client.
        .dispatcher(Dispatcher().apply { maxRequestsPerHost = 16 })
        .connectionPool(connectionPool)
        .applyConnectionSecurity()
        .applyPerformanceMonitor()
        .build()

    private val okHttpClient = createOkHttpClient(expectedSessionIdentity = null)

    val authenticatedOkHttpClient: OkHttpClient
        get() = okHttpClient

    @Volatile
    private var cachedService: IrisApiService? = null
    @Volatile
    private var cachedServiceSessionIdentity: String? = null

    private data class BoundServiceCache(
        val baseUrl: String,
        val sessionIdentity: String,
        val service: IrisApiService
    )

    @Volatile
    private var cachedBoundService: BoundServiceCache? = null

    val apiService: IrisApiService
        get() {
            val sessionIdentity = credentialsStore?.getSessionIdentity()
            val current = cachedService
            if (current != null && cachedServiceSessionIdentity == sessionIdentity) return current
            return synchronized(this) {
                if (cachedService != null && cachedServiceSessionIdentity == sessionIdentity) {
                    cachedService!!
                } else {
                    createService(sessionIdentity).also {
                        cachedService = it
                        cachedServiceSessionIdentity = sessionIdentity
                    }
                }
            }
        }

    /**
     * API facade for one immutable credential session. If the session changes
     * before a request reaches OkHttp, that request is rejected locally rather
     * than being sent with the next account's token.
     */
    fun apiServiceForSession(sessionIdentity: String): IrisApiService {
        require(sessionIdentity.isNotBlank()) { "A session identity is required" }
        val cached = cachedBoundService
        if (cached?.baseUrl == baseUrl && cached.sessionIdentity == sessionIdentity) return cached.service
        return synchronized(this) {
            val current = cachedBoundService
            if (current?.baseUrl == baseUrl && current.sessionIdentity == sessionIdentity) {
                current.service
            } else {
                createService(sessionIdentity).also {
                    cachedBoundService = BoundServiceCache(baseUrl, sessionIdentity, it)
                }
            }
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
                cachedServiceSessionIdentity = null
                cachedBoundService = null
            }
        }
    }

    private fun createService(expectedSessionIdentity: String? = null): IrisApiService {
        val contentType = "application/json".toMediaType()
        return Retrofit.Builder()
            .baseUrl(baseUrl)
            .client(if (expectedSessionIdentity == null) okHttpClient else createOkHttpClient(expectedSessionIdentity))
            .addConverterFactory(json.asConverterFactory(contentType))
            .build()
            .create(IrisApiService::class.java)
    }

    private fun OkHttpClient.Builder.applyConnectionSecurity(): OkHttpClient.Builder =
        connectionSecurity.apply(this)

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
            if (trimmed.isBlank()) return "http://127.0.0.1:8000/"
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
