package com.iris.app.ui.screens.collections

import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import com.iris.app.data.model.IrisCollection
import com.iris.app.data.model.IrisConcept
import com.iris.app.data.model.MediaRecord
import com.iris.app.data.repository.IrisRepository
import com.iris.app.performance.Metric
import com.iris.app.performance.PerformanceMonitor
import com.iris.app.ui.state.LatestJob
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

data class CollectionsUiState(
    val collections: List<IrisCollection> = emptyList(),
    val concepts: List<IrisConcept> = emptyList(),
    val selectedTab: Int = 0, // 0 = Coleções, 1 = Conceitos
    val isLoading: Boolean = false,
    val isRefreshing: Boolean = false,
    val error: String? = null
)

/** Not an album on the server: the hearted photos, shown as one. */
const val FAVORITES_COLLECTION_ID = -1

data class CollectionMediaUiState(
    val collectionId: Int,
    val collectionName: String,
    val members: List<MediaRecord> = emptyList(),
    val isLoading: Boolean = false,
    val isLoadingMore: Boolean = false,
    val page: Int = 1,
    val totalPages: Int = 1,
    val totalRecords: Int = 0,
    val error: String? = null
) {
    /**
     * Only with nothing to show: a later page that fails must not hide the
     * pages already on screen behind an error that, retried, starts over.
     */
    val showsErrorScreen: Boolean get() = error != null && members.isEmpty()
}

class CollectionsViewModel(
    private val repository: IrisRepository
) : ViewModel() {

    private val _uiState = MutableStateFlow(CollectionsUiState())
    val uiState: StateFlow<CollectionsUiState> = _uiState.asStateFlow()

    init {
        val initialSession = repository.credentialsStore.sessionIdentity.value
        if (initialSession != null) loadData()
        viewModelScope.launch {
            var previousSession = initialSession
            repository.credentialsStore.sessionIdentity.collect { identity ->
                if (identity == previousSession) return@collect
                previousSession = identity
                if (identity == null) {
                    _uiState.value = CollectionsUiState(error = "AUTH_REQUIRED")
                } else {
                    _uiState.value = CollectionsUiState()
                    loadData()
                }
            }
        }
    }

    fun setTab(tab: Int) {
        _uiState.update { it.copy(selectedTab = tab) }
    }

    fun loadData(isRefresh: Boolean = false) {
        val requestedSession = repository.credentialsStore.sessionIdentity.value ?: return
        viewModelScope.launch {
            _uiState.update { it.copy(isLoading = !isRefresh, isRefreshing = isRefresh, error = null) }

            val collectionsResult = repository.getCollections()
            if (repository.credentialsStore.sessionIdentity.value != requestedSession) return@launch
            val collections = collectionsResult.getOrDefault(emptyList())

            if (collectionsResult.isSuccess) {
                _uiState.update {
                    it.copy(
                        collections = collections,
                        isLoading = false,
                        isRefreshing = false,
                        error = null
                    )
                }
            } else {
                _uiState.update {
                    it.copy(
                        isLoading = false,
                        isRefreshing = false,
                        error = "Erro ao carregar álbuns do servidor"
                    )
                }
            }
        }
    }

    class Factory(private val repository: IrisRepository) : ViewModelProvider.Factory {
        @Suppress("UNCHECKED_CAST")
        override fun <T : ViewModel> create(modelClass: Class<T>): T {
            return CollectionsViewModel(repository) as T
        }
    }
}

class CollectionMediaViewModel(
    private val collectionId: Int,
    private val collectionName: String,
    private val repository: IrisRepository,
    val performanceMonitor: PerformanceMonitor,
    /** Shows one kind of document instead of an album. */
    private val documentKind: String? = null
) : ViewModel() {
    // One page request at a time: reloading cancels a next page still on its
    // way, which would otherwise be appended to the fresh first page.
    private val pageJob = LatestJob()

    private val _uiState = MutableStateFlow(
        CollectionMediaUiState(collectionId = collectionId, collectionName = collectionName)
    )
    val uiState: StateFlow<CollectionMediaUiState> = _uiState.asStateFlow()
    private var firstContentFinish: (() -> Unit)? = null

    init {
        val initialSession = repository.credentialsStore.sessionIdentity.value
        if (initialSession != null) loadMembers()
        viewModelScope.launch {
            var previousSession = initialSession
            repository.credentialsStore.sessionIdentity.collect { identity ->
                if (identity == previousSession) return@collect
                previousSession = identity
                if (identity == null) clearPrivateState()
                else {
                    _uiState.value = CollectionMediaUiState(collectionId, collectionName)
                    loadMembers()
                }
            }
        }
    }

    fun loadMembers() {
        loadPage(page = 1)
    }

    fun loadNextPage() {
        val current = _uiState.value
        if (current.isLoading || current.isLoadingMore || current.page >= current.totalPages) return
        loadPage(page = current.page + 1)
    }

    private fun loadPage(page: Int) {
        val requestedSession = repository.credentialsStore.sessionIdentity.value ?: run {
            clearPrivateState()
            return
        }
        val finishMembers = performanceMonitor.begin(Metric.CollectionMembers)
        if (page == 1 && _uiState.value.members.isEmpty()) {
            firstContentFinish = performanceMonitor.begin(Metric.CollectionFirstContent)
        }
        pageJob.launch(viewModelScope) {
            _uiState.update {
                it.copy(
                    isLoading = page == 1,
                    isLoadingMore = page > 1,
                    error = null
                )
            }
            val request = if (documentKind != null) {
                repository.getDocumentsPage(documentKind, page)
            } else if (collectionId == FAVORITES_COLLECTION_ID) {
                repository.getFavoritesPage(page)
            } else {
                repository.getCollectionMembersPage(collectionId, page)
            }
            request.onSuccess { response ->
                finishMembers()
                if (repository.credentialsStore.sessionIdentity.value != requestedSession) return@onSuccess
                _uiState.update {
                    it.copy(
                        members = if (page == 1) response.records else it.members + response.records,
                        isLoading = false,
                        isLoadingMore = false,
                        page = response.page,
                        totalPages = response.totalPages,
                        totalRecords = response.total,
                        error = null
                    )
                }
            }.onFailure { ex ->
                if (repository.credentialsStore.sessionIdentity.value != requestedSession) return@onFailure
                _uiState.update {
                    it.copy(
                        isLoading = false,
                        isLoadingMore = false,
                        error = ex.localizedMessage ?: "Erro ao carregar membros da coleção"
                    )
                }
            }
        }
    }

    private fun clearPrivateState() {
        _uiState.value = CollectionMediaUiState(
            collectionId = collectionId,
            collectionName = "",
            error = "AUTH_REQUIRED",
        )
    }

    fun onFirstContentDrawn() {
        firstContentFinish?.invoke()
        firstContentFinish = null
    }

    class Factory(
        private val collectionId: Int,
        private val collectionName: String,
        private val repository: IrisRepository,
        private val performanceMonitor: PerformanceMonitor,
        private val documentKind: String? = null
    ) : ViewModelProvider.Factory {
        @Suppress("UNCHECKED_CAST")
        override fun <T : ViewModel> create(modelClass: Class<T>): T {
            return CollectionMediaViewModel(
                collectionId, collectionName, repository, performanceMonitor, documentKind
            ) as T
        }
    }
}
