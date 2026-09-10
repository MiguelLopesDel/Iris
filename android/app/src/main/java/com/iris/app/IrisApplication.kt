package com.iris.app

import android.app.Application
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
import com.iris.app.data.sync.ChangeFeedSyncManager
import com.iris.app.data.sync.MediaSyncWorker
import com.iris.app.data.sync.MediaStoreScanner
import com.iris.app.data.sync.SyncUploadManager
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch

class IrisApplication : Application(), ImageLoaderFactory {

    private val applicationScope = CoroutineScope(SupervisorJob() + Dispatchers.Main)

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

    override fun onCreate() {
        super.onCreate()
        instance = this

        settingsRepository = ServerSettingsRepository(this)
        credentialsStore = DeviceCredentialsStore(this)
        dbHelper = UploadDatabaseHelper(this)

        apiClient = IrisApiClient(
            credentialsStore = credentialsStore
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

        // Observe server URL changes from DataStore
        applicationScope.launch {
            val initialUrl = settingsRepository.serverUrl.first()
            apiClient.updateBaseUrl(initialUrl)

            settingsRepository.serverUrl.collect { url ->
                apiClient.updateBaseUrl(url)
            }
        }

        // Schedule periodic WorkManager sync respecting user preferences
        applicationScope.launch {
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
                }
            }
        }

        // Poll change feed on app open (contract section 38)
        applicationScope.launch(Dispatchers.IO) {
            if (credentialsStore.hasValidCredentials()) {
                changeFeedSyncManager.syncChanges()
            }
        }
    }

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
            .crossfade(true)
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
        lateinit var instance: IrisApplication
            private set
    }
}
