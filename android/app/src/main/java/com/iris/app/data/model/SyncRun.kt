package com.iris.app.data.model

/**
 * One background sync execution, persisted so it survives the process: the
 * only way to see afterwards whether media kept uploading with the app closed.
 */
data class SyncRun(
    val id: Long,
    val startedAtMillis: Long,
    val endedAtMillis: Long?,
    val trigger: SyncRunTrigger,
    val startedInForeground: Boolean,
    val bytes: Long,
    val items: Long,
    val uploadMillis: Long,
    val outcome: SyncRunOutcome,
    /** WorkManager stop reason (WorkInfo.STOP_REASON_*) when the system stopped the run. */
    val stopReason: Int?,
    /** Short machine code explaining a retry or failure; never a message with user data. */
    val detail: String?,
    /** True when the run became a foreground service, false when Android refused, null when not attempted. */
    val foregroundService: Boolean? = null,
)

enum class SyncRunTrigger { MANUAL, AUTOMATIC, PERIODIC }

enum class SyncRunOutcome {
    /** Started and not finished yet; if it is not the active run, the process died. */
    RUNNING,
    COMPLETED,
    /** Ended early and asked WorkManager to try again later. */
    RETRY,
    /** Stopped by the system or by a newer run replacing it. */
    STOPPED,
    /** Skipped the upload queue because of the account's Wi-Fi/charging preferences. */
    SKIPPED,
}
