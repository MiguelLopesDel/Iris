package com.iris.app

import android.app.Application
import androidx.work.Configuration
import coil.ImageLoader
import coil.ImageLoaderFactory
import coil.decode.VideoFrameDecoder
import coil.disk.DiskCache
import coil.memory.MemoryCache
import com.iris.app.data.local.DeviceCredentialsStore
import com.iris.app.data.local.UploadDatabaseHelper
import com.iris.app.data.remote.IrisApiClient
import com.iris.app.data.repository.IrisRepository
import com.iris.app.data.repository.ServerSettingsRepository
import com.iris.app.data.catalog.MediaCatalog
import com.iris.app.data.catalog.SqliteCatalogStore
import com.iris.app.data.sync.ChangeFeedSyncManager
import com.iris.app.data.sync.MediaSyncWorker
import com.iris.app.data.sync.MediaStoreScanner
import com.iris.app.data.sync.SyncUploadManager
import com.iris.app.performance.PerformanceMonitor
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import kotlinx.coroutines.delay

class IrisApplication : Application(), ImageLoaderFactory, Configuration.Provider {

    private val applicationScope = CoroutineScope(SupervisorJob() + Dispatchers.Default)

    lateinit var settingsRepository: ServerSettingsRepository
        private set

    lateinit var credentialsStore: DeviceCredentialsStore
        private set

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

        settingsRepository = ServerSettingsRepository(this)
        credentialsStore = DeviceCredentialsStore(this)
        dbHelper = UploadDatabaseHelper(this)

        val catalogStore = SqliteCatalogStore(this)

        apiClient = IrisApiClient(
            credentialsStore = credentialsStore,
            performanceMonitor = performanceMonitor
        )

        syncUploadManager = SyncUploadManager(
            contentResolver = contentResolver,
            dbHelper = dbHelper,
            apiServiceProvider = { apiClient.apiService }
        )

        mediaStoreScanner = MediaStoreScanner(
            contentResolver = contentResolver,
            uploadManager = syncUploadManager
        )

        changeFeedSyncManager = ChangeFeedSyncManager(
            dbHelper = dbHelper,
            apiServiceProvider = { apiClient.apiService }
        )

        irisRepository = IrisRepository(
            apiClient = apiClient,
            credentialsStore = credentialsStore,
            dbHelper = dbHelper,
            uploadManager = syncUploadManager,
            mediaScanner = mediaStoreScanner,
            changeFeedSync = changeFeedSyncManager
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

        // Observe server URL changes from DataStore
        applicationScope.launch {
            val initialUrl = settingsRepository.serverUrl.first()
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
                settingsRepository.syncWifiOnly,
                settingsRepository.syncChargingOnly,
                settingsRepository.autoBackupEnabled
            ) { wifiOnly, chargingOnly, autoBackup ->
                Triple(wifiOnly, chargingOnly, autoBackup)
            }.collect { (wifiOnly, chargingOnly, autoBackup) ->
                if (autoBackup) {
                    MediaSyncWorker.schedulePeriodic(
                        context = this@IrisApplication,
                        wifiOnly = wifiOnly,
                        requiresCharging = chargingOnly
                    )
                } else {
                    MediaSyncWorker.cancelPeriodic(this@IrisApplication)
                }
            }
        }

        // Poll change feed on app open (contract section 38)
        applicationScope.launch(Dispatchers.IO) {
            isServerConfigurationReady.first { it }
            delay(BACKGROUND_START_DELAY_MS)
            if (credentialsStore.hasValidCredentials()) {
                changeFeedSyncManager.syncChanges()
            }
        }

        // Pull the recent catalog into the local mirror so the gallery opens
        // from disk and keeps scrolling when the server is slow or unreachable.
        // Bounded per launch: newest-first in coarse pages, so a few requests
        // cover far more than a user scrolls in one sitting. Runs after the
        // first frame and off the UI path, and browsing keeps warming the
        // mirror on its own.
        applicationScope.launch(Dispatchers.IO) {
            isServerConfigurationReady.first { it }
            delay(BACKGROUND_START_DELAY_MS)
            if (credentialsStore.hasValidCredentials()) {
                runCatching { mediaCatalog.reconcile(maxPages = CATALOG_RECONCILE_PAGES) }
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

        lateinit var instance: IrisApplication
            private set
    }
}
