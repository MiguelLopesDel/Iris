package com.iris.app.ui.screens.collections

import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import com.iris.app.data.model.IrisCollection
import com.iris.app.data.model.IrisConcept
import com.iris.app.data.model.MediaRecord
import com.iris.app.data.repository.IrisRepository
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

data class CollectionMediaUiState(
    val collectionId: Int,
    val collectionName: String,
    val members: List<MediaRecord> = emptyList(),
    val isLoading: Boolean = false,
    val error: String? = null
)

class CollectionsViewModel(
    private val repository: IrisRepository
) : ViewModel() {

    private val _uiState = MutableStateFlow(CollectionsUiState())
    val uiState: StateFlow<CollectionsUiState> = _uiState.asStateFlow()

    init {
        loadData()
    }

    fun setTab(tab: Int) {
        _uiState.update { it.copy(selectedTab = tab) }
    }

    fun loadData(isRefresh: Boolean = false) {
        viewModelScope.launch {
            _uiState.update { it.copy(isLoading = !isRefresh, isRefreshing = isRefresh, error = null) }

            val collectionsResult = repository.getCollections()
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
    private val repository: IrisRepository
) : ViewModel() {

    private val _uiState = MutableStateFlow(
        CollectionMediaUiState(collectionId = collectionId, collectionName = collectionName)
    )
    val uiState: StateFlow<CollectionMediaUiState> = _uiState.asStateFlow()

    init {
        loadMembers()
    }

    fun loadMembers() {
        viewModelScope.launch {
            _uiState.update { it.copy(isLoading = true, error = null) }
            repository.getCollectionMembers(collectionId).onSuccess { list ->
                _uiState.update {
                    it.copy(members = list, isLoading = false, error = null)
                }
            }.onFailure { ex ->
                _uiState.update {
                    it.copy(
                        isLoading = false,
                        error = ex.localizedMessage ?: "Erro ao carregar membros da coleção"
                    )
                }
            }
        }
    }

    class Factory(
        private val collectionId: Int,
        private val collectionName: String,
        private val repository: IrisRepository
    ) : ViewModelProvider.Factory {
        @Suppress("UNCHECKED_CAST")
        override fun <T : ViewModel> create(modelClass: Class<T>): T {
            return CollectionMediaViewModel(collectionId, collectionName, repository) as T
        }
    }
}
