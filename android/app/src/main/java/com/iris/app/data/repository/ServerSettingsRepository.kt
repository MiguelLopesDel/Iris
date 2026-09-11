package com.iris.app.data.repository

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.floatPreferencesKey
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.core.stringSetPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map

private val Context.dataStore: DataStore<Preferences> by preferencesDataStore(name = "iris_settings")

class ServerSettingsRepository(private val context: Context) {

    private object PreferencesKeys {
        val SERVER_URL = stringPreferencesKey("server_url")
        val THEME_MODE = stringPreferencesKey("theme_mode")
        val SEARCH_BALANCE = floatPreferencesKey("search_balance")
        val SEARCH_THRESHOLD = floatPreferencesKey("search_threshold")
        val TEXT_BONUS = floatPreferencesKey("text_bonus")
        val SYNC_WIFI_ONLY = booleanPreferencesKey("sync_wifi_only")
        val SYNC_CHARGING_ONLY = booleanPreferencesKey("sync_charging_only")
        val AUTO_BACKUP_ENABLED = booleanPreferencesKey("auto_backup_enabled")
        val SYNC_SOURCE_MODE = stringPreferencesKey("sync_source_mode")
        val SYNC_SELECTED_SOURCE_IDS = stringSetPreferencesKey("sync_selected_source_ids")
        val SYNC_IMAGES_ENABLED = booleanPreferencesKey("sync_images_enabled")
        val SYNC_VIDEOS_ENABLED = booleanPreferencesKey("sync_videos_enabled")
    }

    val serverUrl: Flow<String> = context.dataStore.data.map { preferences ->
        preferences[PreferencesKeys.SERVER_URL] ?: DEFAULT_SERVER_URL
    }

    val themeMode: Flow<String> = context.dataStore.data.map { preferences ->
        preferences[PreferencesKeys.THEME_MODE] ?: "system"
    }

    val searchBalance: Flow<Float> = context.dataStore.data.map { preferences ->
        preferences[PreferencesKeys.SEARCH_BALANCE] ?: 0.5f
    }

    val searchThreshold: Flow<Float> = context.dataStore.data.map { preferences ->
        preferences[PreferencesKeys.SEARCH_THRESHOLD] ?: 0.15f
    }

    val textBonus: Flow<Float> = context.dataStore.data.map { preferences ->
        preferences[PreferencesKeys.TEXT_BONUS] ?: 1.0f
    }

    val syncWifiOnly: Flow<Boolean> = context.dataStore.data.map { preferences ->
        preferences[PreferencesKeys.SYNC_WIFI_ONLY] ?: false
    }

    val syncChargingOnly: Flow<Boolean> = context.dataStore.data.map { preferences ->
        preferences[PreferencesKeys.SYNC_CHARGING_ONLY] ?: false
    }

    val autoBackupEnabled: Flow<Boolean> = context.dataStore.data.map { preferences ->
        preferences[PreferencesKeys.AUTO_BACKUP_ENABLED] ?: false
    }

    val syncSourceMode: Flow<String> = context.dataStore.data.map { preferences ->
        preferences[PreferencesKeys.SYNC_SOURCE_MODE] ?: "selected"
    }

    val syncSelectedSourceIds: Flow<Set<String>> = context.dataStore.data.map { preferences ->
        preferences[PreferencesKeys.SYNC_SELECTED_SOURCE_IDS] ?: emptySet()
    }

    val syncImagesEnabled: Flow<Boolean> = context.dataStore.data.map { preferences ->
        preferences[PreferencesKeys.SYNC_IMAGES_ENABLED] ?: true
    }

    val syncVideosEnabled: Flow<Boolean> = context.dataStore.data.map { preferences ->
        preferences[PreferencesKeys.SYNC_VIDEOS_ENABLED] ?: true
    }

    suspend fun updateServerUrl(url: String) {
        context.dataStore.edit { preferences ->
            preferences[PreferencesKeys.SERVER_URL] = url.trim()
        }
    }

    suspend fun updateThemeMode(mode: String) {
        context.dataStore.edit { preferences ->
            preferences[PreferencesKeys.THEME_MODE] = mode
        }
    }

    suspend fun updateSearchBalance(balance: Float) {
        context.dataStore.edit { preferences ->
            preferences[PreferencesKeys.SEARCH_BALANCE] = balance
        }
    }

    suspend fun updateSyncWifiOnly(wifiOnly: Boolean) {
        context.dataStore.edit { preferences ->
            preferences[PreferencesKeys.SYNC_WIFI_ONLY] = wifiOnly
        }
    }

    suspend fun updateSyncChargingOnly(chargingOnly: Boolean) {
        context.dataStore.edit { preferences ->
            preferences[PreferencesKeys.SYNC_CHARGING_ONLY] = chargingOnly
        }
    }

    suspend fun updateAutoBackupEnabled(enabled: Boolean) {
        context.dataStore.edit { preferences ->
            preferences[PreferencesKeys.AUTO_BACKUP_ENABLED] = enabled
        }
    }

    suspend fun updateSyncSourceMode(mode: String) {
        require(mode == "all" || mode == "selected")
        context.dataStore.edit { it[PreferencesKeys.SYNC_SOURCE_MODE] = mode }
    }

    suspend fun updateSelectedSourceIds(sourceIds: Set<String>) {
        context.dataStore.edit { it[PreferencesKeys.SYNC_SELECTED_SOURCE_IDS] = sourceIds }
    }

    suspend fun updateSyncImagesEnabled(enabled: Boolean) {
        context.dataStore.edit { it[PreferencesKeys.SYNC_IMAGES_ENABLED] = enabled }
    }

    suspend fun updateSyncVideosEnabled(enabled: Boolean) {
        context.dataStore.edit { it[PreferencesKeys.SYNC_VIDEOS_ENABLED] = enabled }
    }

    companion object {
        const val DEFAULT_SERVER_URL = "http://10.0.2.2:8000/"
    }
}
