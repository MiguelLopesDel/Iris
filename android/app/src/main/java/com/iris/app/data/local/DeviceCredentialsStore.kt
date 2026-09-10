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

    fun saveSession(
        deviceId: String,
        accessToken: String,
        refreshToken: String,
        expiresInSeconds: Long,
        username: String = "",
        serverOrigin: String = ""
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
                .commit()
        }
        _isLoggedIn.value = true
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

    override fun clearCredentials() {
        synchronized(lock) {
            sharedPreferences.edit()
                .remove(KEY_DEVICE_ID)
                .remove(KEY_ACCESS_TOKEN)
                .remove(KEY_REFRESH_TOKEN)
                .remove(KEY_EXPIRY_TIMESTAMP)
                .remove(KEY_USERNAME)
                .remove(KEY_SERVER_ORIGIN)
                .commit()
        }
        _isLoggedIn.value = false
    }

    companion object {
        private const val KEY_DEVICE_ID = "enc_device_id"
        private const val KEY_ACCESS_TOKEN = "enc_access_token"
        private const val KEY_REFRESH_TOKEN = "enc_refresh_token"
        private const val KEY_EXPIRY_TIMESTAMP = "enc_expiry_timestamp"
        private const val KEY_USERNAME = "enc_username"
        private const val KEY_SERVER_ORIGIN = "enc_server_origin"
    }
}
