package com.iris.app.data.sync

import android.content.Context
import android.net.ConnectivityManager
import android.os.BatteryManager
import android.util.Log
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.ExistingWorkPolicy
import androidx.work.ForegroundInfo
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequest
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkInfo
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import androidx.work.await
import androidx.work.workDataOf
import com.iris.app.IrisApplication
import com.iris.app.data.model.MediaScanPolicy
import com.iris.app.data.model.SyncRunOutcome
import com.iris.app.data.model.SyncRunTrigger
import com.iris.app.data.repository.ServerSettingsRepository.AccountSyncSettings
import com.iris.app.performance.Metric
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlin.coroutines.coroutineContext
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
        val isPeriodic = inputData.getBoolean(PERIODIC_SYNC_KEY, false)
        val forceScan = inputData.getBoolean(FORCE_SCAN_KEY, false)
        val recorder = SyncRunRecorder(app.dbHelper, app.syncUploadManager.speedMeter, accountKey)
        recorder.start(
            trigger = when {
                forceScan -> SyncRunTrigger.MANUAL
                isPeriodic -> SyncRunTrigger.PERIODIC
                else -> SyncRunTrigger.AUTOMATIC
            },
            startedInForeground = app.isAppInForeground,
        )
        val checkpoints = CoroutineScope(coroutineContext).launch {
            while (true) {
                delay(RUN_CHECKPOINT_MILLIS)
                recorder.checkpoint()
            }
        }
        var outcome = SyncRunOutcome.RETRY
        var outcomeDetail: String? = null
        var outcomeStopReason: Int? = null

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
                outcomeDetail = "server_unreachable"
                return Result.retry()
            }
            app.settingsRepository.markCloudConnected(accountKey)
            val requeued = app.syncUploadManager.bindServerInstance(accountKey, healthResult.getOrNull()?.instanceId)
            if (requeued > 0) Log.i(TAG, "Server installation changed; requeued=$requeued")

            val canRunMediaWork = !isPeriodic || BackgroundSyncPolicy.shouldRunMediaWork(
                wifiOnly = syncSettings.wifiOnly,
                chargingOnly = syncSettings.chargingOnly,
                networkUnmetered = !isActiveNetworkMetered(applicationContext),
                isCharging = isCharging(applicationContext),
            )

            // 2. Discover new media and drain the durable queue together. This
            // lets the first new or already-pending item upload while the rest
            // of MediaStore is still being scanned and hashed.
            if (!canRunMediaWork) {
                outcomeDetail = when {
                    syncSettings.wifiOnly && isActiveNetworkMetered(applicationContext) -> "wifi_required"
                    syncSettings.chargingOnly && !isCharging(applicationContext) -> "charging_required"
                    else -> "constraints"
                }
            }
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
                    val runningAsService = startForegroundService()
                    recorder.markForeground(runningAsService)
                    val notificationUpdates = if (runningAsService) {
                        CoroutineScope(coroutineContext).launch {
                            while (true) {
                                delay(NOTIFICATION_UPDATE_MILLIS)
                                updateNotification(app, accountKey)
                            }
                        }
                    } else {
                        null
                    }
                    try {
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
                                    // Hashing the whole library is long; do it only on the charger.
                                    allowFullVerification = isCharging(applicationContext),
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
                                    onQueueRunStarted = recorder::queueRunStarted,
                                    onQueueRunFinished = recorder::queueRunFinished,
                                )
                            },
                        ).queueCompleted
                    } finally {
                        notificationUpdates?.cancel()
                    }
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
                outcome = if (canRunMediaWork) SyncRunOutcome.COMPLETED else SyncRunOutcome.SKIPPED
                return Result.success()
            }
            stage = "upload_queue"
            Log.w(TAG, "Background sync retry stage=$stage reason=upload_queue_incomplete")
            app.settingsRepository.markCloudSyncFailed(accountKey)
            outcomeDetail = "upload_incomplete"
            return Result.retry()
        } catch (cancelled: CancellationException) {
            outcome = SyncRunOutcome.STOPPED
            if (isStopped) {
                outcomeStopReason = stopReason
            } else {
                outcomeDetail = "session_changed"
            }
            throw cancelled
        } catch (e: Exception) {
            Log.w(TAG, "Background sync retry stage=$stage error=${e.javaClass.simpleName}")
            outcomeDetail = "error_${stage}_${e.javaClass.simpleName}"
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
        } finally {
            checkpoints.cancel()
            withContext(NonCancellable) {
                runCatching { recorder.finish(outcome, outcomeStopReason, outcomeDetail) }
                    .onFailure { Log.w(TAG, "Sync history not saved error=${it.javaClass.simpleName}") }
            }
        }
    }

    override suspend fun getForegroundInfo(): ForegroundInfo =
        SyncNotifications.foregroundInfo(applicationContext, SyncNotifications.Content.Preparing)

    /**
     * Turns this run into a foreground data-sync service, which Android does
     * not stop after its background time limit. Android 12+ refuses it for an
     * app in the background unless the user exempted it from battery
     * optimization; the run then continues as ordinary background work.
     */
    private suspend fun startForegroundService(): Boolean = try {
        setForeground(getForegroundInfo())
        true
    } catch (cancelled: CancellationException) {
        throw cancelled
    } catch (refused: Exception) {
        Log.w(TAG, "Foreground service refused error=${refused.javaClass.simpleName}")
        false
    }

    private suspend fun updateNotification(app: IrisApplication, accountKey: String) {
        try {
            val remaining = app.dbHelper.remainingUploadBytes(accountKey)
            val speed = app.syncUploadManager.speedMeter.snapshot()
            setForeground(
                SyncNotifications.foregroundInfo(applicationContext, SyncNotifications.content(speed, remaining))
            )
        } catch (cancelled: CancellationException) {
            throw cancelled
        } catch (failure: Exception) {
            Log.w(TAG, "Sync notification not updated error=${failure.javaClass.simpleName}")
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
        private const val RUN_CHECKPOINT_MILLIS = 15_000L
        private const val NOTIFICATION_UPDATE_MILLIS = 2_000L

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

        suspend fun enqueueImmediate(context: Context) {
            val constraints = Constraints.Builder()
                .setRequiredNetworkType(NetworkType.CONNECTED)
                .build()

            val request = OneTimeWorkRequestBuilder<MediaSyncWorker>()
                .setConstraints(constraints)
                .setInputData(workDataOf(FORCE_SCAN_KEY to true))
                .build()

            enqueueUnlessRunning(context, request)
        }

        /** Run this account's pending work after login/account switch, without
         * bypassing the account's automatic-backup opt-in. */
        suspend fun enqueueBackground(
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

            enqueueUnlessRunning(context, request)
        }

        /** See [OneTimeSyncScheduling]: never interrupts a running sync, queues at most one follow-up. */
        private suspend fun enqueueUnlessRunning(context: Context, request: OneTimeWorkRequest) {
            OneTimeSyncScheduling.enqueue(WorkManagerOneTimeQueue(WorkManager.getInstance(context)), request)
        }

        private class WorkManagerOneTimeQueue(
            private val workManager: WorkManager,
        ) : OneTimeSyncQueue<OneTimeWorkRequest> {
            override suspend fun states(): List<WorkInfo.State> =
                workManager.getWorkInfosForUniqueWorkFlow(ONE_TIME_WORK_TAG).first().map { it.state }

            override suspend fun append(request: OneTimeWorkRequest) {
                workManager.enqueueUniqueWork(ONE_TIME_WORK_TAG, ExistingWorkPolicy.APPEND_OR_REPLACE, request).await()
            }

            override suspend fun replace(request: OneTimeWorkRequest) {
                workManager.enqueueUniqueWork(ONE_TIME_WORK_TAG, ExistingWorkPolicy.REPLACE, request).await()
            }
        }

        /**
         * True while a sync that did not finish waits for WorkManager's backoff
         * before running again. The sync button must say so: it starts one at
         * once, while the automatic retry may be minutes away.
         */
        fun retryPending(context: Context): Flow<Boolean> {
            val workManager = WorkManager.getInstance(context)
            return combine(
                workManager.getWorkInfosForUniqueWorkFlow(ONE_TIME_WORK_TAG),
                workManager.getWorkInfosForUniqueWorkFlow(PERIODIC_WORK_TAG),
            ) { oneTime, periodic ->
                (oneTime + periodic).any { it.state == WorkInfo.State.ENQUEUED && it.runAttemptCount > 0 }
            }
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
