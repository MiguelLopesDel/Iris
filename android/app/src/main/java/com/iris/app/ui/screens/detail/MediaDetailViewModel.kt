package com.iris.app.ui.screens.detail

import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import com.iris.app.data.local.DeviceMediaDetails
import com.iris.app.data.model.MediaRecord
import com.iris.app.data.model.RecordMetadataResponse
import com.iris.app.data.repository.IrisRepository
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

data class MediaDetailUiState(
    val recordIndex: Int,
    val record: MediaRecord? = null,
    val metadata: RecordMetadataResponse? = null,
    val similarRecords: List<MediaRecord> = emptyList(),
    val isLoading: Boolean = true,
    val isLoadingMetadata: Boolean = false,
    val isLoadingSimilars: Boolean = false,
    val error: String? = null,
    val isRenaming: Boolean = false,
    /** Whether the device copy was looked for (when the panel first opened). */
    val deviceChecked: Boolean = false,
    /** The copy on this device, when this device sent the item and still holds it. */
    val deviceCopy: DeviceMediaDetails? = null,
    /** Mensagem curta para a tela mostrar e descartar (rename, download). */
    val notice: String? = null
)

class MediaDetailViewModel(
    private val recordIndex: Int,
    private val repository: IrisRepository,
    /** Reads a device item from the media store; the default finds none (tests, no device). */
    private val readDeviceMedia: (String) -> DeviceMediaDetails? = { null },
) : ViewModel() {

    private val _uiState = MutableStateFlow(MediaDetailUiState(recordIndex = recordIndex))
    val uiState: StateFlow<MediaDetailUiState> = _uiState.asStateFlow()

    init {
        val initialSession = repository.credentialsStore.sessionIdentity.value
        if (initialSession != null) loadDetail()
        viewModelScope.launch {
            var previousSession = initialSession
            repository.credentialsStore.sessionIdentity.collect { identity ->
                if (identity == previousSession) return@collect
                previousSession = identity
                _uiState.value = MediaDetailUiState(
                    recordIndex = recordIndex,
                    isLoading = false,
                    error = if (identity == null) "AUTH_REQUIRED" else null,
                )
            }
        }
    }

    fun loadDetail() {
        val requestedSession = repository.credentialsStore.sessionIdentity.value ?: run {
            clearPrivateState()
            return
        }
        viewModelScope.launch {
            _uiState.update { it.copy(isLoading = true, error = null) }
            repository.getRecordDetail(recordIndex).onSuccess { rec ->
                if (repository.credentialsStore.sessionIdentity.value != requestedSession) return@onSuccess
                _uiState.update { it.copy(record = rec, isLoading = false, error = null) }
                loadMetadata(requestedSession)
            }.onFailure { ex ->
                if (repository.credentialsStore.sessionIdentity.value != requestedSession) return@onFailure
                val rawMessage = ex.localizedMessage.orEmpty()
                val userMessage = if (rawMessage.contains("Unexpected JSON token", ignoreCase = true)) {
                    "O servidor enviou um formato de detalhes incompatível. Atualize o Iris e tente novamente."
                } else {
                    rawMessage.ifBlank { "Erro ao carregar mídia" }
                }
                _uiState.update {
                    it.copy(isLoading = false, error = userMessage)
                }
            }
        }
    }

    fun rename(newName: String) {
        val record = _uiState.value.record ?: return
        val requestedSession = repository.credentialsStore.sessionIdentity.value ?: return
        viewModelScope.launch {
            _uiState.update { it.copy(isRenaming = true, notice = null) }
            repository.renameRecord(record.index, newName)
                .onSuccess {
                    if (repository.credentialsStore.sessionIdentity.value != requestedSession) return@onSuccess
                    // O nome vem do servidor (extensão preservada, colisão
                    // resolvida), então recarrega em vez de adivinhar.
                    loadDetail()
                    _uiState.update { it.copy(isRenaming = false, notice = "Renomeado") }
                }
                .onFailure { ex ->
                    if (repository.credentialsStore.sessionIdentity.value != requestedSession) return@onFailure
                    val motivo = when {
                        ex.localizedMessage?.contains("409") == true -> "Já existe um arquivo com esse nome"
                        ex.localizedMessage?.contains("400") == true -> "Nome inválido"
                        else -> "Não foi possível renomear"
                    }
                    _uiState.update { it.copy(isRenaming = false, notice = motivo) }
                }
        }
    }

    /**
     * Looks once, when the information panel opens, for this item's copy on
     * the device: the upload this device made of it (by content hash, as the
     * gallery's badges do), and that file in the media store if it is still
     * there. Reading the whole upload history for every photo swiped past
     * would be wasted work.
     */
    fun loadBackupState() {
        val record = _uiState.value.record ?: return
        if (_uiState.value.deviceChecked) return
        val requestedSession = repository.credentialsStore.sessionIdentity.value ?: return
        viewModelScope.launch {
            val copy = try {
                val hash = record.contentHash?.lowercase()
                val job = hash?.let { h -> repository.getUploadQueue().lastOrNull { it.sha256.lowercase() == h } }
                job?.let { withContext(Dispatchers.IO) { readDeviceMedia(it.localUri) } }
            } catch (cancelled: kotlinx.coroutines.CancellationException) {
                throw cancelled
            } catch (_: Exception) {
                null
            }
            if (repository.credentialsStore.sessionIdentity.value != requestedSession) return@launch
            _uiState.update { it.copy(deviceChecked = true, deviceCopy = copy) }
        }
    }

    fun showNotice(message: String) {
        _uiState.update { it.copy(notice = message) }
    }

    fun clearNotice() {
        _uiState.update { it.copy(notice = null) }
    }

    private fun loadMetadata(requestedSession: String) {
        viewModelScope.launch {
            _uiState.update { it.copy(isLoadingMetadata = true) }
            repository.getRecordMetadata(recordIndex).onSuccess { meta ->
                if (repository.credentialsStore.sessionIdentity.value != requestedSession) return@onSuccess
                _uiState.update { it.copy(metadata = meta, isLoadingMetadata = false) }
            }.onFailure {
                _uiState.update { it.copy(isLoadingMetadata = false) }
            }
        }
    }

    /**
     * Loads the similar items once, when the information panel first opens:
     * a similarity search per photo swiped past would be wasted work.
     */
    fun loadSimilars() {
        val state = _uiState.value
        if (state.record == null || state.isLoadingSimilars || state.similarRecords.isNotEmpty()) return
        val requestedSession = repository.credentialsStore.sessionIdentity.value ?: return
        viewModelScope.launch {
            _uiState.update { it.copy(isLoadingSimilars = true) }
            repository.searchSimilar(recordIndex, topK = 15).onSuccess { resp ->
                if (repository.credentialsStore.sessionIdentity.value != requestedSession) return@onSuccess
                _uiState.update { it.copy(similarRecords = resp.results, isLoadingSimilars = false) }
            }.onFailure {
                _uiState.update { it.copy(isLoadingSimilars = false) }
            }
        }
    }

    private fun clearPrivateState() {
        _uiState.value = MediaDetailUiState(
            recordIndex = recordIndex,
            isLoading = false,
            error = "AUTH_REQUIRED",
        )
    }

    class Factory(
        private val recordIndex: Int,
        private val repository: IrisRepository,
        private val readDeviceMedia: (String) -> DeviceMediaDetails? = { null },
    ) : ViewModelProvider.Factory {
        @Suppress("UNCHECKED_CAST")
        override fun <T : ViewModel> create(modelClass: Class<T>): T {
            return MediaDetailViewModel(recordIndex, repository, readDeviceMedia) as T
        }
    }
}
