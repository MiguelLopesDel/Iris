package com.iris.app.ui.screens.gallery

import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import com.iris.app.data.catalog.MediaCatalog
import com.iris.app.data.model.MediaRecord
import com.iris.app.data.model.ServerInfo
import com.iris.app.data.repository.IrisRepository
import com.iris.app.performance.Metric
import com.iris.app.performance.PerformanceMonitor
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.Job
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
    private val repository: IrisRepository,
    val performanceMonitor: PerformanceMonitor,
    private val catalog: MediaCatalog? = null
) : ViewModel() {

    private val _uiState = MutableStateFlow(GalleryUiState())
    val uiState: StateFlow<GalleryUiState> = _uiState.asStateFlow()
    private var firstContentFinish: (() -> Unit)? = null
    private var initialLoadJob: Job? = null
    private var firstPageLoadJob: Job? = null

    init {
        showMirroredCatalog()
        checkServerAndLoad()
    }

    /**
     * Paints whatever the local mirror already holds, before any network call.
     *
     * A cold start otherwise shows placeholder tiles until a health check, a
     * library-info call and the first page have all completed in sequence — on
     * a home server over a VPN that is seconds of empty grid. The network
     * result replaces this as soon as it lands.
     */
    private fun showMirroredCatalog() {
        val catalog = catalog ?: return
        viewModelScope.launch {
            val cached = runCatching {
                catalog.cached(offset = 0, limit = MIRROR_FIRST_PAINT, mediaType = _uiState.value.mediaType)
            }.getOrNull().orEmpty()
            if (cached.isEmpty()) return@launch
            val cachedTotal = runCatching { catalog.cachedCount(_uiState.value.mediaType) }.getOrDefault(0)
            _uiState.update { current ->
                // Never paint over a network result that already arrived.
                if (current.records.isNotEmpty()) current
                else current.copy(records = cached, totalRecords = cachedTotal)
            }
        }
    }

    fun checkServerAndLoad() {
        if (_uiState.value.records.isEmpty()) {
            firstContentFinish = performanceMonitor.begin(Metric.GalleryFirstContent)
        }
        initialLoadJob?.cancel()
        initialLoadJob = viewModelScope.launch {
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
                    isDeviceLoggedIn = isLoggedIn,
                    // Health is the connection decision. Library information is
                    // optional decoration and must never keep the gallery in a
                    // permanent "connecting" state.
                    isServerChecking = false
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
            viewModelScope.launch {
                repository.getServerInfo().onSuccess { info ->
                    _uiState.update { it.copy(serverInfo = info) }
                }.onFailure {
                    _uiState.update { it.copy(serverInfo = null) }
                }
            }

            loadPage(page = 1, isRefresh = false)
        }
    }

    fun refresh() {
        _uiState.update { it.copy(isRefreshing = true) }
        checkServerAndLoad()
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
        val finishPage = performanceMonitor.begin(
            if (page == 1) Metric.GalleryFirstPage else Metric.GalleryPage
        )
        if (page == 1) firstPageLoadJob?.cancel()
        val job = viewModelScope.launch {
            _uiState.update {
                it.copy(
                    isLoading = !isRefresh,
                    isRefreshing = isRefresh,
                    error = null
                )
            }

            repository.getRecords(
                page = page,
                // A home server has noticeable request latency. One useful batch
                // plus look-ahead avoids making the user wait at every short scroll.
                perPage = 24,
                // Newest-first by capture date, like Google Photos — the gallery
                // groups pages into date headers and that only stays coherent if
                // pages arrive in date order.
                sortBy = "data",
                mediaType = _uiState.value.mediaType
            ).onSuccess { response ->
                finishPage()
                // One transaction per page, so ordinary scrolling warms the
                // mirror for the next cold start.
                catalog?.let { mirror ->
                    launch { runCatching { mirror.remember(response.records) } }
                }
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
                val rawMsg = ex.localizedMessage ?: ex.message ?: ""
                val errorMsg = when {
                    rawMsg.contains("401") -> "AUTH_REQUIRED"
                    rawMsg.contains("Unexpected token", ignoreCase = true) ->
                        "Resposta inesperada do servidor (verifique a URL em Configurações)"
                    rawMsg.contains("Connection refused", ignoreCase = true) || rawMsg.contains("ConnectException", ignoreCase = true) ->
                        "SERVER_OFFLINE"
                    else -> rawMsg.ifBlank { "Erro ao carregar mídias da biblioteca" }
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
        if (page == 1) firstPageLoadJob = job
    }

    /** Called only after Compose has received a frame with real gallery content. */
    fun onFirstContentDrawn() {
        firstContentFinish?.invoke()
        firstContentFinish = null
    }

    private companion object {
        /** Enough to fill the first screens while the network catches up. */
        const val MIRROR_FIRST_PAINT = 60
    }

    class Factory(
        private val repository: IrisRepository,
        private val performanceMonitor: PerformanceMonitor,
        private val catalog: MediaCatalog? = null
    ) : ViewModelProvider.Factory {
        @Suppress("UNCHECKED_CAST")
        override fun <T : ViewModel> create(modelClass: Class<T>): T {
            return GalleryViewModel(repository, performanceMonitor, catalog) as T
        }
    }
}
