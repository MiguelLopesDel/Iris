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
        sharedPreferences.edit()
            .putString(KEY_DEVICE_ID, deviceId)
            .putString(KEY_ACCESS_TOKEN, accessToken)
            .putString(KEY_REFRESH_TOKEN, refreshToken)
            .putLong(KEY_EXPIRY_TIMESTAMP, expiryTimestamp)
            .putString(KEY_USERNAME, username)
            .putString(KEY_SERVER_ORIGIN, serverOrigin)
            .apply()
        _isLoggedIn.value = true
    }

    override fun replaceTokensAtomically(accessToken: String, refreshToken: String, expiresInSeconds: Long) {
        val expiryTimestamp = System.currentTimeMillis() + (expiresInSeconds * 1000L)
        sharedPreferences.edit()
            .putString(KEY_ACCESS_TOKEN, accessToken)
            .putString(KEY_REFRESH_TOKEN, refreshToken)
            .putLong(KEY_EXPIRY_TIMESTAMP, expiryTimestamp)
            .apply()
    }

    override fun getAccessToken(): String? = sharedPreferences.getString(KEY_ACCESS_TOKEN, null)

    override fun getRefreshToken(): String? = sharedPreferences.getString(KEY_REFRESH_TOKEN, null)

    override fun getDeviceId(): String? = sharedPreferences.getString(KEY_DEVICE_ID, null)

    override fun getServerOrigin(): String? = sharedPreferences.getString(KEY_SERVER_ORIGIN, null)

    fun getUsername(): String = sharedPreferences.getString(KEY_USERNAME, "") ?: ""

    override fun isAccessTokenExpired(): Boolean {
        val expiry = sharedPreferences.getLong(KEY_EXPIRY_TIMESTAMP, 0L)
        // Consider expired 30 seconds ahead of actual deadline
        return System.currentTimeMillis() >= (expiry - 30_000L)
    }

    fun hasValidCredentials(): Boolean {
        val token = getAccessToken()
        val deviceId = getDeviceId()
        return !token.isNullOrBlank() && !deviceId.isNullOrBlank()
    }

    override fun clearCredentials() {
        sharedPreferences.edit()
            .remove(KEY_DEVICE_ID)
            .remove(KEY_ACCESS_TOKEN)
            .remove(KEY_REFRESH_TOKEN)
            .remove(KEY_EXPIRY_TIMESTAMP)
            .remove(KEY_USERNAME)
            .remove(KEY_SERVER_ORIGIN)
            .apply()
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
