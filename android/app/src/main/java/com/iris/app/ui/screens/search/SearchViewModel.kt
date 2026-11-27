package com.iris.app.ui.screens.search

import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import com.iris.app.R
import com.iris.app.data.model.MediaRecord
import com.iris.app.data.repository.IrisRepository
import com.iris.app.data.repository.SearchPreferences
import com.iris.app.data.repository.ServerSettingsRepository
import com.iris.app.ui.state.LatestJob
import com.iris.app.ui.state.isUnreadableResponse
import com.iris.app.ui.text.appString
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

enum class SearchMode {
    SEMANTIC,
    FILENAME
}

data class SearchUiState(
    val query: String = "",
    val mode: SearchMode = SearchMode.SEMANTIC,
    val mediaType: String = "all",
    val balance: Float = 0.5f,
    val threshold: Float = 0.15f,
    val textBonus: Float = 1.0f,
    val lexicalWeight: Float = 0.25f,
    val showFilters: Boolean = false,
    val results: List<MediaRecord> = emptyList(),
    val total: Int = 0,
    val isSearching: Boolean = false,
    val error: String? = null,
    val hasSearched: Boolean = false
) {
    /**
     * The state right after the query is edited. A search still running for
     * the old text is cancelled, so it must stop showing as running: nothing
     * else would end it until the next search.
     */
    fun withQuery(newQuery: String): SearchUiState {
        val text = newQuery.trim()
        return when {
            text.isEmpty() -> copy(
                query = newQuery, results = emptyList(), total = 0,
                hasSearched = false, error = null, isSearching = false,
            )
            text.length < MIN_QUERY_LENGTH -> copy(query = newQuery, isSearching = false)
            else -> copy(query = newQuery)
        }
    }

    /** The saved search settings, which the search sends from then on. */
    fun withPreferences(preferences: SearchPreferences): SearchUiState = copy(
        balance = preferences.balance,
        threshold = preferences.threshold,
        textBonus = preferences.textBonus,
    )

    companion object {
        const val MIN_QUERY_LENGTH = 2
    }
}

class SearchViewModel(
    private val repository: IrisRepository,
    // The saved settings, and where a balance the user picks is kept. They
    // were stored but never read: each visit started from the defaults.
    searchPreferences: Flow<SearchPreferences> = flowOf(SearchPreferences.DEFAULT),
    private val saveBalance: suspend (Float) -> Unit = {},
) : ViewModel() {

    private val _uiState = MutableStateFlow(SearchUiState())
    val uiState: StateFlow<SearchUiState> = _uiState.asStateFlow()

    // Text and random searches share it: whichever started last is the one shown.
    private val searchJob = LatestJob()
    private var preferences = SearchPreferences.DEFAULT

    init {
        viewModelScope.launch {
            searchPreferences.collect { saved ->
                preferences = saved
                _uiState.update { it.withPreferences(saved) }
            }
        }
        var previousSession = repository.credentialsStore.sessionIdentity.value
        viewModelScope.launch {
            repository.credentialsStore.sessionIdentity.collect { identity ->
                if (identity == previousSession) return@collect
                previousSession = identity
                searchJob.cancel()
                _uiState.value = SearchUiState().withPreferences(preferences)
            }
        }
    }

    fun onQueryChange(newQuery: String) {
        searchJob.cancel()
        _uiState.update { it.withQuery(newQuery) }
        if (newQuery.trim().length >= SearchUiState.MIN_QUERY_LENGTH) {
            searchJob.launch(viewModelScope) {
                delay(400) // Debounce typing
                performSearch()
            }
        }
    }

    fun setMode(mode: SearchMode) {
        _uiState.update { it.copy(mode = mode) }
        if (_uiState.value.query.isNotBlank()) {
            performSearch()
        }
    }

    fun setMediaType(type: String) {
        _uiState.update { it.copy(mediaType = type) }
        if (_uiState.value.query.isNotBlank()) {
            performSearch()
        }
    }

    fun setBalance(balance: Float) {
        _uiState.update { it.copy(balance = balance) }
        viewModelScope.launch { saveBalance(balance) }
    }

    fun toggleFilters() {
        _uiState.update { it.copy(showFilters = !it.showFilters) }
    }

    fun searchRandom() {
        val requestedSession = repository.credentialsStore.sessionIdentity.value ?: return
        searchJob.launch(viewModelScope) {
            _uiState.update { it.copy(isSearching = true, error = null, query = "") }
            repository.searchRandom(count = 30).onSuccess { resp ->
                if (repository.credentialsStore.sessionIdentity.value != requestedSession) return@onSuccess
                _uiState.update {
                    it.copy(
                        results = resp.results,
                        total = resp.total,
                        isSearching = false,
                        hasSearched = true,
                        error = null
                    )
                }
            }.onFailure { ex ->
                if (repository.credentialsStore.sessionIdentity.value != requestedSession) return@onFailure
                _uiState.update {
                    it.copy(
                        isSearching = false,
                        hasSearched = true,
                        error = ex.localizedMessage ?: appString(R.string.search_error_random)
                    )
                }
            }
        }
    }

    fun performSearch() {
        val q = _uiState.value.query.trim()
        if (q.isBlank()) return
        val requestedSession = repository.credentialsStore.sessionIdentity.value ?: return

        searchJob.launch(viewModelScope) {
            _uiState.update { it.copy(isSearching = true, error = null) }

            val result = when (_uiState.value.mode) {
                SearchMode.SEMANTIC -> repository.searchText(
                    query = q,
                    threshold = _uiState.value.threshold,
                    balance = _uiState.value.balance,
                    textBonus = _uiState.value.textBonus,
                    lexicalWeight = _uiState.value.lexicalWeight,
                    mediaType = _uiState.value.mediaType
                )
                SearchMode.FILENAME -> repository.searchFilename(
                    query = q,
                    mediaType = _uiState.value.mediaType
                )
            }

            result.onSuccess { resp ->
                if (repository.credentialsStore.sessionIdentity.value != requestedSession) return@onSuccess
                _uiState.update {
                    it.copy(
                        results = resp.results,
                        total = resp.total,
                        isSearching = false,
                        hasSearched = true,
                        error = null
                    )
                }
            }.onFailure { ex ->
                if (repository.credentialsStore.sessionIdentity.value != requestedSession) return@onFailure
                val rawMsg = ex.localizedMessage ?: ex.message ?: ""
                val errorMsg = when {
                    rawMsg.contains("401") -> appString(R.string.error_auth_required_backup)
                    isUnreadableResponse(ex) ->
                        appString(R.string.error_unexpected_response)
                    else -> rawMsg.ifBlank { appString(R.string.search_error) }
                }
                _uiState.update {
                    it.copy(
                        isSearching = false,
                        hasSearched = true,
                        error = errorMsg
                    )
                }
            }
        }
    }

    class Factory(
        private val repository: IrisRepository,
        private val settings: ServerSettingsRepository,
    ) : ViewModelProvider.Factory {
        @Suppress("UNCHECKED_CAST")
        override fun <T : ViewModel> create(modelClass: Class<T>): T {
            return SearchViewModel(repository, settings.searchPreferences, settings::updateSearchBalance) as T
        }
    }
}
