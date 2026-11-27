package com.iris.app.ui.screens.trash

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.GridItemSpan
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.outlined.Delete
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import com.iris.app.ui.text.countPluralResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import coil.compose.AsyncImage
import com.iris.app.IrisApplication
import com.iris.app.R
import com.iris.app.data.model.TrashedItem
import com.iris.app.ui.components.EmptyState
import com.iris.app.ui.text.appPlural
import com.iris.app.ui.text.appString
import com.iris.app.ui.theme.IrisAccent
import com.iris.app.ui.theme.IrisBackground
import com.iris.app.ui.theme.IrisSurfaceBright
import com.iris.app.ui.theme.IrisTextSoft

/** The server's trash, where a photo moved there by mistake comes back with one tap. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun TrashScreen(viewModel: TrashViewModel, onBack: () -> Unit) {
    val uiState by viewModel.uiState.collectAsStateWithLifecycle()
    val context = LocalContext.current
    val apiClient = (context.applicationContext as IrisApplication).apiClient
    var restoring by remember { mutableStateOf<TrashedItem?>(null) }
    var restoringAll by remember { mutableStateOf(false) }

    uiState.notice?.let { notice ->
        LaunchedEffect(notice) {
            android.widget.Toast.makeText(context, notice, android.widget.Toast.LENGTH_SHORT).show()
            viewModel.clearNotice()
        }
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text(stringResource(R.string.trash_title)) },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = stringResource(R.string.common_back))
                    }
                },
                actions = {
                    if (uiState.items.isNotEmpty()) {
                        TextButton(onClick = { restoringAll = true }) { Text(stringResource(R.string.trash_restore_all)) }
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(containerColor = IrisBackground),
            )
        },
        containerColor = IrisBackground,
    ) { padding ->
        Box(Modifier.fillMaxSize().padding(padding)) {
            when {
                uiState.isLoading && uiState.items.isEmpty() -> CircularProgressIndicator(
                    color = IrisAccent, modifier = Modifier.align(Alignment.Center)
                )
                uiState.error != null && uiState.items.isEmpty() -> EmptyState(
                    title = uiState.error ?: "",
                    message = stringResource(R.string.common_check_connection),
                    actionLabel = stringResource(R.string.common_retry),
                    onAction = viewModel::load,
                )
                uiState.items.isEmpty() -> EmptyState(
                    icon = Icons.Outlined.Delete,
                    title = stringResource(R.string.trash_empty_title),
                    message = countPluralResource(R.plurals.trash_empty_message, uiState.trashDays, uiState.trashDays),
                )
                else -> LazyVerticalGrid(
                    columns = GridCells.Fixed(3),
                    contentPadding = PaddingValues(8.dp),
                    horizontalArrangement = Arrangement.spacedBy(6.dp),
                    verticalArrangement = Arrangement.spacedBy(6.dp),
                ) {
                    item(span = { GridItemSpan(maxLineSpan) }) {
                        Text(
                            countPluralResource(R.plurals.trash_hint, uiState.trashDays, uiState.trashDays),
                            fontSize = 13.sp,
                            color = IrisTextSoft,
                            modifier = Modifier.padding(4.dp),
                        )
                    }
                    items(uiState.items, key = { it.id }) { item ->
                        Column(Modifier.clickable { restoring = item }) {
                            Box(
                                Modifier
                                    .fillMaxWidth()
                                    .aspectRatio(1f)
                                    .clip(RoundedCornerShape(12.dp))
                                    .background(IrisSurfaceBright)
                            ) {
                                item.thumbnailUrl?.let { url ->
                                    AsyncImage(
                                        model = apiClient.resolveThumbnailUrl(url),
                                        contentDescription = item.name,
                                        contentScale = ContentScale.Crop,
                                        modifier = Modifier.fillMaxSize(),
                                    )
                                }
                                Text(
                                    text = daysLeftLabel(daysLeft(item.purgeAfter)),
                                    fontSize = 11.sp,
                                    color = Color.White,
                                    modifier = Modifier
                                        .align(Alignment.BottomStart)
                                        .padding(4.dp)
                                        .background(Color.Black.copy(alpha = 0.55f), RoundedCornerShape(6.dp))
                                        .padding(horizontal = 5.dp, vertical = 2.dp),
                                )
                            }
                        }
                    }
                }
            }
        }
    }

    restoring?.let { item ->
        AlertDialog(
            onDismissRequest = { restoring = null },
            title = { Text(stringResource(R.string.trash_restore_one_title)) },
            text = { Text(stringResource(R.string.trash_restore_one_message), color = MaterialTheme.colorScheme.onSurfaceVariant) },
            confirmButton = {
                TextButton(onClick = { viewModel.restore(listOf(item.id)); restoring = null }) {
                    Text(stringResource(R.string.common_restore), color = IrisAccent)
                }
            },
            dismissButton = { TextButton(onClick = { restoring = null }) { Text(stringResource(R.string.common_cancel)) } },
        )
    }
    if (restoringAll) {
        AlertDialog(
            onDismissRequest = { restoringAll = false },
            title = { Text(stringResource(R.string.trash_restore_all_title)) },
            text = { Text(stringResource(R.string.trash_restore_all_message)) },
            confirmButton = {
                TextButton(onClick = { viewModel.restore(uiState.items.map { it.id }); restoringAll = false }) {
                    Text(stringResource(R.string.trash_restore_all), color = IrisAccent)
                }
            },
            dismissButton = { TextButton(onClick = { restoringAll = false }) { Text(stringResource(R.string.common_cancel)) } },
        )
    }
}

/** "Last day", "1 day", "12 days": how long until it is deleted for good. */
fun daysLeftLabel(days: Int): String = when (days) {
    0 -> appString(R.string.trash_last_day)
    else -> appPlural(R.plurals.trash_days_left, days, days)
}
