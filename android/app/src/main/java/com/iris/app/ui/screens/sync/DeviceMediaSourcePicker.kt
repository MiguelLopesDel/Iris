package com.iris.app.ui.screens.sync

import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.toggleable
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Search
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Checkbox
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.iris.app.R
import com.iris.app.data.model.DeviceMediaSource
import com.iris.app.ui.theme.IrisAccentInk
import com.iris.app.ui.theme.IrisAccentLime
import com.iris.app.ui.theme.IrisDarkSurfaceBright
import com.iris.app.ui.theme.IrisDanger
import com.iris.app.ui.theme.IrisTextSoft

@OptIn(ExperimentalMaterial3Api::class)
@Composable
internal fun DeviceMediaSourcePicker(
    sources: List<DeviceMediaSource>,
    selectedIds: Set<String>,
    isLoading: Boolean,
    hasLimitedMediaAccess: Boolean,
    errorMessage: String?,
    onToggleSource: (String, Boolean) -> Unit,
    onClearSelection: () -> Unit,
    onRefresh: () -> Unit,
    onRequestMediaAccess: () -> Unit,
    onDismiss: () -> Unit
) {
    var query by remember { mutableStateOf("") }
    var activeFilter by remember { mutableStateOf(DeviceMediaSourceFilter.ALL) }
    val visibleSources = remember(sources, query, activeFilter, selectedIds) {
        filterDeviceMediaSources(sources, query, activeFilter, selectedIds)
    }
    val sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)

    ModalBottomSheet(
        onDismissRequest = onDismiss,
        sheetState = sheetState
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .fillMaxHeight(0.92f)
                .padding(horizontal = 20.dp)
        ) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically
            ) {
                Column(modifier = Modifier.weight(1f)) {
                    Text(
                        text = stringResource(R.string.sync_source_picker_title),
                        fontSize = 19.sp,
                        fontWeight = FontWeight.Bold
                    )
                    Text(
                        text = stringResource(R.string.sync_source_picker_description),
                        fontSize = 13.sp,
                        color = IrisTextSoft
                    )
                }
                IconButton(onClick = onRefresh, enabled = !isLoading) {
                    Icon(
                        imageVector = Icons.Default.Refresh,
                        contentDescription = stringResource(R.string.sync_source_picker_refresh)
                    )
                }
                IconButton(onClick = onDismiss) {
                    Icon(
                        imageVector = Icons.Default.Close,
                        contentDescription = stringResource(R.string.sync_source_picker_close)
                    )
                }
            }

            if (hasLimitedMediaAccess) {
                Spacer(modifier = Modifier.height(12.dp))
                Card(
                    modifier = Modifier.fillMaxWidth(),
                    colors = CardDefaults.cardColors(containerColor = IrisDarkSurfaceBright),
                    shape = RoundedCornerShape(14.dp)
                ) {
                    Column(modifier = Modifier.padding(14.dp)) {
                        Text(
                            text = stringResource(R.string.sync_source_picker_limited_access_title),
                            fontWeight = FontWeight.SemiBold,
                            color = MaterialTheme.colorScheme.onSurface
                        )
                        Text(
                            text = stringResource(R.string.sync_source_picker_limited_access_message),
                            modifier = Modifier.padding(top = 4.dp),
                            fontSize = 13.sp,
                            color = IrisTextSoft
                        )
                        TextButton(
                            onClick = onRequestMediaAccess,
                            modifier = Modifier.align(Alignment.End)
                        ) {
                            Text(stringResource(R.string.sync_source_picker_manage_access))
                        }
                    }
                }
            }

            Spacer(modifier = Modifier.height(14.dp))
            Row(
                modifier = Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.SpaceBetween
            ) {
                Text(
                    text = stringResource(
                        R.string.sync_source_picker_counts,
                        selectedIds.size,
                        sources.size,
                        visibleSources.size
                    ),
                    fontSize = 13.sp,
                    fontWeight = FontWeight.SemiBold
                )
                TextButton(onClick = onClearSelection, enabled = selectedIds.isNotEmpty()) {
                    Text(stringResource(R.string.sync_source_picker_clear))
                }
            }

            OutlinedTextField(
                value = query,
                onValueChange = { query = it },
                modifier = Modifier.fillMaxWidth(),
                singleLine = true,
                placeholder = { Text(stringResource(R.string.sync_source_picker_search_hint)) },
                leadingIcon = { Icon(Icons.Default.Search, contentDescription = null) },
                trailingIcon = {
                    if (query.isNotEmpty()) {
                        IconButton(onClick = { query = "" }) {
                            Icon(
                                imageVector = Icons.Default.Close,
                                contentDescription = stringResource(R.string.sync_source_picker_clear_search)
                            )
                        }
                    }
                }
            )

            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .horizontalScroll(rememberScrollState()),
                horizontalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                SourceFilterChip(
                    label = stringResource(R.string.sync_source_filter_all),
                    selected = activeFilter == DeviceMediaSourceFilter.ALL
                ) { activeFilter = DeviceMediaSourceFilter.ALL }
                SourceFilterChip(
                    label = stringResource(R.string.sync_source_filter_photos),
                    selected = activeFilter == DeviceMediaSourceFilter.PHOTOS
                ) { activeFilter = DeviceMediaSourceFilter.PHOTOS }
                SourceFilterChip(
                    label = stringResource(R.string.sync_source_filter_videos),
                    selected = activeFilter == DeviceMediaSourceFilter.VIDEOS
                ) { activeFilter = DeviceMediaSourceFilter.VIDEOS }
                SourceFilterChip(
                    label = stringResource(R.string.sync_source_filter_selected),
                    selected = activeFilter == DeviceMediaSourceFilter.SELECTED
                ) { activeFilter = DeviceMediaSourceFilter.SELECTED }
            }

            HorizontalDivider(color = IrisDarkSurfaceBright)

            when {
                isLoading -> SourcePickerLoading()
                errorMessage != null -> Text(
                    text = errorMessage,
                    modifier = Modifier.padding(vertical = 20.dp),
                    color = IrisDanger
                )
                visibleSources.isEmpty() -> Text(
                    text = stringResource(
                        if (sources.isEmpty()) R.string.sync_source_picker_no_sources
                        else R.string.sync_source_picker_no_results
                    ),
                    modifier = Modifier.padding(vertical = 20.dp),
                    color = IrisTextSoft
                )
                else -> LazyColumn(
                    modifier = Modifier
                        .fillMaxWidth()
                        .weight(1f),
                    verticalArrangement = Arrangement.spacedBy(8.dp)
                ) {
                    items(visibleSources, key = { it.id }) { source ->
                        DeviceMediaSourceRow(
                            source = source,
                            checked = source.id in selectedIds,
                            onCheckedChange = { checked -> onToggleSource(source.id, checked) }
                        )
                    }
                }
            }

            Spacer(modifier = Modifier.height(12.dp))
            Button(
                onClick = onDismiss,
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(bottom = 20.dp),
                shape = RoundedCornerShape(14.dp),
                colors = androidx.compose.material3.ButtonDefaults.buttonColors(
                    containerColor = IrisAccentLime,
                    contentColor = IrisAccentInk
                )
            ) {
                Icon(Icons.Default.Check, contentDescription = null)
                Spacer(modifier = Modifier.width(8.dp))
                Text(stringResource(R.string.sync_source_picker_done), fontWeight = FontWeight.Bold)
            }
        }
    }
}

@Composable
private fun SourceFilterChip(label: String, selected: Boolean, onClick: () -> Unit) {
    FilterChip(
        selected = selected,
        onClick = onClick,
        label = { Text(label) }
    )
}

@Composable
private fun DeviceMediaSourceRow(
    source: DeviceMediaSource,
    checked: Boolean,
    onCheckedChange: (Boolean) -> Unit
) {
    val mediaLabel = stringResource(
        if (source.mediaKind == "video") R.string.sync_videos_lowercase
        else R.string.sync_photos_lowercase
    )
    val itemSummary = stringResource(R.string.sync_source_summary, source.itemCount, mediaLabel)
    Card(
        modifier = Modifier
            .fillMaxWidth()
            .testTag("device-media-source-${source.id}")
            .toggleable(value = checked, role = Role.Checkbox, onValueChange = onCheckedChange),
        shape = RoundedCornerShape(14.dp),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 12.dp, vertical = 8.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text = source.name,
                    fontWeight = FontWeight.SemiBold,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis
                )
                Text(text = itemSummary, fontSize = 12.sp, color = IrisTextSoft)
                if (source.relativePath.isNotBlank()) {
                    Text(
                        text = source.relativePath,
                        fontSize = 12.sp,
                        color = IrisTextSoft,
                        maxLines = 2,
                        overflow = TextOverflow.Ellipsis
                    )
                }
            }
            Checkbox(checked = checked, onCheckedChange = null)
        }
    }
}

@Composable
private fun SourcePickerLoading() {
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 20.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center
    ) {
        CircularProgressIndicator(color = IrisAccentLime, modifier = Modifier.size(28.dp))
        Spacer(modifier = Modifier.height(10.dp))
        Text(stringResource(R.string.sync_source_picker_loading), color = IrisTextSoft)
    }
}
