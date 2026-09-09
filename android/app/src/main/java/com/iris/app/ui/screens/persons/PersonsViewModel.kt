package com.iris.app.ui.screens.persons

import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import com.iris.app.data.model.MediaRecord
import com.iris.app.data.model.Person
import com.iris.app.data.repository.IrisRepository
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

data class PersonsUiState(
    val persons: List<Person> = emptyList(),
    val isLoading: Boolean = false,
    val isRefreshing: Boolean = false,
    val error: String? = null
)

data class PersonMediaUiState(
    val personId: Int,
    val personName: String = "",
    val media: List<MediaRecord> = emptyList(),
    val total: Int = 0,
    val isLoading: Boolean = false,
    val error: String? = null
)

class PersonsViewModel(
    private val repository: IrisRepository
) : ViewModel() {

    private val _uiState = MutableStateFlow(PersonsUiState())
    val uiState: StateFlow<PersonsUiState> = _uiState.asStateFlow()

    init {
        loadPersons()
    }

    fun loadPersons(isRefresh: Boolean = false) {
        viewModelScope.launch {
            _uiState.update { it.copy(isLoading = !isRefresh, isRefreshing = isRefresh, error = null) }
            repository.getPersons().onSuccess { list ->
                _uiState.update {
                    it.copy(
                        persons = list,
                        isLoading = false,
                        isRefreshing = false,
                        error = null
                    )
                }
            }.onFailure { ex ->
                _uiState.update {
                    it.copy(
                        isLoading = false,
                        isRefreshing = false,
                        error = ex.localizedMessage ?: "Erro ao carregar pessoas"
                    )
                }
            }
        }
    }

    class Factory(private val repository: IrisRepository) : ViewModelProvider.Factory {
        @Suppress("UNCHECKED_CAST")
        override fun <T : ViewModel> create(modelClass: Class<T>): T {
            return PersonsViewModel(repository) as T
        }
    }
}

class PersonMediaViewModel(
    private val personId: Int,
    private val initialPersonName: String,
    private val repository: IrisRepository
) : ViewModel() {

    private val _uiState = MutableStateFlow(
        PersonMediaUiState(personId = personId, personName = initialPersonName)
    )
    val uiState: StateFlow<PersonMediaUiState> = _uiState.asStateFlow()

    init {
        loadMedia()
    }

    fun loadMedia() {
        viewModelScope.launch {
            _uiState.update { it.copy(isLoading = true, error = null) }
            repository.getPersonMedia(personId).onSuccess { resp ->
                _uiState.update {
                    it.copy(
                        personName = resp.personName.ifBlank { initialPersonName },
                        media = resp.results,
                        total = resp.total,
                        isLoading = false,
                        error = null
                    )
                }
            }.onFailure { ex ->
                _uiState.update {
                    it.copy(
                        isLoading = false,
                        error = ex.localizedMessage ?: "Erro ao carregar mídias da pessoa"
                    )
                }
            }
        }
    }

    class Factory(
        private val personId: Int,
        private val personName: String,
        private val repository: IrisRepository
    ) : ViewModelProvider.Factory {
        @Suppress("UNCHECKED_CAST")
        override fun <T : ViewModel> create(modelClass: Class<T>): T {
            return PersonMediaViewModel(personId, personName, repository) as T
        }
    }
}
