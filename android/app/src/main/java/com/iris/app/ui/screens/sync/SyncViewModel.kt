package com.iris.app.ui.screens.sync

import android.content.Context
import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import com.iris.app.data.local.DeviceCredentialsStore
import com.iris.app.data.model.CloudSyncStatus
import com.iris.app.data.model.DeviceMediaSource
import com.iris.app.data.model.LocalUploadJob
import com.iris.app.data.model.SyncRun
import com.iris.app.data.model.UploadJobState
import com.iris.app.data.repository.IrisRepository
import com.iris.app.data.repository.ServerSettingsRepository
import com.iris.app.data.sync.MediaStoreScanner
import com.iris.app.data.sync.MediaSyncWorker
import com.iris.app.data.sync.ServerSpeedTest
import com.iris.app.data.sync.SyncRunRecorder
import com.iris.app.data.sync.UploadSpeedSnapshot
import kotlinx.coroutines.delay
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.flow.flatMapLatest
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

data class SyncUiState(
    val isLoggedIn: Boolean = false,
    val serverUrl: String = "",
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
    val queueRefreshFailed: Boolean = false,
    val cloudSyncStatus: CloudSyncStatus = CloudSyncStatus(),
    val isSyncing: Boolean = false,
    /** Media examined so far by the running scan, out of the selected folders' total; null when none runs. */
    val scanProgress: MediaStoreScanner.ScanProgress? = null,
    /** A sync that did not finish is waiting to be retried automatically. */
    val retryPending: Boolean = false,
    val currentProgress: Float = 0f,
    val syncWifiOnly: Boolean = false,
    val syncChargingOnly: Boolean = false,
    val autoBackupEnabled: Boolean = false,
    val backupSetupPromptAnswered: Boolean = false,
    val backupSetupPending: Boolean = false,
    val backupSetupPromptReady: Boolean = false,
    val syncSettingsAccountKey: String? = null,
    val sourceMode: String = "selected",
    val selectedSourceIds: Set<String> = emptySet(),
    val syncImagesEnabled: Boolean = true,
    val syncVideosEnabled: Boolean = true,
    val availableSources: List<DeviceMediaSource> = emptyList(),
    val isDiscoveringSources: Boolean = false,
    val sourceDiscoveryError: String? = null,
    val isQueueExpanded: Boolean = false,
    /** Throughput of the current upload run, or of the last one once it ends. */
    val uploadSpeed: UploadSpeedSnapshot = UploadSpeedSnapshot(),
    val remainingUploadBytes: Long = 0L,
    val syncRuns: List<SyncRun> = emptyList(),
    /** The run executing in this process; a stored running row that is not it was interrupted. */
    val activeSyncRunIds: Set<Long> = emptySet(),
    val isHistoryExpanded: Boolean = false,
    val isSpeedTestRunning: Boolean = false,
    val speedTestResults: List<ServerSpeedTest.Result> = emptyList(),
    val speedTestError: String? = null
)

class SyncViewModel(
    private val repository: IrisRepository,
    private val settingsRepository: ServerSettingsRepository,
    private val credentialsStore: DeviceCredentialsStore,
    private val syncRetryPending: Flow<Boolean> = flowOf(false),
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
        viewModelScope.launch {
            settingsRepository.serverUrl.collect { url ->
                _uiState.update { it.copy(serverUrl = url) }
            }
        }
        loadQueue()
        startPeriodicQueuePoller()
        startSpeedTicker()
        viewModelScope.launch {
            SyncRunRecorder.activeRunIds.collect { ids ->
                _uiState.update { it.copy(activeSyncRunIds = ids) }
            }
        }
    }

    @OptIn(ExperimentalCoroutinesApi::class)
    private fun observeSettings() {
        viewModelScope.launch {
            var previousSession = credentialsStore.sessionIdentity.value
            var previousAccount = credentialsStore.accountIdentity.value
            credentialsStore.sessionIdentity.collect { identity ->
                val accountKey = credentialsStore.accountIdentity.value
                val accountChanged = identity != previousSession || accountKey != previousAccount
                val accountSettingsChanged = accountKey != previousAccount
                previousSession = identity
                previousAccount = accountKey
                if (identity == null) {
                    _uiState.update {
                        it.copy(
                            isLoggedIn = false,
                            username = "",
                            deviceId = "",
                            uploadQueue = emptyList(),
                            queueCounts = emptyMap(),
                            queueTotal = 0,
                            queueRefreshFailed = false,
                            cloudSyncStatus = CloudSyncStatus(),
                            isSyncing = false,
                            currentProgress = 0f,
                            uploadSpeed = UploadSpeedSnapshot(),
                            remainingUploadBytes = 0L,
                            syncRuns = emptyList(),
                            syncWifiOnly = false,
                            syncChargingOnly = false,
                            autoBackupEnabled = false,
                            backupSetupPromptAnswered = false,
                            backupSetupPending = false,
                            backupSetupPromptReady = false,
                            syncSettingsAccountKey = null,
                            sourceMode = "selected",
                            selectedSourceIds = emptySet(),
                            syncImagesEnabled = true,
                            syncVideosEnabled = true,
                        )
                    }
                } else {
                    _uiState.update {
                        val settingsNeedLoading = accountSettingsChanged &&
                            it.syncSettingsAccountKey != accountKey
                        it.copy(
                            isLoggedIn = true,
                            username = credentialsStore.getUsername(),
                            deviceId = credentialsStore.getDeviceId() ?: "",
                            uploadQueue = if (accountChanged) emptyList() else it.uploadQueue,
                            queueCounts = if (accountChanged) emptyMap() else it.queueCounts,
                            queueTotal = if (accountChanged) 0 else it.queueTotal,
                            queueRefreshFailed = if (accountChanged) false else it.queueRefreshFailed,
                            cloudSyncStatus = if (accountChanged) CloudSyncStatus() else it.cloudSyncStatus,
                            syncWifiOnly = if (settingsNeedLoading) false else it.syncWifiOnly,
                            syncChargingOnly = if (settingsNeedLoading) false else it.syncChargingOnly,
                            autoBackupEnabled = if (settingsNeedLoading) false else it.autoBackupEnabled,
                            backupSetupPromptAnswered = if (settingsNeedLoading) false else it.backupSetupPromptAnswered,
                            backupSetupPending = if (settingsNeedLoading) false else it.backupSetupPending,
                            backupSetupPromptReady = if (settingsNeedLoading) false else it.backupSetupPromptReady,
                            syncSettingsAccountKey = if (settingsNeedLoading) null else it.syncSettingsAccountKey,
                            sourceMode = if (settingsNeedLoading) "selected" else it.sourceMode,
                            selectedSourceIds = if (settingsNeedLoading) emptySet() else it.selectedSourceIds,
                            syncImagesEnabled = if (settingsNeedLoading) true else it.syncImagesEnabled,
                            syncVideosEnabled = if (settingsNeedLoading) true else it.syncVideosEnabled,
                        )
                    }
                    loadQueue()
                }
            }
        }
        viewModelScope.launch {
            credentialsStore.accountIdentity
                .flatMapLatest { accountKey ->
                    settingsRepository.syncSettingsForAccount(accountKey).map { accountKey to it }
                }
                .collect { (accountKey, settings) ->
                    if (credentialsStore.accountIdentity.value != accountKey) return@collect
                    _uiState.update {
                        it.copy(
                            syncWifiOnly = settings.wifiOnly,
                            syncChargingOnly = settings.chargingOnly,
                            autoBackupEnabled = settings.autoBackupEnabled,
                            backupSetupPromptAnswered = settings.backupSetupPromptAnswered,
                            backupSetupPending = settings.backupSetupPending,
                            backupSetupPromptReady = true,
                            syncSettingsAccountKey = accountKey,
                            sourceMode = settings.sourceMode,
                            selectedSourceIds = settings.selectedSourceIds,
                            syncImagesEnabled = settings.imagesEnabled,
                            syncVideosEnabled = settings.videosEnabled
                        )
                    }
                }
        }
        viewModelScope.launch {
            credentialsStore.accountIdentity
                .flatMapLatest { accountKey ->
                    settingsRepository.cloudSyncStatusForAccount(accountKey).map { accountKey to it }
                }
                .collect { (accountKey, status) ->
                    if (credentialsStore.accountIdentity.value != accountKey) return@collect
                    _uiState.update { it.copy(cloudSyncStatus = status) }
                }
        }
        viewModelScope.launch {
            repository.uploadManager.isUploading.collect { uploading ->
                _uiState.update { it.copy(isSyncing = uploading) }
            }
        }
        viewModelScope.launch {
            repository.mediaScanner.scanProgress.collect { progress ->
                _uiState.update { it.copy(scanProgress = progress) }
            }
        }
        viewModelScope.launch {
            syncRetryPending.collect { pending ->
                _uiState.update { it.copy(retryPending = pending) }
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
        viewModelScope.launch {
            MediaSyncWorker.enqueueImmediate(context)
            loadQueue()
        }
    }

    fun setSyncWifiOnly(wifiOnly: Boolean) {
        val accountKey = credentialsStore.accountIdentity.value ?: return
        viewModelScope.launch { settingsRepository.updateSyncWifiOnly(accountKey, wifiOnly) }
    }

    fun setSyncChargingOnly(chargingOnly: Boolean) {
        val accountKey = credentialsStore.accountIdentity.value ?: return
        viewModelScope.launch { settingsRepository.updateSyncChargingOnly(accountKey, chargingOnly) }
    }

    fun setAutoBackupEnabled(enabled: Boolean, context: Context) {
        val accountKey = credentialsStore.accountIdentity.value ?: return
        viewModelScope.launch {
            settingsRepository.updateAutoBackupEnabled(accountKey, enabled)
            if (enabled) enqueueBackgroundIfEnabled(context, accountKey)
        }
    }

    fun answerBackupSetupPrompt(configureFolders: Boolean, onSaved: () -> Unit = {}) {
        val accountKey = credentialsStore.accountIdentity.value ?: return
        _uiState.update {
            it.copy(
                backupSetupPromptAnswered = true,
                backupSetupPending = configureFolders
            )
        }
        viewModelScope.launch {
            settingsRepository.answerBackupSetupPrompt(accountKey, configureFolders)
            onSaved()
        }
    }

    fun enableBackupForAllFolders(context: Context) {
        val accountKey = credentialsStore.accountIdentity.value ?: return
        viewModelScope.launch {
            settingsRepository.enableBackupForAllFolders(accountKey)
            enqueueBackgroundIfEnabled(context, accountKey)
        }
    }

    fun finishPendingBackupSetupIfScopeChosen(context: Context) {
        val accountKey = credentialsStore.accountIdentity.value ?: return
        viewModelScope.launch {
            if (settingsRepository.completePendingBackupSetupIfScopeChosen(accountKey)) {
                enqueueBackgroundIfEnabled(context, accountKey)
            }
        }
    }

    fun showMediaPermissionRequired() {
        _uiState.update { it.copy(sourceDiscoveryError = "MEDIA_PERMISSION_REQUIRED") }
    }

    fun setSourceMode(mode: String, context: Context) {
        val accountKey = credentialsStore.accountIdentity.value ?: return
        viewModelScope.launch {
            settingsRepository.updateSyncSourceMode(accountKey, mode)
            enqueueBackgroundIfEnabled(context, accountKey)
        }
    }

    fun toggleSource(sourceId: String, enabled: Boolean, context: Context) {
        val selected = _uiState.value.selectedSourceIds.toMutableSet()
        if (enabled) selected += sourceId else selected -= sourceId
        val accountKey = credentialsStore.accountIdentity.value ?: return
        viewModelScope.launch {
            settingsRepository.updateSelectedSourceIds(accountKey, selected)
            enqueueBackgroundIfEnabled(context, accountKey)
        }
    }

    fun clearSelectedSources(context: Context) {
        val accountKey = credentialsStore.accountIdentity.value ?: return
        _uiState.update { it.copy(selectedSourceIds = emptySet()) }
        viewModelScope.launch {
            settingsRepository.updateSelectedSourceIds(accountKey, emptySet())
            enqueueBackgroundIfEnabled(context, accountKey)
        }
    }

    fun setSyncImagesEnabled(enabled: Boolean) {
        val accountKey = credentialsStore.accountIdentity.value ?: return
        viewModelScope.launch { settingsRepository.updateSyncImagesEnabled(accountKey, enabled) }
    }

    fun setSyncVideosEnabled(enabled: Boolean) {
        val accountKey = credentialsStore.accountIdentity.value ?: return
        viewModelScope.launch { settingsRepository.updateSyncVideosEnabled(accountKey, enabled) }
    }

    fun discoverSources() {
        val requestedSession = credentialsStore.sessionIdentity.value ?: return
        viewModelScope.launch {
            _uiState.update { it.copy(isDiscoveringSources = true, sourceDiscoveryError = null) }
            try {
                val sources = repository.mediaScanner.discoverSources()
                if (credentialsStore.sessionIdentity.value != requestedSession) return@launch
                    _uiState.update {
                        it.copy(
                            availableSources = sources,
                            isDiscoveringSources = false,
                        )
                    }
            } catch (cancelled: CancellationException) {
                throw cancelled
            } catch (_: Exception) {
                if (credentialsStore.sessionIdentity.value != requestedSession) return@launch
                    _uiState.update {
                        it.copy(
                            isDiscoveringSources = false,
                            sourceDiscoveryError = "MEDIA_PERMISSION_REQUIRED"
                        )
                    }
            }
        }
    }

    private suspend fun enqueueBackgroundIfEnabled(context: Context, accountKey: String) {
        if (credentialsStore.accountIdentity.value != accountKey) return
        val settings = settingsRepository.syncSettingsForAccount(accountKey).first()
        if (credentialsStore.accountIdentity.value == accountKey && settings.autoBackupEnabled) {
            MediaSyncWorker.enqueueBackground(context, settings)
        }
    }

    fun loadQueue() {
        val requestedSession = credentialsStore.sessionIdentity.value
        val requestedAccount = credentialsStore.accountIdentity.value
        if (requestedSession == null || requestedAccount == null) {
            _uiState.update {
                it.copy(
                    uploadQueue = emptyList(),
                    queueCounts = emptyMap(),
                    queueTotal = 0,
                )
            }
            return
        }
        viewModelScope.launch {
            val counts = try {
                repository.getUploadQueueCounts()
            } catch (cancelled: CancellationException) {
                throw cancelled
            } catch (_: Exception) {
                if (credentialsStore.sessionIdentity.value == requestedSession &&
                    credentialsStore.accountIdentity.value == requestedAccount
                ) {
                    _uiState.update { it.copy(queueRefreshFailed = true) }
                }
                return@launch
            }
            if (credentialsStore.sessionIdentity.value != requestedSession ||
                credentialsStore.accountIdentity.value != requestedAccount
            ) return@launch
            val total = counts.values.sum()
            // A janela só é lida quando a lista está aberta: fechada, os números
            // do resumo já respondem o que o usuário quer saber.
            val window = if (_uiState.value.isQueueExpanded) {
                try {
                    repository.getRecentUploadJobs(QUEUE_WINDOW)
                } catch (cancelled: CancellationException) {
                    throw cancelled
                } catch (_: Exception) {
                    emptyList()
                }
            } else {
                emptyList()
            }
            if (credentialsStore.sessionIdentity.value != requestedSession ||
                credentialsStore.accountIdentity.value != requestedAccount
            ) return@launch
            val remainingBytes = try {
                repository.getRemainingUploadBytes()
            } catch (cancelled: CancellationException) {
                throw cancelled
            } catch (_: Exception) {
                _uiState.value.remainingUploadBytes
            }
            val runs = try {
                repository.getRecentSyncRuns(HISTORY_LIMIT)
            } catch (cancelled: CancellationException) {
                throw cancelled
            } catch (_: Exception) {
                _uiState.value.syncRuns
            }
            if (credentialsStore.sessionIdentity.value != requestedSession ||
                credentialsStore.accountIdentity.value != requestedAccount
            ) return@launch
            _uiState.update {
                it.copy(
                    uploadQueue = window,
                    queueCounts = counts,
                    queueTotal = total,
                    queueRefreshFailed = false,
                    remainingUploadBytes = remainingBytes,
                    syncRuns = runs
                )
            }
        }
    }

    /** Measures the path to the server. Refused while uploading, which would share the link. */
    fun runSpeedTest() {
        val state = _uiState.value
        if (state.isSpeedTestRunning || state.isSyncing || !state.isLoggedIn) return
        _uiState.update { it.copy(isSpeedTestRunning = true, speedTestResults = emptyList(), speedTestError = null) }
        viewModelScope.launch {
            val outcome = repository.runServerSpeedTest { result ->
                _uiState.update { it.copy(speedTestResults = it.speedTestResults + result) }
            }
            _uiState.update {
                it.copy(
                    isSpeedTestRunning = false,
                    speedTestError = outcome.exceptionOrNull()?.let { error ->
                        error.localizedMessage ?: error.javaClass.simpleName
                    }
                )
            }
        }
    }

    fun toggleHistory() = _uiState.update { it.copy(isHistoryExpanded = !it.isHistoryExpanded) }

    /** Refreshes the live rate every second while uploading, and once more when it stops. */
    private fun startSpeedTicker() {
        viewModelScope.launch {
            val meter = repository.uploadManager.speedMeter
            while (true) {
                _uiState.update { it.copy(uploadSpeed = meter.snapshot()) }
                delay(if (_uiState.value.isSyncing) 1_000L else 3_000L)
            }
        }
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
        /** Runs shown in the upload history. */
        const val HISTORY_LIMIT = 20
    }

    class Factory(
        private val repository: IrisRepository,
        private val settingsRepository: ServerSettingsRepository,
        private val credentialsStore: DeviceCredentialsStore,
        private val syncRetryPending: Flow<Boolean> = flowOf(false),
    ) : ViewModelProvider.Factory {
        @Suppress("UNCHECKED_CAST")
        override fun <T : ViewModel> create(modelClass: Class<T>): T {
            return SyncViewModel(repository, settingsRepository, credentialsStore, syncRetryPending) as T
        }
    }
}
