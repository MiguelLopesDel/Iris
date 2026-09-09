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
    val connectionTestResult: String? = null,
    val isConnectionSuccessful: Boolean? = null,
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
                isConnectionSuccessful = null
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
            _uiState.update {
                it.copy(isTestingConnection = true, connectionTestResult = null, isConnectionSuccessful = null)
            }

            irisRepository.getServerInfo().onSuccess { info ->
                _uiState.update {
                    it.copy(
                        isTestingConnection = false,
                        isConnectionSuccessful = true,
                        serverInfo = info,
                        connectionTestResult = "Conexão estabelecida com sucesso! (${info.records} mídias)"
                    )
                }
            }.onFailure { ex ->
                _uiState.update {
                    it.copy(
                        isTestingConnection = false,
                        isConnectionSuccessful = false,
                        serverInfo = null,
                        connectionTestResult = "Falha ao conectar: ${ex.localizedMessage ?: "Servidor indisponível"}"
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
