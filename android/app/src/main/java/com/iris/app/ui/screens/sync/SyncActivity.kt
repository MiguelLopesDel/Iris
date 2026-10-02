package com.iris.app.ui.screens.sync

/**
 * What the sync is doing, as the status line and the sync button show it.
 *
 * Scanning comes first: a first sync spends minutes hashing new media, the
 * queue grows meanwhile, and without saying so the screen looked stuck.
 */
internal enum class SyncActivity {
    SCANNING,
    UPLOADING,
    RETRY_PENDING,
    IDLE;

    /** A run is in progress, so the button would only queue a follow-up. */
    val isRunning: Boolean get() = this == SCANNING || this == UPLOADING

    companion object {
        fun of(state: SyncUiState): SyncActivity = when {
            state.scanProgress != null -> SCANNING
            state.isSyncing -> UPLOADING
            state.retryPending -> RETRY_PENDING
            else -> IDLE
        }
    }
}
