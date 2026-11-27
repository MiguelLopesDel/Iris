package com.iris.app.ui.screens.search

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.expandVertically
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.shrinkVertically
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Casino
import androidx.compose.material.icons.filled.Clear
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.Tune
import androidx.compose.material.icons.outlined.Search
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.FilterChipDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Slider
import androidx.compose.material3.SliderDefaults
import androidx.compose.material3.Tab
import androidx.compose.material3.TabRow
import androidx.compose.material3.TabRowDefaults
import androidx.compose.material3.TabRowDefaults.tabIndicatorOffset
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import com.iris.app.ui.text.countPluralResource
import androidx.compose.ui.res.stringResource
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.iris.app.R
import com.iris.app.ui.components.EmptyState
import com.iris.app.ui.components.MediaCard
import com.iris.app.ui.theme.IrisOnAccent
import com.iris.app.ui.theme.IrisAccent
import com.iris.app.ui.theme.IrisBackground
import com.iris.app.ui.theme.IrisSurface
import com.iris.app.ui.theme.IrisSurfaceBright
import com.iris.app.ui.theme.IrisTextMuted
import com.iris.app.ui.theme.IrisTextSoft
import com.iris.app.ui.theme.IrisViolet

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SearchScreen(
    viewModel: SearchViewModel,
    onMediaClick: (Int) -> Unit
) {
val uiState by viewModel.uiState.collectAsStateWithLifecycle()
    val focusManager = LocalFocusManager.current

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Text(
                        text = stringResource(R.string.search_title),
                        fontSize = 20.sp,
                        fontWeight = FontWeight.Bold
                    )
                },
                actions = {
                    IconButton(onClick = { viewModel.searchRandom() }) {
                        Icon(
                            imageVector = Icons.Default.Casino,
                            contentDescription = stringResource(R.string.search_random),
                            tint = IrisAccent
                        )
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(containerColor = IrisBackground)
            )
        },
        containerColor = IrisBackground
    ) { paddingValues ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(paddingValues)
        ) {
            // Mode Tabs: Semântica vs Nome de arquivo
            TabRow(
                selectedTabIndex = if (uiState.mode == SearchMode.SEMANTIC) 0 else 1,
                containerColor = IrisBackground,
                contentColor = IrisAccent,
                indicator = { tabPositions ->
                    TabRowDefaults.SecondaryIndicator(
                        Modifier.tabIndicatorOffset(
                            tabPositions[if (uiState.mode == SearchMode.SEMANTIC) 0 else 1]
                        ),
                        color = IrisAccent
                    )
                }
            ) {
                Tab(
                    selected = uiState.mode == SearchMode.SEMANTIC,
                    onClick = { viewModel.setMode(SearchMode.SEMANTIC) },
                    text = { Text(stringResource(R.string.search_mode_semantic), fontWeight = FontWeight.SemiBold) }
                )
                Tab(
                    selected = uiState.mode == SearchMode.FILENAME,
                    onClick = { viewModel.setMode(SearchMode.FILENAME) },
                    text = { Text(stringResource(R.string.search_mode_filename), fontWeight = FontWeight.SemiBold) }
                )
            }

            // Search input field + Filter toggle
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 12.dp, vertical = 8.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                OutlinedTextField(
                    value = uiState.query,
                    onValueChange = { viewModel.onQueryChange(it) },
                    placeholder = {
                        Text(
                            if (uiState.mode == SearchMode.SEMANTIC)
                                stringResource(R.string.search_placeholder_semantic)
                            else
                                stringResource(R.string.search_placeholder_filename)
                        )
                    },
                    leadingIcon = {
                        Icon(imageVector = Icons.Default.Search, contentDescription = null)
                    },
                    trailingIcon = {
                        if (uiState.query.isNotEmpty()) {
                            IconButton(onClick = { viewModel.onQueryChange("") }) {
                                Icon(imageVector = Icons.Default.Clear, contentDescription = stringResource(R.string.common_clear))
                            }
                        }
                    },
                    singleLine = true,
                    keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search),
                    keyboardActions = KeyboardActions(onSearch = {
                        focusManager.clearFocus()
                        viewModel.performSearch()
                    }),
                    shape = RoundedCornerShape(14.dp),
                    colors = OutlinedTextFieldDefaults.colors(
                        focusedContainerColor = IrisSurface,
                        unfocusedContainerColor = IrisSurface,
                        focusedBorderColor = IrisAccent,
                        unfocusedBorderColor = IrisSurfaceBright
                    ),
                    modifier = Modifier.weight(1f)
                )

                if (uiState.mode == SearchMode.SEMANTIC) {
                    Spacer(modifier = Modifier.width(8.dp))
                    IconButton(
                        onClick = { viewModel.toggleFilters() },
                        modifier = Modifier
                            .background(
                                if (uiState.showFilters) IrisAccent else IrisSurfaceBright,
                                RoundedCornerShape(12.dp)
                            )
                    ) {
                        Icon(
                            imageVector = Icons.Default.Tune,
                            contentDescription = stringResource(R.string.search_filters),
                            tint = if (uiState.showFilters) IrisOnAccent else IrisTextSoft
                        )
                    }
                }
            }

            // Collapsible semantic balance & filters
            AnimatedVisibility(
                visible = uiState.showFilters && uiState.mode == SearchMode.SEMANTIC,
                enter = expandVertically() + fadeIn(),
                exit = shrinkVertically() + fadeOut()
            ) {
                Card(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(horizontal = 12.dp, vertical = 4.dp),
                    shape = RoundedCornerShape(12.dp),
                    colors = CardDefaults.cardColors(containerColor = IrisSurface)
                ) {
                    SemanticBalanceControl(
                        balance = uiState.balance,
                        onBalanceChangeFinished = { newBalance ->
                            viewModel.setBalance(newBalance)
                            viewModel.performSearch()
                        },
                        modifier = Modifier.padding(14.dp)
                    )
                }
            }

            // Media type filters row
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 12.dp, vertical = 4.dp),
                horizontalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                FilterChip(
                    selected = uiState.mediaType == "all",
                    onClick = { viewModel.setMediaType("all") },
                    label = { Text(stringResource(R.string.common_all)) },
                    colors = FilterChipDefaults.filterChipColors(
                        selectedContainerColor = IrisAccent,
                        selectedLabelColor = IrisOnAccent
                    )
                )
                FilterChip(
                    selected = uiState.mediaType == "image",
                    onClick = { viewModel.setMediaType("image") },
                    label = { Text(stringResource(R.string.common_images)) },
                    colors = FilterChipDefaults.filterChipColors(
                        selectedContainerColor = IrisAccent,
                        selectedLabelColor = IrisOnAccent
                    )
                )
                FilterChip(
                    selected = uiState.mediaType == "video",
                    onClick = { viewModel.setMediaType("video") },
                    label = { Text(stringResource(R.string.common_videos)) },
                    colors = FilterChipDefaults.filterChipColors(
                        selectedContainerColor = IrisAccent,
                        selectedLabelColor = IrisOnAccent
                    )
                )

                if (uiState.results.isNotEmpty()) {
                    Spacer(modifier = Modifier.weight(1f))
                    Text(
                        text = countPluralResource(R.plurals.search_result_count, uiState.total, uiState.total),
                        fontSize = 12.sp,
                        color = IrisTextSoft,
                        modifier = Modifier.align(Alignment.CenterVertically)
                    )
                }
            }

            // Results area
            when {
                uiState.isSearching -> {
                    Box(
                        modifier = Modifier.fillMaxSize(),
                        contentAlignment = Alignment.Center
                    ) {
                        Column(horizontalAlignment = Alignment.CenterHorizontally) {
                            CircularProgressIndicator(color = IrisAccent)
                            Spacer(modifier = Modifier.height(12.dp))
                            Text(
                                text = stringResource(R.string.search_loading),
                                color = IrisTextSoft,
                                fontSize = 14.sp
                            )
                        }
                    }
                }

                uiState.error != null -> {
                    EmptyState(
                        title = stringResource(R.string.search_failed),
                        message = uiState.error ?: "",
                        actionLabel = stringResource(R.string.common_retry),
                        onAction = { viewModel.performSearch() }
                    )
                }

                !uiState.hasSearched -> {
                    EmptyState(
                        icon = Icons.Outlined.Search,
                        title = stringResource(R.string.search_empty_title),
                        message = stringResource(R.string.search_empty_message),
                        actionLabel = stringResource(R.string.search_see_random),
                        onAction = { viewModel.searchRandom() }
                    )
                }

                uiState.results.isEmpty() -> {
                    EmptyState(
                        title = stringResource(R.string.search_no_results_title),
                        message = stringResource(R.string.search_no_results_message, uiState.query),
                        actionLabel = stringResource(R.string.common_clear_search),
                        onAction = { viewModel.onQueryChange("") }
                    )
                }

                else -> {
                    LazyVerticalGrid(
                        columns = GridCells.Adaptive(110.dp),
                        contentPadding = PaddingValues(8.dp),
                        horizontalArrangement = Arrangement.spacedBy(6.dp),
                        verticalArrangement = Arrangement.spacedBy(6.dp),
                        modifier = Modifier.fillMaxSize()
                    ) {
                        items(
                            items = uiState.results,
                            key = { record -> record.index }
                        ) { record ->
                            MediaCard(
                                record = record,
                                onClick = { onMediaClick(record.index) }
                            )
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun SemanticBalanceControl(
    balance: Float,
    onBalanceChangeFinished: (Float) -> Unit,
    modifier: Modifier = Modifier
) {
    var localBalance by remember(balance) { mutableFloatStateOf(balance) }

    Column(modifier = modifier) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween
        ) {
            Text(
                text = stringResource(R.string.search_balance_label),
                fontSize = 13.sp,
                fontWeight = FontWeight.SemiBold,
                color = IrisTextSoft
            )
            val balanceLabel = when {
                localBalance < 0.35f -> stringResource(R.string.search_balance_visual)
                localBalance > 0.65f -> stringResource(R.string.search_balance_conceptual)
                else -> stringResource(R.string.search_balance_even)
            }
            Text(
                text = balanceLabel,
                fontSize = 13.sp,
                color = IrisAccent,
                fontWeight = FontWeight.Medium
            )
        }

        Slider(
            value = localBalance,
            onValueChange = { localBalance = it },
            onValueChangeFinished = { onBalanceChangeFinished(localBalance) },
            valueRange = 0.0f..1.0f,
            colors = SliderDefaults.colors(
                thumbColor = IrisAccent,
                activeTrackColor = IrisAccent,
                inactiveTrackColor = IrisSurfaceBright
            )
        )

        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween
        ) {
            Text(stringResource(R.string.search_balance_visual_end), fontSize = 11.sp, color = IrisTextMuted)
            Text(stringResource(R.string.search_balance_conceptual_end), fontSize = 11.sp, color = IrisTextMuted)
        }
    }
}
