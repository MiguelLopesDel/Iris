package com.iris.app.data.sync

/** Decides whether a signed-in account needs periodic server synchronization. */
internal object BackgroundSyncPolicy {
    fun shouldSchedulePeriodicSync(isLoggedIn: Boolean): Boolean = isLoggedIn

    /** Periodic checks are cheap; local scans/uploads still honor user limits. */
    fun shouldRunMediaWork(
        wifiOnly: Boolean,
        chargingOnly: Boolean,
        networkUnmetered: Boolean,
        isCharging: Boolean,
    ): Boolean = (!wifiOnly || networkUnmetered) && (!chargingOnly || isCharging)

    /**
     * Draining already-persisted uploads is media work too: background session
     * startup must not upload queued files unless backup is opted in. A manual
     * sync is an explicit one-shot request and may process the queue regardless.
     */
    fun shouldProcessMediaQueue(
        allowedByConstraints: Boolean,
        autoBackupEnabled: Boolean,
        forceScan: Boolean,
    ): Boolean = allowedByConstraints && (autoBackupEnabled || forceScan)

    /** A skipped queue is not evidence that queued retries have been completed. */
    fun shouldClearObsoleteRetry(queueWasProcessed: Boolean, queueCompleted: Boolean): Boolean =
        queueWasProcessed && queueCompleted
}
