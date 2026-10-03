package com.iris.app.ui.screens.gallery

import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import com.iris.app.data.model.MediaOriginIndex
import com.iris.app.data.sync.DeviceFolders
import com.iris.app.data.model.MediaRecord
import com.iris.app.data.model.ServerInfo
import com.iris.app.data.model.CloudConnectionState
import com.iris.app.data.model.CloudSyncStatus
import com.iris.app.data.repository.IrisRepository
import com.iris.app.data.repository.GalleryDataSource
import com.iris.app.data.repository.ServerSettingsRepository
import com.iris.app.performance.Metric
import com.iris.app.performance.PerformanceMonitor
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.debounce
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
    val isServerChecking: Boolean = false,
    val cloudSyncStatus: CloudSyncStatus = CloudSyncStatus(),
    val origins: MediaOriginIndex = MediaOriginIndex.EMPTY,
    /** The account's folder selection; device media outside it is marked "not in backup". */
    val backupPolicy: com.iris.app.data.model.MediaScanPolicy? = null,
    val deviceMediaPermissionGranted: Boolean = false,
    val deviceTotalRecords: Int = 0,
    val deviceTotalPages: Int = 1,
    val devicePage: Int = 0,
    val serverTotalPages: Int = 1
)

class GalleryViewModel(
    private val repository: IrisRepository,
    val performanceMonitor: PerformanceMonitor,
    private val galleryDataSource: GalleryDataSource,
    private val settingsRepository: ServerSettingsRepository? = null,
    /** MediaStore changes ([com.iris.app.data.local.DeviceMediaChanges]); none in tests by default. */
    deviceMediaChanges: kotlinx.coroutines.flow.Flow<Unit> = kotlinx.coroutines.flow.emptyFlow(),
) : ViewModel() {

    private val _uiState = MutableStateFlow(GalleryUiState())
    val uiState: StateFlow<GalleryUiState> = _uiState.asStateFlow()
    private val recordMerger = GalleryRecordMerger()
    private var firstContentFinish: (() -> Unit)? = null
    private var initialLoadJob: Job? = null
    private var firstPageLoadJob: Job? = null
    private var devicePageLoadJob: Job? = null
    private var serverRecords: List<MediaRecord> = emptyList()
    private var deviceRecords: List<MediaRecord> = emptyList()
    /** The upload queue as last read; device pages take their hashes from it. */
    private var queueOrigins: MediaOriginIndex = MediaOriginIndex.EMPTY
    private var serverTotalRecords: Int = 0
    private var deviceTotalRecords: Int = 0

    init {
        val initialSessionKey = currentSessionKey()
        if (initialSessionKey != null) {
            showMirroredCatalog()
        } else {
            // A device can be logged out after process death before the prior
            // logout finished clearing disk. Never hydrate that private mirror.
            viewModelScope.launch { runCatching { galleryDataSource.clear() } }
        }
        refreshDeviceMedia()
        refreshOrigins()
        checkServerAndLoad()
        viewModelScope.launch {
            // New media appears as in the system gallery, without a manual refresh.
            deviceMediaChanges.debounce(DEVICE_CHANGE_DEBOUNCE_MILLIS).collect {
                reloadLoadedDevicePages()
                refreshOrigins()
            }
        }
        settingsRepository?.let { settings ->
            viewModelScope.launch {
                repository.credentialsStore.accountIdentity.collectLatest { accountKey ->
                    if (accountKey == null) {
                        _uiState.update { it.copy(backupPolicy = null) }
                        return@collectLatest
                    }
                    settings.syncSettingsForAccount(accountKey).collect { syncSettings ->
                        _uiState.update { it.copy(backupPolicy = DeviceFolders.policyOf(syncSettings)) }
                    }
                }
            }
            viewModelScope.launch {
                repository.credentialsStore.accountIdentity.collectLatest { accountKey ->
                    var previousSuccessfulSyncAt: Long? = null
                    settings.cloudSyncStatusForAccount(accountKey).collect { status ->
                        val syncCompletedInBackground = previousSuccessfulSyncAt != null &&
                            status.lastSuccessfulSyncAtMillis != null &&
                            status.lastSuccessfulSyncAtMillis != previousSuccessfulSyncAt
                        previousSuccessfulSyncAt = status.lastSuccessfulSyncAtMillis
                        _uiState.update { current ->
                            current.copy(
                                cloudSyncStatus = status,
                                isServerOnline = when (status.connectionState) {
                                    CloudConnectionState.OFFLINE -> false
                                    CloudConnectionState.CONNECTED -> true
                                    else -> current.isServerOnline
                                },
                                error = if (status.connectionState == CloudConnectionState.CONNECTED &&
                                    current.error == "SERVER_OFFLINE"
                                ) null else current.error,
                            )
                        }
                        // Queue changes and server totals may have moved while
                        // this screen was open. Refresh once per successful
                        // background cycle, not on every health probe.
                        if (syncCompletedInBackground && !_uiState.value.isServerChecking) refresh()
                    }
                }
            }
        }
        // A lost session must immediately hide private rows and clear their
        // rebuildable mirror; a new session starts with a fresh server read.
        viewModelScope.launch {
            var previousSessionKey = initialSessionKey
            repository.credentialsStore.sessionIdentity.collect { sessionIdentity ->
                if (sessionIdentity == previousSessionKey) return@collect
                previousSessionKey = sessionIdentity
                if (sessionIdentity != null) {
                    showMirroredCatalog()
                    refresh()
                } else {
                    clearPrivateRecords()
            runCatching { galleryDataSource.clear() }
                    checkServerAndLoad()
                }
            }
        }
    }

    /**
     * Reads the durable upload queue so each cell can say where it lives.
     *
     * The queue is local and small, so this stays off the network entirely:
     * a cell can be marked before the server answers anything.
     */
    fun refreshOrigins() {
        val requestedSessionKey = currentSessionKey() ?: run {
            _uiState.update { it.copy(origins = MediaOriginIndex.EMPTY) }
            return
        }
        viewModelScope.launch {
            val jobs = try {
                repository.getUploadQueue()
            } catch (cancelled: kotlinx.coroutines.CancellationException) {
                throw cancelled
            } catch (_: Exception) {
                return@launch
            }
            if (currentSessionKey() != requestedSessionKey) return@launch
            val originIndex = MediaOriginIndex.from(jobs)
            queueOrigins = originIndex
            // A file changed since it was hashed keeps no hash: it stays visible
            // as a local item ("checking") instead of merging into the old copy.
            deviceRecords = deviceRecords.map { record ->
                record.copy(contentHash = originIndex.verifiedHashOf(record)?.ifBlank { null })
            }
            _uiState.update {
                it.copy(
                    origins = originIndex,
                    records = recordMerger.merge(serverRecords, deviceRecords),
                    totalRecords = totalRecordEstimate(originIndex)
                )
            }
        }
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
        val sessionKey = currentSessionKey() ?: return
        viewModelScope.launch {
            if (currentSessionKey() != sessionKey) return@launch
            runCatching { galleryDataSource.activateSession(sessionKey) }
            if (currentSessionKey() != sessionKey) return@launch
            val cached = runCatching {
                galleryDataSource.cachedRecords(
                    offset = 0,
                    limit = MIRROR_FIRST_PAINT,
                    mediaType = _uiState.value.mediaType,
                )
            }.getOrNull().orEmpty()
            if (cached.isEmpty() || currentSessionKey() != sessionKey) return@launch
            val cachedTotal = runCatching { galleryDataSource.cachedCount(_uiState.value.mediaType) }
                .getOrDefault(0)
            if (currentSessionKey() != sessionKey) return@launch
            _uiState.update { current ->
                // Never paint over a network result that already arrived.
                if (currentSessionKey() != sessionKey || serverRecords.isNotEmpty()) current
                else {
                    serverRecords = cached
                    serverTotalRecords = cachedTotal
                    current.copy(
                        records = recordMerger.merge(serverRecords, deviceRecords),
                        totalRecords = totalRecordEstimate(),
                        totalPages = maxOf(current.deviceTotalPages, pagesFor(cachedTotal))
                    )
                }
            }
        }
    }

    /** Reads a small MediaStore page independently from account/server state. */
    private fun withQueueHashes(records: List<MediaRecord>): List<MediaRecord> = records.map { record ->
        val hash = queueOrigins.verifiedHashOf(record)
        if (hash != null) record.copy(contentHash = hash) else record
    }

    /**
     * Rereads every device page loaded so far and replaces them at once, so a
     * change seen while the user is scrolled down neither drops the pages
     * below nor leaves a deleted item behind.
     */
    private fun reloadLoadedDevicePages() {
        devicePageLoadJob?.cancel()
        devicePageLoadJob = viewModelScope.launch {
            val mediaType = _uiState.value.mediaType
            val loaded = _uiState.value.devicePage.coerceAtLeast(1)
            val fresh = mutableListOf<MediaRecord>()
            var last: com.iris.app.data.local.DeviceGalleryPage? = null
            for (page in 1..loaded) {
                val result = runCatching { galleryDataSource.devicePage(page, PAGE_SIZE, mediaType) }.getOrNull()
                    ?: return@launch
                if (!result.permissionGranted) return@launch
                fresh += result.records
                last = result
                if (page >= result.totalPages) break
            }
            val total = last ?: return@launch
            deviceRecords = withQueueHashes(fresh).distinctBy { it.deviceUri }
            deviceTotalRecords = total.total
            _uiState.update { current ->
                val devicePages = total.totalPages.coerceAtLeast(1)
                current.copy(
                    records = recordMerger.merge(serverRecords, deviceRecords),
                    deviceTotalRecords = total.total,
                    deviceTotalPages = devicePages,
                    totalPages = maxOf(current.serverTotalPages, devicePages),
                    totalRecords = totalRecordEstimate()
                )
            }
        }
    }

    fun refreshDeviceMedia(page: Int = 1) {
        devicePageLoadJob?.cancel()
        devicePageLoadJob = viewModelScope.launch {
            val devicePage = runCatching {
                galleryDataSource.devicePage(page, PAGE_SIZE, _uiState.value.mediaType)
            }.getOrNull() ?: return@launch
            if (!devicePage.permissionGranted) {
                if (page == 1) {
                    deviceRecords = emptyList()
                    deviceTotalRecords = 0
                    _uiState.update {
                        it.copy(
                            records = recordMerger.merge(serverRecords, deviceRecords),
                            deviceMediaPermissionGranted = false,
                            deviceTotalRecords = 0,
                            deviceTotalPages = 1,
                            devicePage = 0,
                            totalRecords = totalRecordEstimate()
                        )
                    }
                }
                return@launch
            }
            // A fresh page carries no hashes; reapply the queue's. Otherwise a
            // page arriving after refreshOrigins undid it, and media the server
            // already holds showed twice (its server copy and the device one).
            val hashed = withQueueHashes(devicePage.records)
            deviceRecords = if (page == 1) {
                hashed
            } else {
                (deviceRecords + hashed).distinctBy { it.deviceUri }
            }
            deviceTotalRecords = devicePage.total
            _uiState.update { current ->
                val devicePages = devicePage.totalPages.coerceAtLeast(1)
                current.copy(
                    records = recordMerger.merge(serverRecords, deviceRecords),
                    deviceMediaPermissionGranted = true,
                    deviceTotalRecords = devicePage.total,
                    deviceTotalPages = devicePages,
                    devicePage = maxOf(current.devicePage, page),
                    page = maxOf(current.page, page),
                    totalPages = maxOf(current.serverTotalPages, devicePages),
                    totalRecords = totalRecordEstimate()
                )
            }
        }
    }

    fun checkServerAndLoad() {
        val requestedSessionKey = currentSessionKey()
        val requestedAccountKey = repository.credentialsStore.accountIdentity.value
        if (requestedSessionKey == null) clearPrivateRecords()
        if (_uiState.value.records.isEmpty()) {
            firstContentFinish = performanceMonitor.begin(Metric.GalleryFirstContent)
        }
        initialLoadJob?.cancel()
        initialLoadJob = viewModelScope.launch {
            _uiState.update { it.copy(isServerChecking = true) }

            // 1. Probe server with unauthenticated /healthz
            val healthResult = repository.checkServerHealth()
            if (currentSessionKey() != requestedSessionKey) {
                if (currentSessionKey() == null) {
                    requireLoginAndClearCatalog()
                } else {
                    checkServerAndLoad()
                }
                return@launch
            }
            if (healthResult.isFailure) {
                if (requestedAccountKey != null) {
                    settingsRepository?.markCloudUnavailable(requestedAccountKey)
                }
                _uiState.update {
                    it.copy(
                        isServerChecking = false,
                        isServerOnline = false,
                        isDeviceLoggedIn = requestedSessionKey != null,
                        serverInfo = null,
                        error = "SERVER_OFFLINE"
                    )
                }
                return@launch
            }

            // Server is reachable!
            if (requestedAccountKey != null) {
                settingsRepository?.markCloudConnected(requestedAccountKey)
            }
            _uiState.update {
                it.copy(
                    isServerOnline = true,
                    isDeviceLoggedIn = requestedSessionKey != null,
                    // Health is the connection decision. Library information is
                    // optional decoration and must never keep the gallery in a
                    // permanent "connecting" state.
                    isServerChecking = false
                )
            }

            // 2. Check if logged in to access the private library
            if (requestedSessionKey == null) {
                requireLoginAndClearCatalog()
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
        // Uploads progress while the grid is open, so the badges are only
        // truthful if they are re-read whenever the user asks for fresh data.
        refreshOrigins()
        refreshDeviceMedia()
        _uiState.update { it.copy(isRefreshing = true) }
        checkServerAndLoad()
    }

    fun setMediaType(type: String) {
        if (_uiState.value.mediaType != type) {
            serverRecords = emptyList()
            deviceRecords = emptyList()
            serverTotalRecords = 0
            deviceTotalRecords = 0
            _uiState.update { it.copy(mediaType = type) }
            refreshDeviceMedia()
            loadPage(page = 1, isRefresh = false)
        }
    }

    fun loadNextPage() {
        val current = _uiState.value
        if (current.isLoading || current.isRefreshing || current.page >= current.totalPages) return
        loadPage(page = current.page + 1, isRefresh = false)
    }

    private fun loadPage(page: Int, isRefresh: Boolean) {
        val requestedSessionKey = currentSessionKey() ?: run {
            if (page == 1 || page <= _uiState.value.deviceTotalPages) refreshDeviceMedia(page)
            return
        }
        if (page == 1 || page <= _uiState.value.deviceTotalPages) refreshDeviceMedia(page)
        if (page > 1 && page > _uiState.value.serverTotalPages) return
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

            galleryDataSource.serverPage(
                page = page,
                // A home server has noticeable request latency. One useful batch
                // plus look-ahead avoids making the user wait at every short scroll.
                pageSize = PAGE_SIZE,
                // Newest-first by capture date, like Google Photos — the gallery
                // groups pages into date headers and that only stays coherent if
                // pages arrive in date order.
                mediaType = _uiState.value.mediaType
            ).onSuccess { response ->
                finishPage()
                if (currentSessionKey() != requestedSessionKey) {
                    if (currentSessionKey() == null) requireLoginAndClearCatalog()
                    return@onSuccess
                }
                // One transaction per page, so ordinary scrolling warms the
                // mirror for the next cold start.
                launch {
                    if (currentSessionKey() == requestedSessionKey) {
                        runCatching {
                            galleryDataSource.remember(response.records, requestedSessionKey) {
                                currentSessionKey() == requestedSessionKey
                            }
                        }
                    }
                }
                serverRecords = if (page == 1) {
                    response.records
                } else {
                    (serverRecords + response.records).distinctBy { it.index }
                }
                serverTotalRecords = response.total
                _uiState.update { current ->
                    current.copy(
                        records = recordMerger.merge(serverRecords, deviceRecords),
                        isLoading = false,
                        isRefreshing = false,
                        page = response.page,
                        serverTotalPages = response.totalPages.coerceAtLeast(1),
                        totalPages = maxOf(response.totalPages.coerceAtLeast(1), current.deviceTotalPages),
                        totalRecords = totalRecordEstimate(),
                        error = null
                    )
                }
            }.onFailure { ex ->
                if (currentSessionKey() != requestedSessionKey) {
                    if (currentSessionKey() == null) requireLoginAndClearCatalog()
                    return@onFailure
                }
                val rawMsg = ex.localizedMessage ?: ex.message ?: ""
                val errorMsg = when {
                    rawMsg.contains("401") -> "AUTH_REQUIRED"
                    rawMsg.contains("Unexpected token", ignoreCase = true) ->
                        "Resposta inesperada do servidor (verifique a URL em Configurações)"
                    rawMsg.contains("Connection refused", ignoreCase = true) || rawMsg.contains("ConnectException", ignoreCase = true) ->
                        "SERVER_OFFLINE"
                    else -> rawMsg.ifBlank { "Erro ao carregar mídias da biblioteca" }
                }
                if (errorMsg == "AUTH_REQUIRED") {
                    repository.logoutDevice()
                    requireLoginAndClearCatalog()
                    return@onFailure
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

    /** Clears both the in-memory view and its rebuildable private-library cache. */
    private suspend fun requireLoginAndClearCatalog() {
        clearPrivateRecords()
        _uiState.update {
            it.copy(
                isServerChecking = false,
                isDeviceLoggedIn = false,
                serverInfo = null,
                error = "AUTH_REQUIRED"
            )
        }
                    runCatching { galleryDataSource.clear() }
    }

    private fun clearPrivateRecords() {
        serverRecords = emptyList()
        serverTotalRecords = 0
        _uiState.update {
            it.copy(
                records = deviceRecords,
                origins = MediaOriginIndex.EMPTY,
                isLoading = false,
                isRefreshing = false,
                isDeviceLoggedIn = false,
                error = null,
                page = it.devicePage.coerceAtLeast(1),
                totalPages = it.deviceTotalPages,
                totalRecords = deviceTotalRecords,
                serverTotalPages = 1,
                serverInfo = null
            )
        }
    }

    /** A response or mirror read belongs only to the session that started it. */
    private fun currentSessionKey(): String? {
        return repository.credentialsStore.sessionIdentity.value
            ?.takeIf { repository.credentialsStore.hasValidCredentials() }
    }

    private fun totalRecordEstimate(origins: MediaOriginIndex = _uiState.value.origins): Int {
        val knownCopies = deviceRecords.mapNotNull { it.contentHash }
            .distinct()
            .count(origins::hasServerCopy)
        return (serverTotalRecords + deviceTotalRecords - knownCopies)
            .coerceAtLeast(_uiState.value.records.size)
    }

    private fun pagesFor(total: Int): Int = ((total + PAGE_SIZE - 1) / PAGE_SIZE).coerceAtLeast(1)

    /** Called only after Compose has received a frame with real gallery content. */
    fun onFirstContentDrawn() {
        firstContentFinish?.invoke()
        firstContentFinish = null
    }

    private companion object {
        /** Enough to fill the first screens while the network catches up. */
        const val MIRROR_FIRST_PAINT = 60
        const val PAGE_SIZE = 24
        /** A camera shot is several MediaStore notifications; reload once they settle. */
        const val DEVICE_CHANGE_DEBOUNCE_MILLIS = 700L
    }

    class Factory(
        private val repository: IrisRepository,
        private val performanceMonitor: PerformanceMonitor,
        private val galleryDataSource: GalleryDataSource,
        private val settingsRepository: ServerSettingsRepository? = null,
        private val deviceMediaChanges: kotlinx.coroutines.flow.Flow<Unit> = kotlinx.coroutines.flow.emptyFlow(),
    ) : ViewModelProvider.Factory {
        @Suppress("UNCHECKED_CAST")
        override fun <T : ViewModel> create(modelClass: Class<T>): T {
            return GalleryViewModel(repository, performanceMonitor, galleryDataSource, settingsRepository, deviceMediaChanges) as T
        }
    }
}
