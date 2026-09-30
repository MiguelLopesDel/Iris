package com.iris.app.data.sync

import android.content.Context
import android.net.ConnectivityManager
import android.os.BatteryManager
import android.util.Log
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
import com.iris.app.data.repository.ServerSettingsRepository.AccountSyncSettings
import com.iris.app.performance.Metric
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.CancellationException
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.TimeUnit

class MediaSyncWorker(
    context: Context,
    params: WorkerParameters
) : CoroutineWorker(context, params) {

    override suspend fun doWork(): Result {
        val app = applicationContext as? IrisApplication ?: return Result.failure()

        // 1. Verify valid device credentials
        val sessionIdentity = app.credentialsStore.sessionIdentity.value
        val accountKey = app.credentialsStore.accountIdentity.value
        if (sessionIdentity == null || accountKey == null || !app.credentialsStore.hasValidCredentials()) {
            return Result.success() // Not logged in; nothing to sync
        }
        val syncSession = AccountSyncSession(sessionIdentity, accountKey)

        val syncStartedAtNanos = System.nanoTime()
        val firstUploadMetricRecorded = AtomicBoolean(false)
        val onFirstUploadJobClaimed = {
            if (firstUploadMetricRecorded.compareAndSet(false, true)) {
                app.performanceMonitor.record(
                    Metric.SyncFirstUploadJobStart,
                    (System.nanoTime() - syncStartedAtNanos) / 1_000_000.0
                )
            }
        }
        var stage = "load_settings"
        try {
            ensureSession(app, sessionIdentity, accountKey)
            val syncSettings = app.settingsRepository.syncSettingsForAccount(accountKey).first()

            // A periodic run also probes the cloud even when media backup is
            // opted out. This is intentionally account-scoped and stores no
            // URL, token, or other credential in the UI status.
            stage = "server_health"
            app.settingsRepository.markCloudSyncChecking(accountKey)
            val healthResult = app.irisRepository.checkServerHealth()
            ensureSession(app, sessionIdentity, accountKey)
            if (healthResult.isFailure) {
                Log.w(TAG, "Background sync retry stage=$stage error=${healthResult.exceptionOrNull()?.javaClass?.simpleName ?: "Unknown"}")
                app.settingsRepository.markCloudUnavailable(accountKey)
                return Result.retry()
            }
            app.settingsRepository.markCloudConnected(accountKey)

            val isPeriodic = inputData.getBoolean(PERIODIC_SYNC_KEY, false)
            val canRunMediaWork = !isPeriodic || BackgroundSyncPolicy.shouldRunMediaWork(
                wifiOnly = syncSettings.wifiOnly,
                chargingOnly = syncSettings.chargingOnly,
                networkUnmetered = !isActiveNetworkMetered(applicationContext),
                isCharging = isCharging(applicationContext),
            )

            // 2. Discover new media and drain the durable queue together. This
            // lets the first new or already-pending item upload while the rest
            // of MediaStore is still being scanned and hashed.
            val forceScan = inputData.getBoolean(FORCE_SCAN_KEY, false)
            val shouldProcessMediaQueue = BackgroundSyncPolicy.shouldProcessMediaQueue(
                allowedByConstraints = canRunMediaWork,
                autoBackupEnabled = syncSettings.autoBackupEnabled,
                forceScan = forceScan,
            )
            val queueCompleted = when {
                !canRunMediaWork -> true
                shouldProcessMediaQueue -> {
                    stage = "media_scan_and_upload"
                    val policy = MediaScanPolicy(
                        mode = syncSettings.sourceMode,
                        selectedSourceIds = syncSettings.selectedSourceIds,
                        includeImages = syncSettings.imagesEnabled,
                        includeVideos = syncSettings.videosEnabled
                    )
                    SyncQueueCoordinator.scanAndDrain(
                        scanAndEnqueue = { onNewJobEnqueued ->
                            app.mediaStoreScanner.scanAndEnqueueNewMedia(
                                accountKey = accountKey,
                                policy = policy,
                                isSessionCurrent = {
                                    !isStopped && syncSession.matches(
                                        app.credentialsStore.sessionIdentity.value,
                                        app.credentialsStore.accountIdentity.value
                                    )
                                },
                                onNewJobEnqueued = onNewJobEnqueued,
                            )
                        },
                        drainQueue = { workSignal ->
                            app.syncUploadManager.processQueue(
                                accountKey,
                                sessionIdentity,
                                isSessionCurrent = {
                                    !isStopped && syncSession.matches(
                                        app.credentialsStore.sessionIdentity.value,
                                        app.credentialsStore.accountIdentity.value
                                    )
                                },
                                onFirstUploadJobClaimed = onFirstUploadJobClaimed,
                                workSignal = workSignal,
                            )
                        },
                    ).queueCompleted
                }
                // Checking server health/change feed is independent from photo
                // backup. Do not drain durable uploads in the background after
                // the user has opted out; manual sync sets FORCE_SCAN_KEY.
                else -> true
            }

            ensureSession(app, sessionIdentity, accountKey)
            // 3. Poll change feed after upload completion
            stage = "change_feed"
            app.changeFeedSyncManager.syncChanges(accountKey, sessionIdentity) {
                !isStopped && syncSession.matches(
                    app.credentialsStore.sessionIdentity.value,
                    app.credentialsStore.accountIdentity.value
                )
            }

            if (queueCompleted) {
                app.settingsRepository.markCloudSyncSucceeded(accountKey)
                return Result.success()
            }
            stage = "upload_queue"
            Log.w(TAG, "Background sync retry stage=$stage reason=upload_queue_incomplete")
            app.settingsRepository.markCloudSyncFailed(accountKey)
            return Result.retry()
        } catch (cancelled: CancellationException) {
            throw cancelled
        } catch (e: Exception) {
            Log.w(TAG, "Background sync retry stage=$stage error=${e.javaClass.simpleName}")
            // A local file/queue failure is not proof the server is offline.
            // Re-probe to distinguish it from a host that went away mid-sync.
            if (syncSession.matches(
                    app.credentialsStore.sessionIdentity.value,
                    app.credentialsStore.accountIdentity.value
                )
            ) {
                val reachable = app.irisRepository.checkServerHealth().isSuccess
                if (reachable) app.settingsRepository.markCloudSyncFailed(accountKey)
                else app.settingsRepository.markCloudUnavailable(accountKey)
            }
            return Result.retry()
        }
    }

    private fun isActiveNetworkMetered(context: Context): Boolean =
        context.getSystemService(ConnectivityManager::class.java)?.isActiveNetworkMetered ?: true

    private fun isCharging(context: Context): Boolean =
        context.getSystemService(BatteryManager::class.java)?.isCharging == true

    private fun ensureSession(app: IrisApplication, expectedIdentity: String, expectedAccountKey: String) {
        val expectedSession = AccountSyncSession(expectedIdentity, expectedAccountKey)
        if (isStopped || !expectedSession.matches(
                app.credentialsStore.sessionIdentity.value,
                app.credentialsStore.accountIdentity.value
            )
        ) {
            throw CancellationException("Device session changed during background sync")
        }
    }

    companion object {
        private const val TAG = "MediaSyncWorker"
        private const val PERIODIC_WORK_TAG = "iris_periodic_sync"
        private const val ONE_TIME_WORK_TAG = "iris_immediate_sync"
        private const val FORCE_SCAN_KEY = "force_media_scan"
        private const val PERIODIC_SYNC_KEY = "periodic_cloud_sync"

        fun schedulePeriodic(context: Context) {
            val constraints = Constraints.Builder()
                .setRequiredNetworkType(NetworkType.CONNECTED)
                .build()

            val request = PeriodicWorkRequestBuilder<MediaSyncWorker>(15, TimeUnit.MINUTES)
                .setConstraints(constraints)
                .setInputData(workDataOf(PERIODIC_SYNC_KEY to true))
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

        /** Run this account's pending work after login/account switch, without
         * bypassing the account's automatic-backup opt-in. */
        fun enqueueBackground(
            context: Context,
            syncSettings: AccountSyncSettings
        ) {
            val networkType = if (syncSettings.wifiOnly) NetworkType.UNMETERED else NetworkType.CONNECTED
            val constraints = Constraints.Builder()
                .setRequiredNetworkType(networkType)
                .setRequiresCharging(syncSettings.chargingOnly)
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

        fun cancelPeriodic(context: Context) {
            WorkManager.getInstance(context).cancelUniqueWork(PERIODIC_WORK_TAG)
        }

        fun cancelImmediate(context: Context) {
            WorkManager.getInstance(context).cancelUniqueWork(ONE_TIME_WORK_TAG)
        }

        fun cancelAll(context: Context) {
            cancelPeriodic(context)
            cancelImmediate(context)
        }
    }
}
