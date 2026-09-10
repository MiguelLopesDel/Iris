package com.iris.app.ui.screens.detail

import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import com.iris.app.data.model.MediaRecord
import com.iris.app.data.model.RecordMetadataResponse
import com.iris.app.data.repository.IrisRepository
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

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
    /** Mensagem curta para a tela mostrar e descartar (rename, download). */
    val notice: String? = null
)

class MediaDetailViewModel(
    private val recordIndex: Int,
    private val repository: IrisRepository
) : ViewModel() {

    private val _uiState = MutableStateFlow(MediaDetailUiState(recordIndex = recordIndex))
    val uiState: StateFlow<MediaDetailUiState> = _uiState.asStateFlow()

    init {
        loadDetail()
    }

    fun loadDetail() {
        viewModelScope.launch {
            _uiState.update { it.copy(isLoading = true, error = null) }
            repository.getRecordDetail(recordIndex).onSuccess { rec ->
                _uiState.update { it.copy(record = rec, isLoading = false, error = null) }
                loadMetadata()
                loadSimilars()
            }.onFailure { ex ->
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
        viewModelScope.launch {
            _uiState.update { it.copy(isRenaming = true, notice = null) }
            repository.renameRecord(record.index, newName)
                .onSuccess {
                    // O nome vem do servidor (extensão preservada, colisão
                    // resolvida), então recarrega em vez de adivinhar.
                    loadDetail()
                    _uiState.update { it.copy(isRenaming = false, notice = "Renomeado") }
                }
                .onFailure { ex ->
                    val motivo = when {
                        ex.localizedMessage?.contains("409") == true -> "Já existe um arquivo com esse nome"
                        ex.localizedMessage?.contains("400") == true -> "Nome inválido"
                        else -> "Não foi possível renomear"
                    }
                    _uiState.update { it.copy(isRenaming = false, notice = motivo) }
                }
        }
    }

    fun showNotice(message: String) {
        _uiState.update { it.copy(notice = message) }
    }

    fun clearNotice() {
        _uiState.update { it.copy(notice = null) }
    }

    private fun loadMetadata() {
        viewModelScope.launch {
            _uiState.update { it.copy(isLoadingMetadata = true) }
            repository.getRecordMetadata(recordIndex).onSuccess { meta ->
                _uiState.update { it.copy(metadata = meta, isLoadingMetadata = false) }
            }.onFailure {
                _uiState.update { it.copy(isLoadingMetadata = false) }
            }
        }
    }

    private fun loadSimilars() {
        viewModelScope.launch {
            _uiState.update { it.copy(isLoadingSimilars = true) }
            repository.searchSimilar(recordIndex, topK = 15).onSuccess { resp ->
                _uiState.update { it.copy(similarRecords = resp.results, isLoadingSimilars = false) }
            }.onFailure {
                _uiState.update { it.copy(isLoadingSimilars = false) }
            }
        }
    }

    class Factory(
        private val recordIndex: Int,
        private val repository: IrisRepository
    ) : ViewModelProvider.Factory {
        @Suppress("UNCHECKED_CAST")
        override fun <T : ViewModel> create(modelClass: Class<T>): T {
            return MediaDetailViewModel(recordIndex, repository) as T
        }
    }
}
