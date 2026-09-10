package com.iris.app.ui.screens.settings

import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import com.iris.app.data.model.ServerInfo
import com.iris.app.data.repository.IrisRepository
import com.iris.app.data.repository.ServerSettingsRepository
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

data class SettingsUiState(
    val serverUrl: String = "",
    val isTestingConnection: Boolean = false,
    val isServerOnline: Boolean? = null,
    val serverMode: String? = null,
    val connectionTestResult: String? = null,
    val isDeviceLoggedIn: Boolean = false,
    val loggedInUsername: String = "",
    val deviceId: String = "",
    val serverInfo: ServerInfo? = null
)

class SettingsViewModel(
    private val settingsRepository: ServerSettingsRepository,
    private val irisRepository: IrisRepository
) : ViewModel() {

    private val _uiState = MutableStateFlow(SettingsUiState())
    val uiState: StateFlow<SettingsUiState> = _uiState.asStateFlow()

    init {
        viewModelScope.launch {
            val currentUrl = settingsRepository.serverUrl.first()
            _uiState.update { it.copy(serverUrl = currentUrl) }
            testConnection()
        }
    }

    fun onServerUrlChange(newUrl: String) {
        _uiState.update {
            it.copy(
                serverUrl = newUrl,
                connectionTestResult = null,
                isServerOnline = null
            )
        }
    }

    fun saveAndTestConnection() {
        val url = _uiState.value.serverUrl.trim()
        if (url.isBlank()) return

        viewModelScope.launch {
            settingsRepository.updateServerUrl(url)
            irisRepository.apiClient.updateBaseUrl(url)
            testConnection()
        }
    }

    fun testConnection() {
        viewModelScope.launch {
            val isLoggedIn = irisRepository.credentialsStore.hasValidCredentials()
            val username = irisRepository.credentialsStore.getUsername()
            val deviceId = irisRepository.credentialsStore.getDeviceId() ?: ""

            _uiState.update {
                it.copy(
                    isTestingConnection = true,
                    connectionTestResult = null,
                    isServerOnline = null,
                    isDeviceLoggedIn = isLoggedIn,
                    loggedInUsername = username,
                    deviceId = deviceId
                )
            }

            // Test connection using the unauthenticated /healthz probe
            val healthResult = irisRepository.checkServerHealth()
            if (healthResult.isSuccess) {
                val health = healthResult.getOrThrow()
                var info: ServerInfo? = null
                if (isLoggedIn) {
                    irisRepository.getServerInfo().onSuccess { info = it }
                }

                _uiState.update {
                    it.copy(
                        isTestingConnection = false,
                        isServerOnline = true,
                        serverMode = health.mode,
                        serverInfo = info,
                        connectionTestResult = "Servidor online e acessível (modo: ${health.mode})"
                    )
                }
            } else {
                val err = healthResult.exceptionOrNull()?.localizedMessage ?: "Servidor inacessível"
                _uiState.update {
                    it.copy(
                        isTestingConnection = false,
                        isServerOnline = false,
                        serverMode = null,
                        serverInfo = null,
                        connectionTestResult = "Servidor inacessível: $err"
                    )
                }
            }
        }
    }

    class Factory(
        private val settingsRepository: ServerSettingsRepository,
        private val irisRepository: IrisRepository
    ) : ViewModelProvider.Factory {
        @Suppress("UNCHECKED_CAST")
        override fun <T : ViewModel> create(modelClass: Class<T>): T {
            return SettingsViewModel(settingsRepository, irisRepository) as T
        }
    }
}
