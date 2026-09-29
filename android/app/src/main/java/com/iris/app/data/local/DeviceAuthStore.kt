package com.iris.app.data.local

/**
 * Minimal credential boundary used by network clients.
 *
 * Keeping this independent of Android storage makes authentication behavior
 * testable on the JVM without a phone, emulator, or encrypted preferences.
 */
interface DeviceAuthStore {
    fun getAccessToken(): String?
    fun getRefreshToken(): String?
    fun getDeviceId(): String?
    fun getServerOrigin(): String? = null
    fun getSessionIdentity(): String? = null
    fun getSessionCredentials(expectedIdentity: String): DeviceSessionCredentials? {
        if (getSessionIdentity() != expectedIdentity) return null
        val token = getAccessToken()?.takeIf(String::isNotBlank) ?: return null
        val deviceId = getDeviceId()?.takeIf(String::isNotBlank) ?: return null
        return DeviceSessionCredentials(token, getRefreshToken(), deviceId, getServerOrigin())
    }
    fun isAccessTokenExpired(): Boolean = false
    fun replaceTokensAtomically(accessToken: String, refreshToken: String, expiresInSeconds: Long)
    fun replaceTokensAtomicallyForSession(
        expectedIdentity: String,
        accessToken: String,
        refreshToken: String,
        expiresInSeconds: Long
    ): Boolean {
        if (getSessionIdentity() != expectedIdentity) return false
        replaceTokensAtomically(accessToken, refreshToken, expiresInSeconds)
        return true
    }
    fun clearCredentialsIfSession(expectedIdentity: String): Boolean {
        if (getSessionIdentity() != expectedIdentity) return false
        clearCredentials()
        return true
    }
    fun clearCredentials()
}

data class DeviceSessionCredentials(
    val accessToken: String,
    val refreshToken: String?,
    val deviceId: String,
    val serverOrigin: String?
)
