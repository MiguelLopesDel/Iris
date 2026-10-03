package com.iris.app.data.local

import android.content.Context
import android.content.SharedPreferences
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

class DeviceCredentialsStore(context: Context) : DeviceAuthStore {

    private val masterKey = MasterKey.Builder(context)
        .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
        .build()

    private val sharedPreferences: SharedPreferences = EncryptedSharedPreferences.create(
        context,
        "iris_device_keystore_prefs",
        masterKey,
        EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
        EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM
    )

    private val lock = Any()

    private val _isLoggedIn = MutableStateFlow(hasValidCredentials())
    val isLoggedIn: StateFlow<Boolean> = _isLoggedIn.asStateFlow()
    private val _sessionIdentity = MutableStateFlow(readSessionIdentity())
    /** Changes on login, logout, or an account/device switch even if both states are "logged in". */
    val sessionIdentity: StateFlow<String?> = _sessionIdentity.asStateFlow()
    private val _accountIdentity = MutableStateFlow(readAccountIdentity())
    /** Stable across device re-logins; isolates durable work by server account. */
    val accountIdentity: StateFlow<String?> = _accountIdentity.asStateFlow()

    private val _signOutReason = MutableStateFlow<String?>(null)
    /** Why the app signed itself out, for the login screen; null after a normal logout or a new login. */
    val signOutReason: StateFlow<String?> = _signOutReason.asStateFlow()

    /** The server installation this session was made on, once known. */
    fun serverInstanceId(): String? = synchronized(lock) {
        sharedPreferences.getString(KEY_SERVER_INSTANCE, null)
    }

    fun rememberServerInstance(instanceId: String) {
        synchronized(lock) {
            if (readSessionIdentity() != null) {
                sharedPreferences.edit().putString(KEY_SERVER_INSTANCE, instanceId).commit()
            }
        }
    }

    /** Ends a session that belongs to another server installation, saying why. */
    fun signOutBecause(reason: String) {
        clearCredentials()
        _signOutReason.value = reason
    }

    fun saveSession(
        deviceId: String,
        accessToken: String,
        refreshToken: String,
        expiresInSeconds: Long,
        username: String = "",
        serverOrigin: String = "",
        userId: Int? = null
    ) {
        val expiryTimestamp = System.currentTimeMillis() + (expiresInSeconds * 1000L)
        synchronized(lock) {
            sharedPreferences.edit()
                .putString(KEY_DEVICE_ID, deviceId)
                .putString(KEY_ACCESS_TOKEN, accessToken)
                .putString(KEY_REFRESH_TOKEN, refreshToken)
                .putLong(KEY_EXPIRY_TIMESTAMP, expiryTimestamp)
                .putString(KEY_USERNAME, username)
                .putString(KEY_SERVER_ORIGIN, serverOrigin)
                // A new session: its installation is recorded on the next health check.
                .remove(KEY_SERVER_INSTANCE)
                .apply {
                    if (userId != null && userId > 0) putInt(KEY_USER_ID, userId)
                    else remove(KEY_USER_ID)
                }
                .commit()
        }
        _accountIdentity.value = readAccountIdentity()
        _sessionIdentity.value = readSessionIdentity()
        _isLoggedIn.value = true
        _signOutReason.value = null
    }

    override fun replaceTokensAtomically(accessToken: String, refreshToken: String, expiresInSeconds: Long) {
        val expiryTimestamp = System.currentTimeMillis() + (expiresInSeconds * 1000L)
        synchronized(lock) {
            sharedPreferences.edit()
                .putString(KEY_ACCESS_TOKEN, accessToken)
                .putString(KEY_REFRESH_TOKEN, refreshToken)
                .putLong(KEY_EXPIRY_TIMESTAMP, expiryTimestamp)
                .commit()
        }
    }

    override fun getAccessToken(): String? = synchronized(lock) {
        sharedPreferences.getString(KEY_ACCESS_TOKEN, null)
    }

    override fun getRefreshToken(): String? = synchronized(lock) {
        sharedPreferences.getString(KEY_REFRESH_TOKEN, null)
    }

    override fun getDeviceId(): String? = synchronized(lock) {
        sharedPreferences.getString(KEY_DEVICE_ID, null)
    }

    override fun getServerOrigin(): String? = synchronized(lock) {
        sharedPreferences.getString(KEY_SERVER_ORIGIN, null)
    }

    override fun getSessionIdentity(): String? = readSessionIdentity()

    override fun getSessionCredentials(expectedIdentity: String): DeviceSessionCredentials? = synchronized(lock) {
        if (readSessionIdentityLocked() != expectedIdentity) return@synchronized null
        val token = sharedPreferences.getString(KEY_ACCESS_TOKEN, null)?.takeIf(String::isNotBlank)
            ?: return@synchronized null
        val deviceId = sharedPreferences.getString(KEY_DEVICE_ID, null)?.takeIf(String::isNotBlank)
            ?: return@synchronized null
        DeviceSessionCredentials(
            accessToken = token,
            refreshToken = sharedPreferences.getString(KEY_REFRESH_TOKEN, null),
            deviceId = deviceId,
            serverOrigin = sharedPreferences.getString(KEY_SERVER_ORIGIN, null)
        )
    }

    override fun replaceTokensAtomicallyForSession(
        expectedIdentity: String,
        accessToken: String,
        refreshToken: String,
        expiresInSeconds: Long
    ): Boolean = synchronized(lock) {
        if (readSessionIdentityLocked() != expectedIdentity) return@synchronized false
        val expiryTimestamp = System.currentTimeMillis() + (expiresInSeconds * 1000L)
        sharedPreferences.edit()
            .putString(KEY_ACCESS_TOKEN, accessToken)
            .putString(KEY_REFRESH_TOKEN, refreshToken)
            .putLong(KEY_EXPIRY_TIMESTAMP, expiryTimestamp)
            .commit()
    }

    override fun clearCredentialsIfSession(expectedIdentity: String): Boolean = synchronized(lock) {
        if (readSessionIdentityLocked() != expectedIdentity) return@synchronized false
        clearCredentialsLocked()
        true
    }

    fun getUsername(): String = synchronized(lock) {
        sharedPreferences.getString(KEY_USERNAME, "") ?: ""
    }

    override fun isAccessTokenExpired(): Boolean = synchronized(lock) {
        val expiry = sharedPreferences.getLong(KEY_EXPIRY_TIMESTAMP, 0L)
        // Consider expired 30 seconds ahead of actual deadline
        System.currentTimeMillis() >= (expiry - 30_000L)
    }

    fun hasValidCredentials(): Boolean = synchronized(lock) {
        val token = sharedPreferences.getString(KEY_ACCESS_TOKEN, null)
        val deviceId = sharedPreferences.getString(KEY_DEVICE_ID, null)
        !token.isNullOrBlank() && !deviceId.isNullOrBlank()
    }

    private fun readSessionIdentity(): String? = synchronized(lock) { readSessionIdentityLocked() }

    private fun readSessionIdentityLocked(): String? {
        val token = sharedPreferences.getString(KEY_ACCESS_TOKEN, null)
        val deviceId = sharedPreferences.getString(KEY_DEVICE_ID, null)
        if (token.isNullOrBlank() || deviceId.isNullOrBlank()) return null
        return listOf(
            sharedPreferences.getString(KEY_SERVER_ORIGIN, "").orEmpty(),
            sharedPreferences.getString(KEY_USERNAME, "").orEmpty(),
            sharedPreferences.getInt(KEY_USER_ID, 0).toString(),
            deviceId,
        ).joinToString("|")
    }

    private fun readAccountIdentity(): String? = synchronized(lock) {
        if (sharedPreferences.getString(KEY_ACCESS_TOKEN, null).isNullOrBlank()) return@synchronized null
        val origin = sharedPreferences.getString(KEY_SERVER_ORIGIN, null)?.trim()?.takeIf(String::isNotBlank)
            ?: return@synchronized null
        val userId = sharedPreferences.getInt(KEY_USER_ID, 0)
        if (userId > 0) return@synchronized "$origin|user:$userId"
        val username = sharedPreferences.getString(KEY_USERNAME, null)?.trim()?.lowercase()
            ?.takeIf(String::isNotBlank) ?: return@synchronized null
        val encodedUsername = java.util.Base64.getUrlEncoder().withoutPadding()
            .encodeToString(username.toByteArray(Charsets.UTF_8))
        "$origin|username:$encodedUsername"
    }

    override fun clearCredentials() {
        synchronized(lock) {
            clearCredentialsLocked()
        }
        _sessionIdentity.value = null
        _accountIdentity.value = null
        _isLoggedIn.value = false
    }

    private fun clearCredentialsLocked() {
        sharedPreferences.edit()
            .remove(KEY_DEVICE_ID)
            .remove(KEY_ACCESS_TOKEN)
            .remove(KEY_REFRESH_TOKEN)
            .remove(KEY_EXPIRY_TIMESTAMP)
            .remove(KEY_USERNAME)
            .remove(KEY_SERVER_ORIGIN)
            .remove(KEY_USER_ID)
            .remove(KEY_SERVER_INSTANCE)
            .commit()
        _sessionIdentity.value = null
        _accountIdentity.value = null
        _isLoggedIn.value = false
    }

    companion object {
        private const val KEY_DEVICE_ID = "enc_device_id"
        private const val KEY_ACCESS_TOKEN = "enc_access_token"
        private const val KEY_REFRESH_TOKEN = "enc_refresh_token"
        private const val KEY_EXPIRY_TIMESTAMP = "enc_expiry_timestamp"
        private const val KEY_USERNAME = "enc_username"
        private const val KEY_SERVER_ORIGIN = "enc_server_origin"
        private const val KEY_USER_ID = "enc_user_id"
        private const val KEY_SERVER_INSTANCE = "enc_server_instance_id"
    }
}
