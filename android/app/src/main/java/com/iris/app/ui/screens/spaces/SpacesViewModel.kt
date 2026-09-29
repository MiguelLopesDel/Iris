package com.iris.app.ui.screens.spaces

import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import com.iris.app.data.model.SpaceAlbum
import com.iris.app.data.model.SpaceItem
import com.iris.app.data.model.SpaceItemsResponse
import com.iris.app.data.model.SpaceMember
import com.iris.app.data.model.SpaceSummary
import com.iris.app.data.repository.IrisRepository
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

data class SpacesUiState(
    val spaces: List<SpaceSummary> = emptyList(),
    val isLoading: Boolean = false,
    val error: String? = null,
    val notice: String? = null,
)

class SpacesViewModel(private val repository: IrisRepository) : ViewModel() {

    private val _uiState = MutableStateFlow(SpacesUiState())
    val uiState: StateFlow<SpacesUiState> = _uiState.asStateFlow()

    init {
        val initialSession = repository.credentialsStore.sessionIdentity.value
        if (initialSession != null) load()
        viewModelScope.launch {
            var previousSession = initialSession
            repository.credentialsStore.sessionIdentity.collect { identity ->
                if (identity == previousSession) return@collect
                previousSession = identity
                if (identity == null) _uiState.value = SpacesUiState(error = "AUTH_REQUIRED")
                else {
                    _uiState.value = SpacesUiState()
                    load()
                }
            }
        }
    }

    fun load() {
        val requestedSession = repository.credentialsStore.sessionIdentity.value ?: return
        viewModelScope.launch {
            _uiState.update { it.copy(isLoading = true, error = null) }
            repository.getSpaces()
                .onSuccess { spaces ->
                    if (repository.credentialsStore.sessionIdentity.value == requestedSession) {
                        _uiState.update { it.copy(spaces = spaces, isLoading = false) }
                    }
                }
                .onFailure { e ->
                    if (repository.credentialsStore.sessionIdentity.value != requestedSession) return@onFailure
                    _uiState.update {
                        it.copy(isLoading = false, error = e.localizedMessage ?: "Erro ao carregar espaços")
                    }
                }
        }
    }

    fun create(name: String, onCreated: (SpaceSummary) -> Unit) {
        if (name.isBlank()) return
        val requestedSession = repository.credentialsStore.sessionIdentity.value ?: return
        viewModelScope.launch {
            repository.createSpace(name)
                .onSuccess { space ->
                    if (repository.credentialsStore.sessionIdentity.value != requestedSession) return@onSuccess
                    _uiState.update { it.copy(spaces = listOf(space) + it.spaces) }
                    onCreated(space)
                }
                .onFailure { e ->
                    if (repository.credentialsStore.sessionIdentity.value == requestedSession) {
                        showNotice(e.localizedMessage ?: "Não foi possível criar o espaço")
                    }
                }
        }
    }

    fun showNotice(message: String?) {
        _uiState.update { it.copy(notice = message) }
    }

    class Factory(private val repository: IrisRepository) : ViewModelProvider.Factory {
        @Suppress("UNCHECKED_CAST")
        override fun <T : ViewModel> create(modelClass: Class<T>): T = SpacesViewModel(repository) as T
    }
}

data class SpaceUiState(
    val spaceId: Int,
    val name: String = "",
    val space: SpaceSummary? = null,
    val items: List<SpaceItem> = emptyList(),
    val nextBefore: Int? = null,
    val isLoading: Boolean = false,
    val isLoadingMore: Boolean = false,
    val members: List<SpaceMember> = emptyList(),
    val albums: List<SpaceAlbum> = emptyList(),
    /** Showing one album's photos instead of the whole space. */
    val albumId: Int? = null,
    /** Showing search results; no paging then. */
    val query: String = "",
    val error: String? = null,
    val notice: String? = null,
    /** Set once this account no longer belongs to the space: leave the screen. */
    val left: Boolean = false,
)

class SpaceViewModel(
    spaceId: Int,
    name: String,
    private val repository: IrisRepository,
) : ViewModel() {

    private val _uiState = MutableStateFlow(SpaceUiState(spaceId = spaceId, name = name))
    val uiState: StateFlow<SpaceUiState> = _uiState.asStateFlow()
    private val spaceId get() = _uiState.value.spaceId

    init {
        val initialSession = repository.credentialsStore.sessionIdentity.value
        if (initialSession != null) refresh()
        viewModelScope.launch {
            var previousSession = initialSession
            repository.credentialsStore.sessionIdentity.collect { identity ->
                if (identity == previousSession) return@collect
                previousSession = identity
                if (identity == null) clearPrivateState()
                else {
                    _uiState.value = SpaceUiState(spaceId = spaceId)
                    refresh()
                }
            }
        }
    }

    fun refresh() {
        val requestedSession = repository.credentialsStore.sessionIdentity.value ?: run {
            clearPrivateState()
            return
        }
        viewModelScope.launch {
            repository.getSpace(spaceId).onSuccess { space ->
                if (repository.credentialsStore.sessionIdentity.value != requestedSession) return@onSuccess
                _uiState.update { it.copy(space = space, name = space.name) }
            }
            if (repository.credentialsStore.sessionIdentity.value != requestedSession) return@launch
            repository.getSpaceAlbums(spaceId).onSuccess { albums ->
                if (repository.credentialsStore.sessionIdentity.value != requestedSession) return@onSuccess
                _uiState.update { state ->
                    // An album deleted on the web meanwhile stops being a filter.
                    val kept = state.albumId?.takeIf { id -> albums.any { it.id == id } }
                    state.copy(albums = albums, albumId = kept)
                }
            }
            if (repository.credentialsStore.sessionIdentity.value != requestedSession) return@launch
            loadContent()
        }
    }

    fun search(query: String) {
        _uiState.update { it.copy(query = query.trim(), albumId = null) }
        viewModelScope.launch { loadContent() }
    }

    fun showAlbum(albumId: Int?) {
        _uiState.update { it.copy(albumId = albumId, query = "") }
        viewModelScope.launch { loadContent() }
    }

    /** Whatever the screen is set to: a search, one album, or every photo. */
    private suspend fun loadContent() {
        val requestedSession = repository.credentialsStore.sessionIdentity.value ?: run {
            clearPrivateState()
            return
        }
        _uiState.update { it.copy(isLoading = true, error = null) }
        val state = _uiState.value
        val page: Result<SpaceItemsResponse> = when {
            state.query.isNotEmpty() -> repository.searchSpace(spaceId, state.query)
                .map { SpaceItemsResponse(items = it.items, nextBefore = null) }
            state.albumId != null -> repository.getSpaceAlbumItems(spaceId, state.albumId)
            else -> repository.getSpaceItems(spaceId)
        }
        page.onSuccess { result ->
            if (repository.credentialsStore.sessionIdentity.value != requestedSession) return@onSuccess
            _uiState.update { it.copy(items = result.items, nextBefore = result.nextBefore, isLoading = false) }
        }.onFailure { e ->
            if (repository.credentialsStore.sessionIdentity.value != requestedSession) return@onFailure
            _uiState.update {
                it.copy(isLoading = false, error = e.localizedMessage ?: "Erro ao carregar o espaço")
            }
        }
    }

    fun loadMore() {
        val current = _uiState.value
        val before = current.nextBefore ?: return
        if (current.isLoading || current.isLoadingMore || current.query.isNotEmpty()) return
        val requestedSession = repository.credentialsStore.sessionIdentity.value ?: return
        viewModelScope.launch {
            _uiState.update { it.copy(isLoadingMore = true) }
            val next = current.albumId?.let { repository.getSpaceAlbumItems(spaceId, it, before) }
                ?: repository.getSpaceItems(spaceId, before)
            next.onSuccess { page ->
                if (repository.credentialsStore.sessionIdentity.value != requestedSession) return@onSuccess
                _uiState.update {
                    it.copy(items = it.items + page.items, nextBefore = page.nextBefore, isLoadingMore = false)
                }
            }.onFailure {
                if (repository.credentialsStore.sessionIdentity.value == requestedSession) {
                    _uiState.update { it.copy(isLoadingMore = false) }
                }
            }
        }
    }

    fun saveToLibrary(item: SpaceItem) {
        val requestedSession = repository.credentialsStore.sessionIdentity.value ?: return
        viewModelScope.launch {
            repository.saveSpaceItem(spaceId, item.id)
                .onSuccess { result ->
                    if (repository.credentialsStore.sessionIdentity.value != requestedSession) return@onSuccess
                    showNotice(
                        if (result.state == "duplicate") "Esta foto já está na sua biblioteca"
                        else "Salva na sua biblioteca; aparece na Galeria depois de processada"
                    )
                }
                .onFailure { e ->
                    if (repository.credentialsStore.sessionIdentity.value == requestedSession) {
                        showNotice(e.localizedMessage ?: "Não foi possível salvar")
                    }
                }
        }
    }

    fun remove(item: SpaceItem, onRemoved: () -> Unit) {
        val requestedSession = repository.credentialsStore.sessionIdentity.value ?: return
        viewModelScope.launch {
            repository.removeSpaceItem(spaceId, item.id)
                .onSuccess {
                    if (repository.credentialsStore.sessionIdentity.value != requestedSession) return@onSuccess
                    _uiState.update { state -> state.copy(items = state.items.filterNot { it.id == item.id }) }
                    showNotice("Removida do espaço; pode ser restaurada pela lixeira do espaço na web")
                    onRemoved()
                }
                .onFailure { e ->
                    if (repository.credentialsStore.sessionIdentity.value == requestedSession) {
                        showNotice(e.localizedMessage ?: "Não foi possível remover")
                    }
                }
        }
    }

    fun loadMembers() {
        val requestedSession = repository.credentialsStore.sessionIdentity.value ?: return
        viewModelScope.launch {
            repository.getSpaceMembers(spaceId).onSuccess { members ->
                if (repository.credentialsStore.sessionIdentity.value == requestedSession) {
                    _uiState.update { it.copy(members = members) }
                }
            }
        }
    }

    fun leave() {
        val requestedSession = repository.credentialsStore.sessionIdentity.value ?: return
        viewModelScope.launch {
            repository.leaveSpace(spaceId)
                .onSuccess {
                    if (repository.credentialsStore.sessionIdentity.value == requestedSession) {
                        _uiState.update { it.copy(left = true) }
                    }
                }
                .onFailure { e ->
                    if (repository.credentialsStore.sessionIdentity.value == requestedSession) {
                        showNotice(e.localizedMessage ?: "Não foi possível sair do espaço")
                    }
                }
        }
    }

    private fun clearPrivateState() {
        _uiState.value = SpaceUiState(spaceId = spaceId, name = "", error = "AUTH_REQUIRED")
    }

    fun showNotice(message: String?) {
        _uiState.update { it.copy(notice = message) }
    }

    class Factory(
        private val spaceId: Int,
        private val name: String,
        private val repository: IrisRepository,
    ) : ViewModelProvider.Factory {
        @Suppress("UNCHECKED_CAST")
        override fun <T : ViewModel> create(modelClass: Class<T>): T =
            SpaceViewModel(spaceId, name, repository) as T
    }
}
