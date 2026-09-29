package com.iris.app.data.sync

/** Immutable identity captured by one sync run; both keys must still match. */
data class AccountSyncSession(
    val sessionIdentity: String,
    val accountKey: String,
) {
    init {
        require(sessionIdentity.isNotBlank()) { "A session identity is required" }
        require(accountKey.isNotBlank()) { "An account identity is required" }
    }

    fun matches(currentSessionIdentity: String?, currentAccountKey: String?): Boolean =
        sessionIdentity == currentSessionIdentity && accountKey == currentAccountKey
}
