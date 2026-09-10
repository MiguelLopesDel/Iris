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
    fun isAccessTokenExpired(): Boolean = false
    fun replaceTokensAtomically(accessToken: String, refreshToken: String, expiresInSeconds: Long)
    fun clearCredentials()
}
