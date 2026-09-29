package com.iris.app.data.model

enum class CloudConnectionState {
    UNKNOWN,
    CHECKING,
    CONNECTED,
    OFFLINE,
}

/** Account-scoped connection information; never includes a URL or credential. */
data class CloudSyncStatus(
    val connectionState: CloudConnectionState = CloudConnectionState.UNKNOWN,
    val lastCheckedAtMillis: Long? = null,
    val lastSuccessfulSyncAtMillis: Long? = null,
    val syncError: String? = null,
)
