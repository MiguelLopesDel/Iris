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
import com.iris.app.IrisApplication
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
            app.mediaStoreScanner.scanAndEnqueueNewMedia()

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
                .build()

            WorkManager.getInstance(context).enqueueUniqueWork(
                ONE_TIME_WORK_TAG,
                ExistingWorkPolicy.REPLACE,
                request
            )
        }
    }
}
