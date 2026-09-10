package com.iris.app.ui.screens.search

import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import com.iris.app.data.model.MediaRecord
import com.iris.app.data.repository.IrisRepository
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
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
    val textBonus: Float = 1.0f,
    val lexicalWeight: Float = 0.25f,
    val showFilters: Boolean = false,
    val results: List<MediaRecord> = emptyList(),
    val total: Int = 0,
    val isSearching: Boolean = false,
    val error: String? = null,
    val hasSearched: Boolean = false
)

class SearchViewModel(
    private val repository: IrisRepository
) : ViewModel() {

    private val _uiState = MutableStateFlow(SearchUiState())
    val uiState: StateFlow<SearchUiState> = _uiState.asStateFlow()

    private var searchJob: Job? = null

    fun onQueryChange(newQuery: String) {
        _uiState.update { it.copy(query = newQuery) }
        searchJob?.cancel()
        if (newQuery.trim().length >= 2) {
            searchJob = viewModelScope.launch {
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
    }

    fun toggleFilters() {
        _uiState.update { it.copy(showFilters = !it.showFilters) }
    }

    fun searchRandom() {
        viewModelScope.launch {
            _uiState.update { it.copy(isSearching = true, error = null, query = "") }
            repository.searchRandom(count = 30).onSuccess { resp ->
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
                _uiState.update {
                    it.copy(
                        isSearching = false,
                        hasSearched = true,
                        error = ex.localizedMessage ?: "Erro ao buscar mídias aleatórias"
                    )
                }
            }
        }
    }

    fun performSearch() {
        val q = _uiState.value.query.trim()
        if (q.isBlank()) return

        searchJob?.cancel()
        searchJob = viewModelScope.launch {
            _uiState.update { it.copy(isSearching = true, error = null) }

            val result = when (_uiState.value.mode) {
                SearchMode.SEMANTIC -> repository.searchText(
                    query = q,
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
                val rawMsg = ex.localizedMessage ?: ex.message ?: ""
                val errorMsg = when {
                    rawMsg.contains("401") -> "Autenticação necessária (faça login na aba Backup)"
                    rawMsg.contains("Unexpected token", ignoreCase = true) ->
                        "Resposta inesperada do servidor (verifique a URL em Configurações)"
                    else -> rawMsg.ifBlank { "Erro na busca" }
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

    class Factory(private val repository: IrisRepository) : ViewModelProvider.Factory {
        @Suppress("UNCHECKED_CAST")
        override fun <T : ViewModel> create(modelClass: Class<T>): T {
            return SearchViewModel(repository) as T
        }
    }
}
