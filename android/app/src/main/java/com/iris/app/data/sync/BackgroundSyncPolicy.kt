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
}
