package com.iris.app.ui.screens.trash

import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import com.iris.app.R
import com.iris.app.data.LibraryChanges
import com.iris.app.data.model.TrashedItem
import com.iris.app.data.repository.IrisRepository
import com.iris.app.ui.text.appPlural
import com.iris.app.ui.text.appString
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import java.time.Duration
import java.time.OffsetDateTime

data class TrashUiState(
    val items: List<TrashedItem> = emptyList(),
    val trashDays: Int = 30,
    val isLoading: Boolean = true,
    val error: String? = null,
    val notice: String? = null,
)

/** Days until the server deletes a trashed item for good; 0 on its last day or when unknown. */
fun daysLeft(purgeAfter: String, now: OffsetDateTime = OffsetDateTime.now()): Int = runCatching {
    Duration.between(now, OffsetDateTime.parse(purgeAfter)).toDays().toInt().coerceAtLeast(0)
}.getOrDefault(0)

class TrashViewModel(private val repository: IrisRepository) : ViewModel() {
    private val _uiState = MutableStateFlow(TrashUiState())
    val uiState: StateFlow<TrashUiState> = _uiState.asStateFlow()

    init {
        load()
    }

    fun load() {
        viewModelScope.launch {
            _uiState.update { it.copy(isLoading = true, error = null) }
            repository.getTrash()
                .onSuccess { page ->
                    _uiState.update { it.copy(items = page.items, trashDays = page.trashDays, isLoading = false) }
                }
                .onFailure { _uiState.update { it.copy(isLoading = false, error = appString(R.string.trash_error_open)) } }
        }
    }

    fun restore(ids: List<Int>) {
        if (ids.isEmpty()) return
        viewModelScope.launch {
            repository.restoreTrash(ids)
                .onSuccess { restored ->
                    LibraryChanges.changed()
                    _uiState.update { state ->
                        state.copy(
                            items = state.items.filterNot { it.id in restored },
                            notice = when {
                                restored.isEmpty() -> appString(R.string.trash_error_restore)
                                restored.size == 1 -> appPlural(R.plurals.trash_restored, 1, 1)
                                else -> appPlural(R.plurals.trash_restored, restored.size, restored.size)
                            },
                        )
                    }
                }
                .onFailure { _uiState.update { it.copy(notice = appString(R.string.trash_error_restore)) } }
        }
    }

    fun clearNotice() {
        _uiState.update { it.copy(notice = null) }
    }

    class Factory(private val repository: IrisRepository) : ViewModelProvider.Factory {
        @Suppress("UNCHECKED_CAST")
        override fun <T : ViewModel> create(modelClass: Class<T>): T = TrashViewModel(repository) as T
    }
}
