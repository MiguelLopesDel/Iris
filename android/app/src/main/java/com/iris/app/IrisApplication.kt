package com.iris.app

import android.app.Activity
import android.app.Application
import android.os.Bundle
import androidx.work.Configuration
import coil.ImageLoader
import coil.ImageLoaderFactory
import coil.decode.VideoFrameDecoder
import coil.disk.DiskCache
import coil.memory.MemoryCache
import com.iris.app.data.local.DeviceCredentialsStore
import com.iris.app.data.local.UploadDatabaseHelper
import com.iris.app.data.remote.IrisApiClient
import com.iris.app.data.remote.security.ConnectionSecurity
import com.iris.app.data.remote.security.PreferencesServerSecurityStore
import com.iris.app.data.repository.IrisRepository
import com.iris.app.data.repository.DefaultGalleryDataSource
import com.iris.app.data.repository.GalleryDataSource
import com.iris.app.data.repository.ServerSettingsRepository
import com.iris.app.data.catalog.MediaCatalog
import com.iris.app.data.catalog.SqliteCatalogStore
import com.iris.app.data.local.DeviceGalleryReader
import com.iris.app.data.sync.ChangeFeedSyncManager
import com.iris.app.data.sync.BackgroundSyncPolicy
import com.iris.app.data.sync.AccountSyncSession
import com.iris.app.data.sync.MediaSyncWorker
import com.iris.app.data.sync.MediaStoreScanner
import com.iris.app.data.sync.SyncUploadManager
import com.iris.app.performance.PerformanceMonitor
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import kotlinx.coroutines.delay
import kotlinx.coroutines.CancellationException
import java.security.MessageDigest

class IrisApplication : Application(), ImageLoaderFactory, Configuration.Provider {

    private val applicationScope = CoroutineScope(SupervisorJob() + Dispatchers.Default)

    lateinit var settingsRepository: ServerSettingsRepository
        private set

    lateinit var credentialsStore: DeviceCredentialsStore
        private set

    private lateinit var connectionSecurityStore: PreferencesServerSecurityStore

    /** Pairing links received from outside the app, waiting for the Settings screen to show them. */
    val pairingRequests = MutableStateFlow<String?>(null)

    lateinit var dbHelper: UploadDatabaseHelper
        private set

    lateinit var apiClient: IrisApiClient
        private set

    lateinit var syncUploadManager: SyncUploadManager
        private set

    lateinit var mediaStoreScanner: MediaStoreScanner
        private set

    lateinit var changeFeedSyncManager: ChangeFeedSyncManager
        private set

    lateinit var irisRepository: IrisRepository
        private set

    /** On-disk mirror of the catalog, so the gallery paints before the network. */
    lateinit var mediaCatalog: MediaCatalog
        private set

    lateinit var galleryDataSource: GalleryDataSource
        private set

    private var startedActivities = 0

    /**
     * Whether any activity is visible. The sync history records it at the start
     * of each run, which is how a run with the app closed is told apart.
     */
    val isAppInForeground: Boolean
        get() = synchronized(this) { startedActivities > 0 }

    /** Opt-in, local-only timing summaries shown in Settings diagnostics. */
    val performanceMonitor = PerformanceMonitor()

    /**
     * The saved server address must be applied before any screen or background
     * sync can make a request. Otherwise a cold start briefly uses the emulator
     * default address and incorrectly reports the user's server as offline.
     */
    val isServerConfigurationReady = MutableStateFlow(false)

    override fun onCreate() {
        super.onCreate()
        instance = this
        registerActivityLifecycleCallbacks(object : ActivityLifecycleCallbacks {
            override fun onActivityStarted(activity: Activity) {
                synchronized(this@IrisApplication) { startedActivities++ }
            }

            override fun onActivityStopped(activity: Activity) {
                synchronized(this@IrisApplication) {
                    startedActivities = (startedActivities - 1).coerceAtLeast(0)
                }
            }

            override fun onActivityCreated(activity: Activity, savedInstanceState: Bundle?) = Unit
            override fun onActivityResumed(activity: Activity) = Unit
            override fun onActivityPaused(activity: Activity) = Unit
            override fun onActivitySaveInstanceState(activity: Activity, outState: Bundle) = Unit
            override fun onActivityDestroyed(activity: Activity) = Unit
        })

        credentialsStore = DeviceCredentialsStore(this)
        settingsRepository = ServerSettingsRepository(this)
        dbHelper = UploadDatabaseHelper(this)

        val catalogStore = SqliteCatalogStore(this)

        connectionSecurityStore = PreferencesServerSecurityStore(this)
        apiClient = IrisApiClient(
            credentialsStore = credentialsStore,
            performanceMonitor = performanceMonitor,
            connectionSecurity = ConnectionSecurity(connectionSecurityStore),
        )

        syncUploadManager = SyncUploadManager(
            contentResolver = contentResolver,
            dbHelper = dbHelper,
            apiServiceProvider = { sessionIdentity -> apiClient.apiServiceForSession(sessionIdentity) },
            performanceMonitor = performanceMonitor
        )

        mediaStoreScanner = MediaStoreScanner(
            contentResolver = contentResolver,
            uploadManager = syncUploadManager,
            performanceMonitor = performanceMonitor
        )

        changeFeedSyncManager = ChangeFeedSyncManager(
            dbHelper = dbHelper,
            apiServiceProvider = { sessionIdentity -> apiClient.apiServiceForSession(sessionIdentity) }
        )

        irisRepository = IrisRepository(
            apiClient = apiClient,
            credentialsStore = credentialsStore,
            dbHelper = dbHelper,
            uploadManager = syncUploadManager,
            mediaScanner = mediaStoreScanner,
            changeFeedSync = changeFeedSyncManager,
            performanceMonitor = performanceMonitor,
        )

        // A galeria lê daqui antes de qualquer rede; a reconciliação alimenta
        // em lotes grossos, desacoplados do que está na tela.
        mediaCatalog = MediaCatalog(catalogStore) { page, perPage, mediaType ->
            val response = irisRepository.getRecords(
                page = page, perPage = perPage, sortBy = "data", mediaType = mediaType
            ).getOrThrow()
            MediaCatalog.FetchedPage(
                records = response.records,
                page = response.page,
                totalPages = response.totalPages,
                total = response.total,
            )
        }
        galleryDataSource = DefaultGalleryDataSource(
            serverRepository = irisRepository,
            catalog = mediaCatalog,
            deviceGalleryReader = DeviceGalleryReader(this, contentResolver),
        )

        // Coil's default URI-based cache keys do not distinguish two Iris
        // accounts on the same server. Bind the cache to the current account.
        updatePrivateImageCacheOwner(credentialsStore.sessionIdentity.value)

        // Observe server URL changes from DataStore
        applicationScope.launch {
            val initialUrl = settingsRepository.serverUrl.first()
            connectionSecurityStore.grandfatherCleartext(initialUrl)
            apiClient.updateBaseUrl(initialUrl)
            isServerConfigurationReady.value = true

            settingsRepository.serverUrl.collect { url ->
                apiClient.updateBaseUrl(url)
            }
        }

        // WorkManager initialization is expensive on some phones. Let the first
        // gallery frame render before scheduling background work.
        applicationScope.launch {
            delay(BACKGROUND_START_DELAY_MS)
            combine(
                credentialsStore.sessionIdentity,
                credentialsStore.accountIdentity,
            ) { sessionIdentity, accountKey ->
                sessionIdentity != null && accountKey != null
            }.collect { loggedIn ->
                if (BackgroundSyncPolicy.shouldSchedulePeriodicSync(isLoggedIn = loggedIn)) {
                    MediaSyncWorker.schedulePeriodic(context = this@IrisApplication)
                } else {
                    MediaSyncWorker.cancelPeriodic(this@IrisApplication)
                    if (!loggedIn) MediaSyncWorker.cancelImmediate(this@IrisApplication)
                }
            }
        }

        // Background work belongs to one credential session. collectLatest
        // cancels and joins the prior session's work before the next one can
        // touch the private mirror or image cache.
        applicationScope.launch(Dispatchers.IO) {
            var previousSession = credentialsStore.sessionIdentity.value
            credentialsStore.sessionIdentity.collectLatest { sessionIdentity ->
                val priorSession = previousSession
                val changed = sessionIdentity != previousSession
                previousSession = sessionIdentity

                if (changed || sessionIdentity == null) {
                    if (priorSession != sessionIdentity) MediaSyncWorker.cancelAll(this@IrisApplication)
                    updatePrivateImageCacheOwner(sessionIdentity)
                    if (sessionIdentity == null) mediaCatalog.clear()
                    else mediaCatalog.activateSession(sessionIdentity)
                }

                if (sessionIdentity == null) return@collectLatest
                val accountKey = credentialsStore.accountIdentity.value ?: return@collectLatest
                val syncSession = AccountSyncSession(sessionIdentity, accountKey)
                isServerConfigurationReady.first { it }
                delay(BACKGROUND_START_DELAY_MS)
                if (!syncSession.matches(
                        credentialsStore.sessionIdentity.value,
                        credentialsStore.accountIdentity.value
                    )
                ) return@collectLatest

                try {
                    // Materialize this account's preference namespace before
                    // scheduling it. Old device-wide settings had no safe owner,
                    // so accounts use independent defaults.
                    val syncSettings = settingsRepository.syncSettingsForAccount(accountKey).first()
                    if (!syncSession.matches(
                            credentialsStore.sessionIdentity.value,
                            credentialsStore.accountIdentity.value
                        )
                    ) return@collectLatest
                    MediaSyncWorker.enqueueBackground(this@IrisApplication, syncSettings)

                    // Poll change feed on app open (contract section 38).
                    changeFeedSyncManager.syncChanges(accountKey, sessionIdentity) {
                        syncSession.matches(
                            credentialsStore.sessionIdentity.value,
                            credentialsStore.accountIdentity.value
                        )
                    }
                    if (!syncSession.matches(
                            credentialsStore.sessionIdentity.value,
                            credentialsStore.accountIdentity.value
                        )
                    ) return@collectLatest

                    // Pull recent catalog rows off the UI path so the gallery
                    // opens from disk and stays useful on a slow connection.
                    mediaCatalog.reconcile(
                        maxPages = CATALOG_RECONCILE_PAGES,
                        sessionKey = sessionIdentity,
                        isSessionCurrent = {
                            syncSession.matches(
                                credentialsStore.sessionIdentity.value,
                                credentialsStore.accountIdentity.value
                            )
                        },
                    )
                } catch (cancelled: CancellationException) {
                    throw cancelled
                } catch (error: Exception) {
                    android.util.Log.w("IrisApplication", "Background library refresh failed", error)
                }
            }
        }
    }

    override val workManagerConfiguration: Configuration
        get() = Configuration.Builder().build()

    override fun newImageLoader(): ImageLoader {
        val activityManager = getSystemService(android.app.ActivityManager::class.java)
        val isLowRam = activityManager?.isLowRamDevice == true

        return ImageLoader.Builder(this)
            .okHttpClient { apiClient.authenticatedOkHttpClient }
            .components {
                add(VideoFrameDecoder.Factory())
            }
            .memoryCache {
                MemoryCache.Builder(this)
                    .maxSizePercent(if (isLowRam) 0.15 else 0.20)
                    .strongReferencesEnabled(true)
                    .build()
            }
            .diskCache {
                DiskCache.Builder()
                    .directory(cacheDir.resolve("image_cache"))
                    .maxSizeBytes(250L * 1024 * 1024)
                    .build()
            }
            .allowRgb565(isLowRam)
            .respectCacheHeaders(false)
            // A simultaneous crossfade for every first-screen thumbnail causes
            // visible frame loss on mid-range phones.
            .crossfade(false)
            .build()
    }

    @OptIn(coil.annotation.ExperimentalCoilApi::class)
    private fun updatePrivateImageCacheOwner(sessionIdentity: String?) {
        val fingerprint = sessionIdentity?.let { identity ->
            MessageDigest.getInstance("SHA-256")
                .digest(identity.toByteArray())
                .joinToString("") { byte -> "%02x".format(byte) }
        } ?: LOGGED_OUT_CACHE_OWNER
        val preferences = getSharedPreferences(IMAGE_CACHE_PREFERENCES, MODE_PRIVATE)
        if (preferences.getString(IMAGE_CACHE_OWNER_KEY, null) == fingerprint) return

        val imageLoader = coil.Coil.imageLoader(this)
        imageLoader.memoryCache?.clear()
        imageLoader.diskCache?.clear()
        preferences.edit().putString(IMAGE_CACHE_OWNER_KEY, fingerprint).apply()
    }

    @Suppress("DEPRECATION")
    override fun onTrimMemory(level: Int) {
        super.onTrimMemory(level)
        if (level >= TRIM_MEMORY_RUNNING_LOW) {
            coil.Coil.imageLoader(this).memoryCache?.clear()
        }
    }

    companion object {
        private const val BACKGROUND_START_DELAY_MS = 2_000L

        /**
         * Pages of 200 pulled into the mirror per launch. Ten covers 2.000 of
         * the most recent items for ten requests — the same stretch cost 84
         * requests at the gallery's page size. The rest of a large catalog
         * still needs a persisted cursor to walk it fully; this deliberately
         * covers the recent end rather than pretending to be a full sync.
         */
        private const val CATALOG_RECONCILE_PAGES = 10
        private const val IMAGE_CACHE_PREFERENCES = "iris_private_image_cache"
        private const val IMAGE_CACHE_OWNER_KEY = "owner_fingerprint"
        private const val LOGGED_OUT_CACHE_OWNER = "logged-out"

        lateinit var instance: IrisApplication
            private set
    }
}
