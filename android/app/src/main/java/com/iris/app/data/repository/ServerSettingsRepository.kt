package com.iris.app.data.repository

import android.content.Context
import com.iris.app.BuildConfig
import com.iris.app.data.model.CloudConnectionState
import com.iris.app.data.model.CloudSyncStatus
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.floatPreferencesKey
import androidx.datastore.preferences.core.longPreferencesKey
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.core.stringSetPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.emitAll
import kotlinx.coroutines.flow.flatMapLatest
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.flow.map
import java.security.MessageDigest

private val Context.dataStore: DataStore<Preferences> by preferencesDataStore(name = "iris_settings")

class ServerSettingsRepository(private val context: Context) {

    data class AccountSyncSettings(
        val wifiOnly: Boolean = false,
        val chargingOnly: Boolean = false,
        val autoBackupEnabled: Boolean = false,
        val backupSetupPromptAnswered: Boolean = false,
        val backupSetupPending: Boolean = false,
        val sourceMode: String = "selected",
        val selectedSourceIds: Set<String> = emptySet(),
        val imagesEnabled: Boolean = true,
        val videosEnabled: Boolean = true
    )

    private object PreferencesKeys {
        val SERVER_URL = stringPreferencesKey("server_url")
        val THEME_MODE = stringPreferencesKey("theme_mode")
        val SEARCH_BALANCE = floatPreferencesKey("search_balance")
        val SEARCH_THRESHOLD = floatPreferencesKey("search_threshold")
        val TEXT_BONUS = floatPreferencesKey("text_bonus")
        // Legacy device-wide settings had no attributable account owner. They
        // are retired once instead of being copied to whichever account logs in.
        val LEGACY_SYNC_WIFI_ONLY = booleanPreferencesKey("sync_wifi_only")
        val LEGACY_SYNC_CHARGING_ONLY = booleanPreferencesKey("sync_charging_only")
        val LEGACY_AUTO_BACKUP_ENABLED = booleanPreferencesKey("auto_backup_enabled")
        val LEGACY_SYNC_SOURCE_MODE = stringPreferencesKey("sync_source_mode")
        val LEGACY_SYNC_SELECTED_SOURCE_IDS = stringSetPreferencesKey("sync_selected_source_ids")
        val LEGACY_SYNC_IMAGES_ENABLED = booleanPreferencesKey("sync_images_enabled")
        val LEGACY_SYNC_VIDEOS_ENABLED = booleanPreferencesKey("sync_videos_enabled")
        val LEGACY_SYNC_SETTINGS_MIGRATED = booleanPreferencesKey("sync_settings_account_migration_v1")
    }

    private data class AccountPreferenceKeys(
        val wifiOnly: androidx.datastore.preferences.core.Preferences.Key<Boolean>,
        val chargingOnly: androidx.datastore.preferences.core.Preferences.Key<Boolean>,
        val autoBackup: androidx.datastore.preferences.core.Preferences.Key<Boolean>,
        val backupSetupPromptAnswered: androidx.datastore.preferences.core.Preferences.Key<Boolean>,
        val backupSetupPending: androidx.datastore.preferences.core.Preferences.Key<Boolean>,
        val sourceMode: androidx.datastore.preferences.core.Preferences.Key<String>,
        val selectedSourceIds: androidx.datastore.preferences.core.Preferences.Key<Set<String>>,
        val imagesEnabled: androidx.datastore.preferences.core.Preferences.Key<Boolean>,
        val videosEnabled: androidx.datastore.preferences.core.Preferences.Key<Boolean>
    )

    private data class AccountCloudStatusKeys(
        val state: androidx.datastore.preferences.core.Preferences.Key<String>,
        val lastChecked: androidx.datastore.preferences.core.Preferences.Key<Long>,
        val lastSuccessfulSync: androidx.datastore.preferences.core.Preferences.Key<Long>,
        val syncError: androidx.datastore.preferences.core.Preferences.Key<String>,
    )

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

    fun syncSettingsForAccount(accountKey: String?): Flow<AccountSyncSettings> {
        if (accountKey.isNullOrBlank()) return flowOf(AccountSyncSettings())
        val keys = accountKeys(accountKey)
        return flow {
            retireLegacyUnscopedSyncSettings()
            emitAll(context.dataStore.data.map { preferences ->
                AccountSyncSettings(
                    wifiOnly = preferences[keys.wifiOnly] ?: false,
                    chargingOnly = preferences[keys.chargingOnly] ?: false,
                    autoBackupEnabled = preferences[keys.autoBackup] ?: false,
                    backupSetupPromptAnswered = preferences[keys.backupSetupPromptAnswered] ?: false,
                    backupSetupPending = preferences[keys.backupSetupPending] ?: false,
                    sourceMode = preferences[keys.sourceMode] ?: "selected",
                    selectedSourceIds = preferences[keys.selectedSourceIds] ?: emptySet(),
                    imagesEnabled = preferences[keys.imagesEnabled] ?: true,
                    videosEnabled = preferences[keys.videosEnabled] ?: true
                )
            })
        }
    }

    @OptIn(ExperimentalCoroutinesApi::class)
    fun activeSyncSettings(accountKeys: Flow<String?>): Flow<AccountSyncSettings> =
        accountKeys.flatMapLatest(::syncSettingsForAccount)

    fun cloudSyncStatusForAccount(accountKey: String?): Flow<CloudSyncStatus> {
        if (accountKey.isNullOrBlank()) return flowOf(CloudSyncStatus())
        val keys = cloudStatusKeys(accountKey)
        return context.dataStore.data.map { preferences ->
            CloudSyncStatus(
                connectionState = preferences[keys.state]
                    ?.let { runCatching { CloudConnectionState.valueOf(it) }.getOrNull() }
                    ?: CloudConnectionState.UNKNOWN,
                lastCheckedAtMillis = preferences[keys.lastChecked],
                lastSuccessfulSyncAtMillis = preferences[keys.lastSuccessfulSync],
                syncError = preferences[keys.syncError],
            )
        }
    }

    suspend fun markCloudSyncChecking(accountKey: String) {
        val keys = cloudStatusKeys(accountKey)
        context.dataStore.edit { it[keys.state] = CloudConnectionState.CHECKING.name }
    }

    suspend fun markCloudUnavailable(accountKey: String) {
        val keys = cloudStatusKeys(accountKey)
        context.dataStore.edit { preferences ->
            preferences[keys.state] = CloudConnectionState.OFFLINE.name
            preferences[keys.lastChecked] = System.currentTimeMillis()
            preferences.remove(keys.syncError)
        }
    }

    suspend fun markCloudConnected(accountKey: String) {
        val keys = cloudStatusKeys(accountKey)
        context.dataStore.edit { preferences ->
            preferences[keys.state] = CloudConnectionState.CONNECTED.name
            preferences[keys.lastChecked] = System.currentTimeMillis()
            preferences.remove(keys.syncError)
        }
    }

    suspend fun markCloudSyncSucceeded(accountKey: String) {
        val keys = cloudStatusKeys(accountKey)
        context.dataStore.edit { preferences ->
            preferences[keys.state] = CloudConnectionState.CONNECTED.name
            preferences[keys.lastChecked] = System.currentTimeMillis()
            preferences[keys.lastSuccessfulSync] = System.currentTimeMillis()
            preferences.remove(keys.syncError)
        }
    }

    suspend fun markCloudSyncFailed(accountKey: String) {
        val keys = cloudStatusKeys(accountKey)
        context.dataStore.edit { preferences ->
            // The health probe succeeded. Keep reachability distinct from an
            // upload/file/auth error so the UI does not mislabel the server.
            preferences[keys.state] = CloudConnectionState.CONNECTED.name
            preferences[keys.syncError] = "A conexão está ativa, mas a sincronização não terminou."
        }
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

    suspend fun updateSyncWifiOnly(accountKey: String, wifiOnly: Boolean) {
        context.dataStore.edit { preferences ->
            preferences[accountKeys(accountKey).wifiOnly] = wifiOnly
        }
    }

    suspend fun updateSyncChargingOnly(accountKey: String, chargingOnly: Boolean) {
        context.dataStore.edit { preferences ->
            preferences[accountKeys(accountKey).chargingOnly] = chargingOnly
        }
    }

    suspend fun updateAutoBackupEnabled(accountKey: String, enabled: Boolean) {
        context.dataStore.edit { preferences ->
            val keys = accountKeys(accountKey)
            preferences[keys.autoBackup] = enabled
            preferences[keys.backupSetupPromptAnswered] = true
            preferences[keys.backupSetupPending] = false
        }
    }

    suspend fun answerBackupSetupPrompt(accountKey: String, configureFolders: Boolean) {
        context.dataStore.edit { preferences ->
            val keys = accountKeys(accountKey)
            preferences[keys.backupSetupPromptAnswered] = true
            preferences[keys.backupSetupPending] = configureFolders
        }
    }

    /** Explicitly chosen "all folders" path, committed atomically before a worker can scan. */
    suspend fun enableBackupForAllFolders(accountKey: String) {
        context.dataStore.edit { preferences ->
            val keys = accountKeys(accountKey)
            preferences[keys.sourceMode] = "all"
            preferences[keys.autoBackup] = true
            preferences[keys.backupSetupPromptAnswered] = true
            preferences[keys.backupSetupPending] = false
        }
    }

    suspend fun completePendingBackupSetupIfScopeChosen(accountKey: String): Boolean {
        var enabled = false
        context.dataStore.edit { preferences ->
            val keys = accountKeys(accountKey)
            val hasScope = preferences[keys.sourceMode] == "all" ||
                !preferences[keys.selectedSourceIds].isNullOrEmpty()
            if (preferences[keys.backupSetupPending] == true && hasScope) {
                preferences[keys.autoBackup] = true
                preferences[keys.backupSetupPending] = false
                enabled = true
            }
        }
        return enabled
    }

    suspend fun updateSyncSourceMode(accountKey: String, mode: String) {
        require(mode == "all" || mode == "selected")
        context.dataStore.edit { preferences ->
            val keys = accountKeys(accountKey)
            preferences[keys.sourceMode] = mode
        }
    }

    suspend fun updateSelectedSourceIds(accountKey: String, sourceIds: Set<String>) {
        context.dataStore.edit { preferences ->
            val keys = accountKeys(accountKey)
            preferences[keys.selectedSourceIds] = sourceIds
            if (sourceIds.isNotEmpty() && preferences[keys.backupSetupPending] == true) {
                preferences[keys.autoBackup] = true
                preferences[keys.backupSetupPending] = false
            }
        }
    }

    suspend fun updateSyncImagesEnabled(accountKey: String, enabled: Boolean) {
        context.dataStore.edit { it[accountKeys(accountKey).imagesEnabled] = enabled }
    }

    suspend fun updateSyncVideosEnabled(accountKey: String, enabled: Boolean) {
        context.dataStore.edit { it[accountKeys(accountKey).videosEnabled] = enabled }
    }

    private suspend fun retireLegacyUnscopedSyncSettings() {
        context.dataStore.edit { preferences ->
            if (preferences[PreferencesKeys.LEGACY_SYNC_SETTINGS_MIGRATED] == true) return@edit

            // Old versions stored these settings device-wide, without an
            // owner. Applying them to whichever account happens to log in
            // first could silently upload that account's media under someone
            // else's old policy. Start every account with safe defaults.
            preferences.remove(PreferencesKeys.LEGACY_SYNC_WIFI_ONLY)
            preferences.remove(PreferencesKeys.LEGACY_SYNC_CHARGING_ONLY)
            preferences.remove(PreferencesKeys.LEGACY_AUTO_BACKUP_ENABLED)
            preferences.remove(PreferencesKeys.LEGACY_SYNC_SOURCE_MODE)
            preferences.remove(PreferencesKeys.LEGACY_SYNC_SELECTED_SOURCE_IDS)
            preferences.remove(PreferencesKeys.LEGACY_SYNC_IMAGES_ENABLED)
            preferences.remove(PreferencesKeys.LEGACY_SYNC_VIDEOS_ENABLED)
            preferences[PreferencesKeys.LEGACY_SYNC_SETTINGS_MIGRATED] = true
        }
    }

    private fun accountKeys(accountKey: String): AccountPreferenceKeys {
        require(accountKey.isNotBlank()) { "Sync settings require an account identity" }
        val digest = MessageDigest.getInstance("SHA-256")
            .digest(accountKey.toByteArray(Charsets.UTF_8))
            .joinToString("") { byte -> "%02x".format(byte.toInt() and 0xff) }
        return AccountPreferenceKeys(
            wifiOnly = booleanPreferencesKey("sync_wifi_only_$digest"),
            chargingOnly = booleanPreferencesKey("sync_charging_only_$digest"),
            autoBackup = booleanPreferencesKey("auto_backup_enabled_$digest"),
            backupSetupPromptAnswered = booleanPreferencesKey("backup_setup_prompt_answered_$digest"),
            backupSetupPending = booleanPreferencesKey("backup_setup_pending_$digest"),
            sourceMode = stringPreferencesKey("sync_source_mode_$digest"),
            selectedSourceIds = stringSetPreferencesKey("sync_selected_source_ids_$digest"),
            imagesEnabled = booleanPreferencesKey("sync_images_enabled_$digest"),
            videosEnabled = booleanPreferencesKey("sync_videos_enabled_$digest")
        )
    }

    private fun cloudStatusKeys(accountKey: String): AccountCloudStatusKeys {
        require(accountKey.isNotBlank()) { "Cloud sync status requires an account identity" }
        val digest = MessageDigest.getInstance("SHA-256")
            .digest(accountKey.toByteArray(Charsets.UTF_8))
            .joinToString("") { byte -> "%02x".format(byte.toInt() and 0xff) }
        return AccountCloudStatusKeys(
            state = stringPreferencesKey("cloud_state_$digest"),
            lastChecked = longPreferencesKey("cloud_checked_at_$digest"),
            lastSuccessfulSync = longPreferencesKey("cloud_synced_at_$digest"),
            syncError = stringPreferencesKey("cloud_sync_error_$digest"),
        )
    }

    companion object {
        val DEFAULT_SERVER_URL: String = BuildConfig.IRIS_DEFAULT_SERVER_URL
    }
}
