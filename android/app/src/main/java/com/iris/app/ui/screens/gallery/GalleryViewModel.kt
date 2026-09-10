package com.iris.app.ui.screens.gallery

import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import com.iris.app.data.model.MediaRecord
import com.iris.app.data.model.ServerInfo
import com.iris.app.data.repository.IrisRepository
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

data class GalleryUiState(
    val records: List<MediaRecord> = emptyList(),
    val isLoading: Boolean = false,
    val isRefreshing: Boolean = false,
    val error: String? = null,
    val isServerOnline: Boolean? = null,
    val isDeviceLoggedIn: Boolean = false,
    val page: Int = 1,
    val totalPages: Int = 1,
    val totalRecords: Int = 0,
    val mediaType: String = "all",
    val serverInfo: ServerInfo? = null,
    val isServerChecking: Boolean = false
)

class GalleryViewModel(
    private val repository: IrisRepository
) : ViewModel() {

    private val _uiState = MutableStateFlow(GalleryUiState())
    val uiState: StateFlow<GalleryUiState> = _uiState.asStateFlow()

    init {
        checkServerAndLoad()
    }

    fun checkServerAndLoad() {
        viewModelScope.launch {
            _uiState.update { it.copy(isServerChecking = true) }
            val isLoggedIn = repository.credentialsStore.hasValidCredentials()

            // 1. Probe server with unauthenticated /healthz
            val healthResult = repository.checkServerHealth()
            if (healthResult.isFailure) {
                _uiState.update {
                    it.copy(
                        isServerChecking = false,
                        isServerOnline = false,
                        isDeviceLoggedIn = isLoggedIn,
                        serverInfo = null,
                        error = "SERVER_OFFLINE"
                    )
                }
                return@launch
            }

            // Server is reachable!
            _uiState.update {
                it.copy(
                    isServerOnline = true,
                    isDeviceLoggedIn = isLoggedIn
                )
            }

            // 2. Check if logged in to access the private library
            if (!isLoggedIn) {
                _uiState.update {
                    it.copy(
                        isServerChecking = false,
                        serverInfo = null,
                        error = "AUTH_REQUIRED"
                    )
                }
                return@launch
            }

            // 3. User is authenticated -> load library stats and records
            repository.getServerInfo().onSuccess { info ->
                _uiState.update { it.copy(serverInfo = info, isServerChecking = false) }
            }.onFailure {
                _uiState.update { it.copy(serverInfo = null, isServerChecking = false) }
            }

            loadPage(page = 1, isRefresh = false)
        }
    }

    fun refresh() {
        viewModelScope.launch {
            _uiState.update { it.copy(isRefreshing = true) }
            checkServerAndLoad()
        }
    }

    fun setMediaType(type: String) {
        if (_uiState.value.mediaType != type) {
            _uiState.update { it.copy(mediaType = type) }
            if (_uiState.value.isDeviceLoggedIn) {
                loadPage(page = 1, isRefresh = false)
            }
        }
    }

    fun loadNextPage() {
        val current = _uiState.value
        if (current.isLoading || current.isRefreshing || current.page >= current.totalPages || !current.isDeviceLoggedIn) return
        loadPage(page = current.page + 1, isRefresh = false)
    }

    private fun loadPage(page: Int, isRefresh: Boolean) {
        viewModelScope.launch {
            _uiState.update {
                it.copy(
                    isLoading = !isRefresh,
                    isRefreshing = isRefresh,
                    error = null
                )
            }

            repository.getRecords(
                page = page,
                perPage = 30,
                mediaType = _uiState.value.mediaType
            ).onSuccess { response ->
                _uiState.update { current ->
                    val combined = if (page == 1) response.records else current.records + response.records
                    current.copy(
                        records = combined,
                        isLoading = false,
                        isRefreshing = false,
                        page = response.page,
                        totalPages = response.totalPages,
                        totalRecords = response.total,
                        error = null
                    )
                }
            }.onFailure { ex ->
                val errorMsg = if (ex.message?.contains("401") == true) {
                    "AUTH_REQUIRED"
                } else {
                    ex.localizedMessage ?: "Erro ao carregar mídias da biblioteca"
                }
                _uiState.update {
                    it.copy(
                        isLoading = false,
                        isRefreshing = false,
                        error = errorMsg
                    )
                }
            }
        }
    }

    class Factory(private val repository: IrisRepository) : ViewModelProvider.Factory {
        @Suppress("UNCHECKED_CAST")
        override fun <T : ViewModel> create(modelClass: Class<T>): T {
            return GalleryViewModel(repository) as T
        }
    }
}
