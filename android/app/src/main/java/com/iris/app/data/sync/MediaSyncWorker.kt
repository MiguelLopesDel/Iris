package com.iris.app.data.sync

import android.content.Context
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import androidx.work.workDataOf
import com.iris.app.IrisApplication
import com.iris.app.data.model.MediaScanPolicy
import kotlinx.coroutines.flow.first
import java.util.concurrent.TimeUnit

class MediaSyncWorker(
    context: Context,
    params: WorkerParameters
) : CoroutineWorker(context, params) {

    override suspend fun doWork(): Result {
        val app = applicationContext as? IrisApplication ?: return Result.failure()

        // 1. Verify valid device credentials
        if (!app.credentialsStore.hasValidCredentials()) {
            return Result.success() // Not logged in; nothing to sync
        }

        try {
            // 2. Discover new media via MediaStore
            if (app.settingsRepository.autoBackupEnabled.first() || inputData.getBoolean(FORCE_SCAN_KEY, false)) {
                val policy = MediaScanPolicy(
                    mode = app.settingsRepository.syncSourceMode.first(),
                    selectedSourceIds = app.settingsRepository.syncSelectedSourceIds.first(),
                    includeImages = app.settingsRepository.syncImagesEnabled.first(),
                    includeVideos = app.settingsRepository.syncVideosEnabled.first()
                )
                app.mediaStoreScanner.scanAndEnqueueNewMedia(policy)
            }

            // 3. Process the durable upload queue
            app.syncUploadManager.processQueue()

            // 4. Poll change feed after upload completion
            app.changeFeedSyncManager.syncChanges()

            return Result.success()
        } catch (e: Exception) {
            return Result.retry()
        }
    }

    companion object {
        private const val PERIODIC_WORK_TAG = "iris_periodic_sync"
        private const val ONE_TIME_WORK_TAG = "iris_immediate_sync"
        private const val FORCE_SCAN_KEY = "force_media_scan"

        fun schedulePeriodic(context: Context, wifiOnly: Boolean = false, requiresCharging: Boolean = false) {
            val networkType = if (wifiOnly) NetworkType.UNMETERED else NetworkType.CONNECTED
            val constraints = Constraints.Builder()
                .setRequiredNetworkType(networkType)
                .setRequiresCharging(requiresCharging)
                .build()

            val request = PeriodicWorkRequestBuilder<MediaSyncWorker>(15, TimeUnit.MINUTES)
                .setConstraints(constraints)
                .build()

            WorkManager.getInstance(context).enqueueUniquePeriodicWork(
                PERIODIC_WORK_TAG,
                ExistingPeriodicWorkPolicy.UPDATE,
                request
            )
        }

        fun enqueueImmediate(context: Context) {
            val constraints = Constraints.Builder()
                .setRequiredNetworkType(NetworkType.CONNECTED)
                .build()

            val request = OneTimeWorkRequestBuilder<MediaSyncWorker>()
                .setConstraints(constraints)
                .setInputData(workDataOf(FORCE_SCAN_KEY to true))
                .build()

            WorkManager.getInstance(context).enqueueUniqueWork(
                ONE_TIME_WORK_TAG,
                ExistingWorkPolicy.REPLACE,
                request
            )
        }

        fun cancelPeriodic(context: Context) {
            WorkManager.getInstance(context).cancelUniqueWork(PERIODIC_WORK_TAG)
        }
    }
}
