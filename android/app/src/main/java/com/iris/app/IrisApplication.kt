package com.iris.app

import android.app.Application
import coil.ImageLoader
import coil.ImageLoaderFactory
import coil.decode.VideoFrameDecoder
import coil.disk.DiskCache
import coil.memory.MemoryCache
import coil.util.DebugLogger
import com.iris.app.data.remote.IrisApiClient
import com.iris.app.data.repository.IrisRepository
import com.iris.app.data.repository.ServerSettingsRepository
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch

class IrisApplication : Application(), ImageLoaderFactory {

    private val applicationScope = CoroutineScope(SupervisorJob() + Dispatchers.Main)

    lateinit var settingsRepository: ServerSettingsRepository
        private set

    lateinit var apiClient: IrisApiClient
        private set

    lateinit var irisRepository: IrisRepository
        private set

    override fun onCreate() {
        super.onCreate()
        instance = this

        settingsRepository = ServerSettingsRepository(this)
        apiClient = IrisApiClient()
        irisRepository = IrisRepository(apiClient)

        // Observe server URL changes from DataStore and update the API client
        applicationScope.launch {
            val initialUrl = settingsRepository.serverUrl.first()
            apiClient.updateBaseUrl(initialUrl)

            settingsRepository.serverUrl.collect { url ->
                apiClient.updateBaseUrl(url)
            }
        }
    }

    override fun newImageLoader(): ImageLoader {
        return ImageLoader.Builder(this)
            .components {
                add(VideoFrameDecoder.Factory())
            }
            .memoryCache {
                MemoryCache.Builder(this)
                    .maxSizePercent(0.25)
                    .build()
            }
            .diskCache {
                DiskCache.Builder()
                    .directory(cacheDir.resolve("image_cache"))
                    .maxSizePercent(0.05)
                    .build()
            }
            .crossfade(true)
            .build()
    }

    companion object {
        lateinit var instance: IrisApplication
            private set
    }
}
