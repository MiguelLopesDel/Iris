package com.iris.app.ui.screens.sync

import android.content.Context
import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import com.iris.app.data.local.DeviceCredentialsStore
import com.iris.app.data.model.DeviceMediaSource
import com.iris.app.data.model.LocalUploadJob
import com.iris.app.data.model.UploadJobState
import com.iris.app.data.repository.IrisRepository
import com.iris.app.data.repository.ServerSettingsRepository
import com.iris.app.data.sync.MediaSyncWorker
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

data class SyncUiState(
    val isLoggedIn: Boolean = false,
    val username: String = "",
    val deviceId: String = "",
    val loginUsernameInput: String = "",
    val loginPasswordInput: String = "",
    val deviceNameInput: String = "Android Device",
    val isLoggingIn: Boolean = false,
    val loginError: String? = null,
    // Só a janela exibida. A fila inteira pode ter milhares de linhas depois
    // da primeira sincronização, e carregá-la toda a cada atualização travava
    // a tela em vez de informá-la.
    val uploadQueue: List<LocalUploadJob> = emptyList(),
    val queueCounts: Map<UploadJobState, Int> = emptyMap(),
    val queueTotal: Int = 0,
    val isSyncing: Boolean = false,
    val currentProgress: Float = 0f,
    val syncWifiOnly: Boolean = false,
    val syncChargingOnly: Boolean = false,
    val autoBackupEnabled: Boolean = false,
    val sourceMode: String = "selected",
    val selectedSourceIds: Set<String> = emptySet(),
    val syncImagesEnabled: Boolean = true,
    val syncVideosEnabled: Boolean = true,
    val availableSources: List<DeviceMediaSource> = emptyList(),
    val isDiscoveringSources: Boolean = false,
    val sourceDiscoveryError: String? = null,
    val isFolderPickerExpanded: Boolean = false,
    val isQueueExpanded: Boolean = false
)

class SyncViewModel(
    private val repository: IrisRepository,
    private val settingsRepository: ServerSettingsRepository,
    private val credentialsStore: DeviceCredentialsStore
) : ViewModel() {

    private val _uiState = MutableStateFlow(
        SyncUiState(
            isLoggedIn = credentialsStore.hasValidCredentials(),
            username = credentialsStore.getUsername(),
            deviceId = credentialsStore.getDeviceId() ?: ""
        )
    )
    val uiState: StateFlow<SyncUiState> = _uiState.asStateFlow()

    init {
        observeSettings()
        loadQueue()
        startPeriodicQueuePoller()
    }

    private fun observeSettings() {
        viewModelScope.launch {
            credentialsStore.isLoggedIn.collect { loggedIn ->
                _uiState.update {
                    it.copy(
                        isLoggedIn = loggedIn,
                        username = credentialsStore.getUsername(),
                        deviceId = credentialsStore.getDeviceId() ?: ""
                    )
                }
            }
        }
        viewModelScope.launch {
            settingsRepository.syncWifiOnly.collect { wifiOnly ->
                _uiState.update { it.copy(syncWifiOnly = wifiOnly) }
            }
        }
        viewModelScope.launch {
            settingsRepository.syncChargingOnly.collect { chargingOnly ->
                _uiState.update { it.copy(syncChargingOnly = chargingOnly) }
            }
        }
        viewModelScope.launch {
            settingsRepository.autoBackupEnabled.collect { autoBackup ->
                _uiState.update { it.copy(autoBackupEnabled = autoBackup) }
            }
        }
        viewModelScope.launch {
            settingsRepository.syncSourceMode.collect { mode ->
                _uiState.update { it.copy(sourceMode = mode) }
            }
        }
        viewModelScope.launch {
            settingsRepository.syncSelectedSourceIds.collect { sourceIds ->
                _uiState.update { it.copy(selectedSourceIds = sourceIds) }
            }
        }
        viewModelScope.launch {
            settingsRepository.syncImagesEnabled.collect { enabled ->
                _uiState.update { it.copy(syncImagesEnabled = enabled) }
            }
        }
        viewModelScope.launch {
            settingsRepository.syncVideosEnabled.collect { enabled ->
                _uiState.update { it.copy(syncVideosEnabled = enabled) }
            }
        }
        viewModelScope.launch {
            repository.uploadManager.isUploading.collect { uploading ->
                _uiState.update { it.copy(isSyncing = uploading) }
            }
        }
        viewModelScope.launch {
            repository.uploadManager.currentProgress.collect { progress ->
                _uiState.update { it.copy(currentProgress = progress) }
            }
        }
    }

    fun onUsernameChange(u: String) = _uiState.update { it.copy(loginUsernameInput = u) }
    fun onPasswordChange(p: String) = _uiState.update { it.copy(loginPasswordInput = p) }
    fun onDeviceNameChange(d: String) = _uiState.update { it.copy(deviceNameInput = d) }

    fun loginDevice() {
        val u = _uiState.value.loginUsernameInput.trim()
        val p = _uiState.value.loginPasswordInput
        val d = _uiState.value.deviceNameInput.trim()
        if (u.isBlank() || p.isBlank()) return

        viewModelScope.launch {
            _uiState.update { it.copy(isLoggingIn = true, loginError = null) }
            repository.deviceLogin(username = u, password = p, deviceName = d)
                .onSuccess {
                    _uiState.update {
                        it.copy(
                            isLoggingIn = false,
                            loginError = null,
                            loginPasswordInput = ""
                        )
                    }
                    loadQueue()
                }
                .onFailure { ex ->
                    _uiState.update {
                        it.copy(
                            isLoggingIn = false,
                            loginError = ex.localizedMessage ?: "Erro ao autenticar dispositivo"
                        )
                    }
                }
        }
    }

    fun logoutDevice() {
        repository.logoutDevice()
        loadQueue()
    }

    fun triggerManualSync(context: Context) {
        MediaSyncWorker.enqueueImmediate(context)
        loadQueue()
    }

    fun setSyncWifiOnly(wifiOnly: Boolean) {
        viewModelScope.launch { settingsRepository.updateSyncWifiOnly(wifiOnly) }
    }

    fun setSyncChargingOnly(chargingOnly: Boolean) {
        viewModelScope.launch { settingsRepository.updateSyncChargingOnly(chargingOnly) }
    }

    fun setAutoBackupEnabled(enabled: Boolean) {
        viewModelScope.launch { settingsRepository.updateAutoBackupEnabled(enabled) }
    }

    fun setSourceMode(mode: String) {
        viewModelScope.launch { settingsRepository.updateSyncSourceMode(mode) }
    }

    fun toggleSource(sourceId: String, enabled: Boolean) {
        val selected = _uiState.value.selectedSourceIds.toMutableSet()
        if (enabled) selected += sourceId else selected -= sourceId
        viewModelScope.launch { settingsRepository.updateSelectedSourceIds(selected) }
    }

    fun setSyncImagesEnabled(enabled: Boolean) {
        viewModelScope.launch { settingsRepository.updateSyncImagesEnabled(enabled) }
    }

    fun setSyncVideosEnabled(enabled: Boolean) {
        viewModelScope.launch { settingsRepository.updateSyncVideosEnabled(enabled) }
    }

    fun discoverSources() {
        viewModelScope.launch {
            _uiState.update { it.copy(isDiscoveringSources = true, sourceDiscoveryError = null) }
            runCatching { repository.mediaScanner.discoverSources() }
                .onSuccess { sources ->
                    _uiState.update {
                        it.copy(
                            availableSources = sources,
                            isDiscoveringSources = false,
                            // Asking for the folder list and getting a closed
                            // header back reads like the button did nothing.
                            isFolderPickerExpanded = sources.isNotEmpty()
                        )
                    }
                }
                .onFailure {
                    _uiState.update {
                        it.copy(
                            isDiscoveringSources = false,
                            sourceDiscoveryError = "MEDIA_PERMISSION_REQUIRED"
                        )
                    }
                }
        }
    }

    fun loadQueue() {
        viewModelScope.launch {
            val counts = runCatching { repository.getUploadQueueCounts() }.getOrNull() ?: return@launch
            val total = counts.values.sum()
            // A janela só é lida quando a lista está aberta: fechada, os números
            // do resumo já respondem o que o usuário quer saber.
            val window = if (_uiState.value.isQueueExpanded) {
                runCatching { repository.getRecentUploadJobs(QUEUE_WINDOW) }.getOrNull().orEmpty()
            } else {
                emptyList()
            }
            _uiState.update { it.copy(uploadQueue = window, queueCounts = counts, queueTotal = total) }
        }
    }

    fun toggleFolderPicker() {
        _uiState.update { it.copy(isFolderPickerExpanded = !it.isFolderPickerExpanded) }
    }

    fun toggleQueue() {
        val expanded = !_uiState.value.isQueueExpanded
        _uiState.update { it.copy(isQueueExpanded = expanded) }
        if (expanded) loadQueue() else _uiState.update { it.copy(uploadQueue = emptyList()) }
    }

    private fun startPeriodicQueuePoller() {
        viewModelScope.launch {
            while (true) {
                delay(3000)
                loadQueue()
            }
        }
    }

    companion object {
        /** Rows kept in memory for the queue list. */
        const val QUEUE_WINDOW = 40
    }

    class Factory(
        private val repository: IrisRepository,
        private val settingsRepository: ServerSettingsRepository,
        private val credentialsStore: DeviceCredentialsStore
    ) : ViewModelProvider.Factory {
        @Suppress("UNCHECKED_CAST")
        override fun <T : ViewModel> create(modelClass: Class<T>): T {
            return SyncViewModel(repository, settingsRepository, credentialsStore) as T
        }
    }
}
