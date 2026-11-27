package com.iris.app.ui.screens.spaces

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.ui.res.stringResource
import com.iris.app.R
import com.iris.app.data.model.SpaceAlbum
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.compose.material3.FilterChip
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.Close
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.lazy.grid.rememberLazyGridState
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Download
import androidx.compose.material.icons.filled.Group
import androidx.compose.material.icons.filled.LibraryAdd
import androidx.compose.material.icons.filled.PlayCircle
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FloatingActionButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.compose.runtime.derivedStateOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import coil.compose.AsyncImage
import com.iris.app.IrisApplication
import com.iris.app.data.model.SpaceItem
import com.iris.app.data.model.SpaceSummary
import com.iris.app.data.repository.IrisRepository
import com.iris.app.ui.components.EmptyState
import com.iris.app.ui.components.rememberMediaDownload
import com.iris.app.ui.theme.IrisDanger
import com.iris.app.ui.theme.IrisText
import com.iris.app.ui.theme.IrisOnAccent
import com.iris.app.ui.theme.IrisAccent
import com.iris.app.ui.theme.IrisBackground
import com.iris.app.ui.theme.IrisSurface
import com.iris.app.ui.theme.IrisTextMuted
import com.iris.app.ui.theme.IrisTextSoft

private fun roleLabelRes(role: String): Int? = when (role) {
    "manager" -> R.string.spaces_role_manager
    "contributor" -> R.string.spaces_role_contributor
    "viewer" -> R.string.spaces_role_viewer
    else -> null
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SpacesScreen(
    viewModel: SpacesViewModel,
    onSpaceClick: (Int, String) -> Unit,
    onBack: (() -> Unit)? = null,
) {
val uiState by viewModel.uiState.collectAsStateWithLifecycle()
    var creating by remember { mutableStateOf(false) }
    val snackbar = remember { SnackbarHostState() }

    LaunchedEffect(uiState.notice) {
        uiState.notice?.let { snackbar.showSnackbar(it); viewModel.showNotice(null) }
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Column {
                        Text(stringResource(R.string.spaces_title), fontSize = 18.sp, fontWeight = FontWeight.Bold)
                        Text(stringResource(R.string.spaces_subtitle), fontSize = 12.sp, color = IrisTextSoft)
                    }
                },
                navigationIcon = {
                    if (onBack != null) {
                        IconButton(onClick = onBack) {
                            Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = stringResource(R.string.common_back))
                        }
                    }
                },
                actions = {
                    IconButton(onClick = { viewModel.load() }) {
                        Icon(Icons.Default.Refresh, contentDescription = stringResource(R.string.common_refresh))
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(containerColor = IrisBackground),
            )
        },
        floatingActionButton = {
            FloatingActionButton(
                onClick = { creating = true },
                containerColor = IrisAccent,
                contentColor = IrisOnAccent,
            ) { Icon(Icons.Default.Add, contentDescription = stringResource(R.string.spaces_new_group)) }
        },
        snackbarHost = { SnackbarHost(snackbar) },
        containerColor = IrisBackground,
    ) { padding ->
        when {
            uiState.isLoading && uiState.spaces.isEmpty() -> Box(
                Modifier.fillMaxSize().padding(padding), contentAlignment = Alignment.Center,
            ) { CircularProgressIndicator(color = IrisAccent) }

            uiState.error != null && uiState.spaces.isEmpty() -> EmptyState(
                title = stringResource(R.string.spaces_error_list),
                message = uiState.error ?: "",
                actionLabel = stringResource(R.string.common_retry),
                onAction = { viewModel.load() },
                modifier = Modifier.padding(padding),
            )

            uiState.spaces.isEmpty() -> EmptyState(
                title = stringResource(R.string.spaces_empty_title),
                message = stringResource(R.string.spaces_empty_message),
                actionLabel = stringResource(R.string.spaces_create_action),
                onAction = { creating = true },
                modifier = Modifier.padding(padding),
            )

            else -> LazyColumn(
                contentPadding = PaddingValues(12.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp),
                modifier = Modifier.fillMaxSize().padding(padding),
            ) {
                items(uiState.spaces, key = { it.id }) { space ->
                    Card(
                        colors = CardDefaults.cardColors(containerColor = IrisSurface),
                        modifier = Modifier.fillMaxWidth().clickable { onSpaceClick(space.id, space.name) },
                    ) {
                        Row(Modifier.padding(16.dp), verticalAlignment = Alignment.CenterVertically) {
                            Icon(Icons.Default.Group, contentDescription = null, tint = IrisAccent)
                            Spacer(Modifier.size(14.dp))
                            Column {
                                Text(space.name, fontSize = 16.sp, fontWeight = FontWeight.SemiBold, color = IrisText)
                                Text(space.roleLabel, fontSize = 12.sp, color = IrisTextSoft)
                            }
                        }
                    }
                }
            }
        }
    }

    if (creating) {
        var name by remember { mutableStateOf("") }
        AlertDialog(
            onDismissRequest = { creating = false },
            title = { Text(stringResource(R.string.spaces_new_group)) },
            text = {
                OutlinedTextField(
                    value = name,
                    onValueChange = { name = it.take(120) },
                    label = { Text(stringResource(R.string.spaces_name_label)) },
                    placeholder = { Text(stringResource(R.string.spaces_name_placeholder)) },
                    singleLine = true,
                )
            },
            confirmButton = {
                TextButton(enabled = name.isNotBlank(), onClick = {
                    creating = false
                    viewModel.create(name) { space -> onSpaceClick(space.id, space.name) }
                }) { Text(stringResource(R.string.spaces_create_confirm)) }
            },
            dismissButton = { TextButton(onClick = { creating = false }) { Text(stringResource(R.string.common_cancel)) } },
        )
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SpaceScreen(
    viewModel: SpaceViewModel,
    onBack: () -> Unit,
) {
    val uiState by viewModel.uiState.collectAsStateWithLifecycle()
    val snackbar = remember { SnackbarHostState() }
    val gridState = rememberLazyGridState()
    var opened by remember { mutableStateOf<SpaceItem?>(null) }
    var showMembers by remember { mutableStateOf(false) }
    var confirmLeave by remember { mutableStateOf(false) }

    LaunchedEffect(uiState.notice) {
        uiState.notice?.let { snackbar.showSnackbar(it); viewModel.showNotice(null) }
    }
    LaunchedEffect(uiState.left) { if (uiState.left) onBack() }
    val nearEnd by remember {
        derivedStateOf {
            val last = gridState.layoutInfo.visibleItemsInfo.lastOrNull()?.index ?: 0
            val total = gridState.layoutInfo.totalItemsCount
            total > 0 && last >= total - 8
        }
    }
    LaunchedEffect(nearEnd) { if (nearEnd) viewModel.loadMore() }

    // One box so notices sit above the full-screen viewer as well.
    Box(Modifier.fillMaxSize()) {
    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Column {
                        Text(uiState.name, fontSize = 18.sp, fontWeight = FontWeight.Bold)
                        Text(uiState.space?.roleLabel ?: "", fontSize = 12.sp, color = IrisTextSoft)
                    }
                },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = stringResource(R.string.common_back))
                    }
                },
                actions = {
                    IconButton(onClick = { viewModel.loadMembers(); showMembers = true }) {
                        Icon(Icons.Default.Group, contentDescription = stringResource(R.string.spaces_members))
                    }
                    IconButton(onClick = { viewModel.refresh() }) {
                        Icon(Icons.Default.Refresh, contentDescription = stringResource(R.string.common_refresh))
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(containerColor = IrisBackground),
            )
        },
        containerColor = IrisBackground,
    ) { padding ->
        Column(Modifier.fillMaxSize().padding(padding)) {
            SpaceFilters(
                query = uiState.query,
                albums = uiState.albums,
                albumId = uiState.albumId,
                onSearch = viewModel::search,
                onAlbum = viewModel::showAlbum,
            )
            Box(Modifier.weight(1f)) {
                when {
                    uiState.isLoading && uiState.items.isEmpty() -> Box(
                        Modifier.fillMaxSize(), contentAlignment = Alignment.Center,
                    ) { CircularProgressIndicator(color = IrisAccent) }

                    uiState.error != null && uiState.items.isEmpty() -> EmptyState(
                        title = stringResource(R.string.spaces_error_open),
                        message = uiState.error ?: "",
                        actionLabel = stringResource(R.string.common_retry),
                        onAction = { viewModel.refresh() },
                    )

                    uiState.items.isEmpty() -> EmptyState(
                        title = when {
                            uiState.query.isNotEmpty() -> stringResource(R.string.spaces_nothing_found, uiState.query)
                            uiState.albumId != null -> stringResource(R.string.spaces_album_empty)
                            else -> stringResource(R.string.spaces_group_empty)
                        },
                        message = when {
                            uiState.query.isNotEmpty() -> stringResource(R.string.spaces_search_hint)
                            uiState.albumId != null -> stringResource(R.string.spaces_albums_hint)
                            uiState.space?.canAdd == true ->
                                stringResource(R.string.spaces_add_hint)
                            else -> stringResource(R.string.spaces_waiting_hint)
                        },
                    )

                    else -> LazyVerticalGrid(
                        state = gridState,
                        columns = GridCells.Adaptive(110.dp),
                        contentPadding = PaddingValues(8.dp),
                        horizontalArrangement = Arrangement.spacedBy(6.dp),
                        verticalArrangement = Arrangement.spacedBy(6.dp),
                        modifier = Modifier.fillMaxSize(),
                    ) {
                        items(uiState.items, key = { it.id }) { item ->
                            SpaceThumbnail(item = item, onClick = { opened = item })
                        }
                    }
                }
            }
        }
    }

    opened?.let { item ->
        SpaceItemViewer(
            item = item,
            onClose = { opened = null },
            onSave = { viewModel.saveToLibrary(item) },
            onRemove = { viewModel.remove(item) { opened = null } },
            onNotice = { viewModel.showNotice(it) },
        )
    }
    SnackbarHost(
        snackbar,
        Modifier.align(Alignment.BottomCenter).navigationBarsPadding().padding(bottom = 72.dp),
    )
    }

    if (showMembers) {
        ModalBottomSheet(onDismissRequest = { showMembers = false }, containerColor = IrisBackground) {
            Column(Modifier.padding(horizontal = 20.dp).padding(bottom = 24.dp)) {
                Text(stringResource(R.string.spaces_members), fontSize = 18.sp, fontWeight = FontWeight.Bold, color = IrisText)
                Spacer(Modifier.height(4.dp))
                Text(
                    stringResource(R.string.spaces_members_hint),
                    fontSize = 12.sp, color = IrisTextMuted,
                )
                Spacer(Modifier.height(12.dp))
                uiState.members.forEach { member ->
                    Row(Modifier.fillMaxWidth().padding(vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                        Column(Modifier.weight(1f)) {
                            Text(
                                (member.displayName.ifBlank { member.username }) + if (member.isYou) " " + stringResource(R.string.spaces_member_you) else "",
                                color = IrisText,
                            )
                            Text(member.username, fontSize = 12.sp, color = IrisTextMuted)
                        }
                        Text(roleLabelRes(member.role)?.let { stringResource(it) } ?: member.role, fontSize = 12.sp, color = IrisTextSoft)
                    }
                }
                Spacer(Modifier.height(16.dp))
                TextButton(onClick = { confirmLeave = true }) {
                    Text(stringResource(R.string.spaces_leave), color = IrisDanger)
                }
            }
        }
    }

    if (confirmLeave) {
        AlertDialog(
            onDismissRequest = { confirmLeave = false },
            title = { Text(stringResource(R.string.spaces_leave_title, uiState.name)) },
            text = {
                Text(stringResource(R.string.spaces_leave_message))
            },
            confirmButton = {
                TextButton(onClick = { confirmLeave = false; showMembers = false; viewModel.leave() }) {
                    Text(stringResource(R.string.spaces_leave_confirm))
                }
            },
            dismissButton = { TextButton(onClick = { confirmLeave = false }) { Text(stringResource(R.string.common_cancel)) } },
        )
    }
}

@Composable
private fun SpaceThumbnail(item: SpaceItem, onClick: () -> Unit) {
    val apiClient = (LocalContext.current.applicationContext as IrisApplication).apiClient
    Box(
        modifier = Modifier
            .fillMaxWidth()
            .aspectRatio(1f)
            .clip(RoundedCornerShape(8.dp))
            .background(IrisSurface)
            .clickable(onClick = onClick),
    ) {
        AsyncImage(
            model = apiClient.resolveThumbnailUrl(item.thumbnailUrl),
            contentDescription = item.name,
            contentScale = ContentScale.Crop,
            modifier = Modifier.fillMaxSize(),
        )
        if (item.isVideo) {
            Icon(
                Icons.Default.PlayCircle, contentDescription = stringResource(R.string.spaces_video), tint = Color.White,
                modifier = Modifier.align(Alignment.Center).size(32.dp),
            )
        }
    }
}

/** Full-screen view of one shared item, with what the role allows. */
@Composable
private fun SpaceItemViewer(
    item: SpaceItem,
    onClose: () -> Unit,
    onSave: () -> Unit,
    onRemove: () -> Unit,
    onNotice: (String) -> Unit,
) {
    val apiClient = (LocalContext.current.applicationContext as IrisApplication).apiClient
    val download = rememberMediaDownload(onNotice)
    var confirmRemove by remember { mutableStateOf(false) }
    BackHandler(onBack = onClose)

    Box(Modifier.fillMaxSize().background(Color.Black)) {
        AsyncImage(
            // Videos show their poster: playback is from the downloaded file.
            model = apiClient.resolveThumbnailUrl(if (item.isVideo) item.thumbnailUrl else item.originalUrl),
            contentDescription = item.name,
            contentScale = ContentScale.Fit,
            modifier = Modifier.fillMaxSize(),
        )
        Row(
            Modifier.fillMaxWidth().background(Color.Black.copy(alpha = 0.55f)).statusBarsPadding().padding(4.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            IconButton(onClick = onClose) {
                Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = stringResource(R.string.common_back), tint = Color.White)
            }
            Column {
                Text(item.name, color = Color.White, fontSize = 15.sp)
                item.addedByUsername?.let { Text(stringResource(R.string.spaces_added_by, it), color = IrisTextSoft, fontSize = 12.sp) }
            }
        }
        Row(
            Modifier.align(Alignment.BottomCenter).fillMaxWidth()
                .background(Color.Black.copy(alpha = 0.55f)).navigationBarsPadding().padding(vertical = 10.dp),
            horizontalArrangement = Arrangement.SpaceEvenly,
        ) {
            ViewerAction(Icons.Default.LibraryAdd, stringResource(R.string.spaces_action_save), onSave)
            ViewerAction(Icons.Default.Download, stringResource(R.string.spaces_action_download)) {
                download(apiClient.resolveThumbnailUrl(item.originalUrl), item.name)
            }
            if (item.canRemove) ViewerAction(Icons.Default.Delete, stringResource(R.string.common_remove)) { confirmRemove = true }
        }
    }

    if (confirmRemove) {
        AlertDialog(
            onDismissRequest = { confirmRemove = false },
            title = { Text(stringResource(R.string.spaces_remove_title)) },
            text = { Text(stringResource(R.string.spaces_remove_message)) },
            confirmButton = { TextButton(onClick = { confirmRemove = false; onRemove() }) { Text(stringResource(R.string.common_remove)) } },
            dismissButton = { TextButton(onClick = { confirmRemove = false }) { Text(stringResource(R.string.common_cancel)) } },
        )
    }
}

@Composable
private fun ViewerAction(icon: androidx.compose.ui.graphics.vector.ImageVector, label: String, onClick: () -> Unit) {
    Column(
        horizontalAlignment = Alignment.CenterHorizontally,
        modifier = Modifier.clickable(onClick = onClick).padding(horizontal = 12.dp, vertical = 4.dp),
    ) {
        Icon(icon, contentDescription = label, tint = Color.White, modifier = Modifier.size(22.dp))
        Spacer(Modifier.height(4.dp))
        Text(label, color = Color.White, fontSize = 11.sp)
    }
}

/**
 * "Send to a space" from a photo of the private library: lists only spaces
 * where this account may add. Sending is always this explicit action; nothing
 * reaches a space by itself.
 */
@Composable
fun SpacePickerDialog(
    repository: IrisRepository,
    onDismiss: () -> Unit,
    onPick: (SpaceSummary) -> Unit,
) {
    val loaded by produceState<Result<List<SpaceSummary>>?>(initialValue = null) {
        value = repository.getSpaces()
    }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(stringResource(R.string.spaces_send_title)) },
        text = {
            val result = loaded
            when {
                result == null -> CircularProgressIndicator(color = IrisAccent)
                result.isFailure -> Text(result.exceptionOrNull()?.localizedMessage ?: stringResource(R.string.spaces_error_load_groups))
                else -> {
                    val spaces = result.getOrDefault(emptyList()).filter { it.canAdd }
                    if (spaces.isEmpty()) {
                        Text(
                            stringResource(R.string.spaces_send_no_groups)
                        )
                    } else {
                        Column {
                            Text(
                                stringResource(R.string.spaces_send_copy_hint),
                                fontSize = 12.sp, color = IrisTextMuted,
                            )
                            Spacer(Modifier.height(8.dp))
                            spaces.forEach { space ->
                                Row(
                                    Modifier.fillMaxWidth().clickable { onPick(space) }.padding(vertical = 12.dp),
                                    verticalAlignment = Alignment.CenterVertically,
                                ) {
                                    Icon(Icons.Default.Group, contentDescription = null, tint = IrisAccent)
                                    Spacer(Modifier.size(12.dp))
                                    Column {
                                        Text(space.name, fontWeight = FontWeight.SemiBold)
                                        Text(space.roleLabel, fontSize = 12.sp, color = IrisTextSoft)
                                    }
                                }
                            }
                        }
                    }
                }
            }
        },
        confirmButton = {},
        dismissButton = { TextButton(onClick = onDismiss) { Text(stringResource(R.string.common_cancel)) } },
    )
}


/** Search the space, or narrow it to one album. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun SpaceFilters(
    query: String,
    albums: List<SpaceAlbum>,
    albumId: Int?,
    onSearch: (String) -> Unit,
    onAlbum: (Int?) -> Unit,
) {
    var text by remember(query) { mutableStateOf(query) }
    val keyboard = LocalSoftwareKeyboardController.current
    Column(Modifier.fillMaxWidth().padding(horizontal = 8.dp)) {
        OutlinedTextField(
            value = text,
            onValueChange = { text = it.take(300) },
            placeholder = { Text(stringResource(R.string.spaces_search_placeholder)) },
            singleLine = true,
            leadingIcon = { Icon(Icons.Default.Search, contentDescription = null) },
            trailingIcon = {
                if (text.isNotEmpty()) {
                    IconButton(onClick = { text = ""; onSearch("") }) {
                        Icon(Icons.Default.Close, contentDescription = stringResource(R.string.common_clear_search))
                    }
                }
            },
            keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search),
            keyboardActions = KeyboardActions(onSearch = { keyboard?.hide(); onSearch(text) }),
            modifier = Modifier.fillMaxWidth(),
        )
        if (albums.isNotEmpty()) {
            LazyRow(
                horizontalArrangement = Arrangement.spacedBy(6.dp),
                contentPadding = PaddingValues(vertical = 6.dp),
            ) {
                item(key = "all") {
                    FilterChip(
                        selected = albumId == null && query.isEmpty(),
                        onClick = { onAlbum(null) },
                        label = { Text(stringResource(R.string.common_all)) },
                    )
                }
                items(albums, key = { it.id }) { album ->
                    FilterChip(
                        selected = album.id == albumId,
                        onClick = { onAlbum(album.id) },
                        label = { Text("${album.name} · ${album.count}") },
                    )
                }
            }
        }
    }
}
