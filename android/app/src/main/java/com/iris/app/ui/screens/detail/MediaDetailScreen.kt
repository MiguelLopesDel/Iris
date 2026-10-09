package com.iris.app.ui.screens.detail

import android.app.Activity
import android.graphics.Color as AndroidColor
import android.net.Uri
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.pager.HorizontalPager
import androidx.compose.foundation.pager.rememberPagerState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.outlined.DriveFileRenameOutline
import androidx.compose.material.icons.outlined.FileDownload
import androidx.compose.material.icons.outlined.Info
import androidx.compose.material.icons.outlined.Share
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.SideEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.luminance
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.view.WindowCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.media3.common.MediaItem
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.source.DefaultMediaSourceFactory
import androidx.media3.ui.PlayerView
import com.iris.app.IrisApplication
import com.iris.app.data.local.DeviceBackupState
import com.iris.app.data.local.DeviceMediaDetails
import com.iris.app.data.remote.IrisApiClient
import com.iris.app.data.remote.IrisMediaDataSourceFactory
import com.iris.app.ui.components.EmptyState
import com.iris.app.ui.components.decodeThumbHash
import com.iris.app.ui.components.rememberMediaDownload
import com.iris.app.ui.components.rememberMediaShare
import com.iris.app.ui.screens.gallery.rememberNotInBackupNotice
import com.iris.app.ui.screens.spaces.SpacePickerDialog
import com.iris.app.ui.theme.IrisAccent
import com.iris.app.ui.theme.IrisBackground
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/** Identity for the device-backup lookup shown by the information panel. */
internal data class DeviceBackupLookup(val uri: String, val accountIdentity: String, val panelRevision: Int)

internal data class DeviceBackupSnapshot(val lookup: DeviceBackupLookup, val state: DeviceBackupState)

internal fun deviceBackupLookup(
    uri: String?,
    accountIdentity: String?,
    panelOpen: Boolean,
    panelRevision: Int,
): DeviceBackupLookup? = if (panelOpen && uri != null && accountIdentity != null) {
    DeviceBackupLookup(uri, accountIdentity, panelRevision)
} else {
    null
}

internal fun visibleDeviceBackupState(
    lookup: DeviceBackupLookup?,
    snapshot: DeviceBackupSnapshot?,
    currentAccountIdentity: String?,
): DeviceBackupState? {
    if (lookup?.accountIdentity != currentAccountIdentity) return null
    return snapshot?.takeIf { it.lookup == lookup }?.state
}

/**
 * Full-bleed media viewer, behaving like the phone's own gallery.
 *
 * It opens with its controls showing, so it is plain what can be done; a tap
 * hides them. Swiping sideways goes through the grid it was opened from
 * ([ViewerSequence]), Iris's items and the phone's own alike; the gestures
 * are those of [ZoomableMedia]. Every gesture also has a visible button, for
 * whoever does not know it.
 *
 * The information panel rises from the bottom and pushes the photo up rather
 * than covering it, so the photo it describes stays in view. It and the bars
 * follow the item shown: Iris's has its catalog, actions and similar photos;
 * a phone's file has what the media store knows and where it stands with the
 * backup.
 */
@Composable
internal fun MediaDetailScreen(
    start: ViewerItem,
    viewModelFor: @Composable (Int) -> MediaDetailViewModel,
    onBack: () -> Unit,
    onMediaClick: (Int) -> Unit,
    onPersonClick: (Int, String) -> Unit,
    onSearchText: (String) -> Unit,
) {
    val context = LocalContext.current
    val application = context.applicationContext as IrisApplication
    val apiClient = application.apiClient
    val accountIdentity by application.credentialsStore.accountIdentity.collectAsStateWithLifecycle()
    val sequence = remember(start) { ViewerSequence.around(start) }
    val pagerState = rememberPagerState(initialPage = sequence.indexOf(start).coerceAtLeast(0)) { sequence.size }

    var chromeVisible by remember { mutableStateOf(true) }
    var zoomed by remember { mutableStateOf(false) }
    var showDetails by remember { mutableStateOf(false) }
    var detailPanelRevision by remember { mutableStateOf(0) }
    var renaming by remember { mutableStateOf(false) }
    var pickingSpace by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()

    fun openDetails() {
        if (!showDetails) detailPanelRevision++
        showDetails = true
    }

    LaunchedEffect(pagerState.currentPage) { zoomed = false }
    // Light icons over the black viewer; the navigation bar's follow the panel when it is up.
    val lightPanel = MaterialTheme.colorScheme.surface.luminance() > 0.5f
    ViewerSystemBarIcons(darkNavigationIcons = showDetails && lightPanel)

    val current = sequence[pagerState.currentPage]

    // The current item, when it is Iris's.
    val viewModel = (current as? ViewerItem.Server)?.let { viewModelFor(it.index) }
    val uiState = viewModel?.uiState?.collectAsStateWithLifecycle()?.value
    val record = uiState?.record
    val mediaUrl = record?.let { apiClient.resolveMediaUrl(it.resolvedPath ?: it.caminho) }
    val download = rememberMediaDownload { message -> viewModel?.showNotice(message) }
    val share = rememberMediaShare { message -> viewModel?.showNotice(message) }

    // The current item, when it is only on the phone.
    val deviceUri = (current as? ViewerItem.Device)?.uri
    val deviceDetails by produceState<DeviceMediaDetails?>(initialValue = null, deviceUri) {
        value = deviceUri?.let { uri ->
            withContext(Dispatchers.IO) { DeviceMediaDetails.read(context.contentResolver, Uri.parse(uri)) }
        }
    }
    val notInBackup = deviceUri?.let { rememberNotInBackupNotice(application, it) }
    val backupLookup = deviceBackupLookup(deviceUri, accountIdentity, showDetails, detailPanelRevision)
    val backupSnapshot by produceState<DeviceBackupSnapshot?>(initialValue = null, backupLookup) {
        value = null
        val lookup = backupLookup ?: return@produceState
        val jobs = runCatching {
            withContext(Dispatchers.IO) { application.irisRepository.getUploadQueue(lookup.accountIdentity) }
        }.getOrElse { emptyList() }
        if (application.credentialsStore.accountIdentity.value == lookup.accountIdentity) {
            value = DeviceBackupSnapshot(lookup, DeviceBackupState.of(lookup.uri, jobs))
        }
    }
    val deviceBackup = visibleDeviceBackupState(
        backupLookup,
        backupSnapshot,
        application.credentialsStore.accountIdentity.value,
    )

    uiState?.notice?.let { notice ->
        LaunchedEffect(notice) {
            android.widget.Toast.makeText(context, notice, android.widget.Toast.LENGTH_SHORT).show()
            viewModel.clearNotice()
        }
    }
    if (showDetails && record != null) {
        LaunchedEffect(record.index) {
            viewModel.loadSimilars()
            viewModel.loadBackupState()
        }
    }

    ViewerScaffold(
        panelOpen = showDetails && (record != null || deviceDetails != null),
        onClosePanel = { showDetails = false },
        chromeVisible = chromeVisible,
        topBar = {
            val details = deviceDetails
            val title = when {
                record != null -> ViewerTitle.of(
                    capturedAt = uiState.metadata?.curated?.textOf("captured_at"),
                    fileMtime = record.fileMtime,
                    place = uiState.metadata?.curated?.textOf("location_label"),
                    fileName = record.cleanFilename,
                )
                details != null -> ViewerTitle.of(
                    capturedAt = null,
                    fileMtime = details.takenAtMillis?.let { it / 1000.0 },
                    place = null,
                    fileName = details.name,
                )
                else -> ViewerTitle("", null)
            }
            ViewerTopBar(
                title = title,
                onBack = onBack,
                onTitleClick = if (record != null || details != null) ({ openDetails() }) else null,
                menu = when {
                    record != null && mediaUrl != null -> listOf(
                        ViewerAction(Icons.Outlined.Info, "Sobre") { openDetails() },
                        ViewerAction(Icons.Outlined.DriveFileRenameOutline, "Renomear") { renaming = true },
                        ViewerAction(Icons.Outlined.FileDownload, "Salvar no celular") {
                            download(mediaUrl, record.cleanFilename)
                        },
                    )
                    details != null -> listOf(ViewerAction(Icons.Outlined.Info, "Sobre") { openDetails() })
                    else -> emptyList()
                },
            )
        },
        bottomBar = {
            when {
                record != null && mediaUrl != null -> ViewerActionBar(
                    listOf(
                        ViewerAction(Icons.Outlined.Share, "Compartilhar") { share(mediaUrl, record.cleanFilename) },
                        ViewerAction(Icons.Default.Add, "Adicionar a") { pickingSpace = true },
                        ViewerAction(Icons.Outlined.FileDownload, "Salvar no celular") {
                            download(mediaUrl, record.cleanFilename)
                        },
                        ViewerAction(Icons.Outlined.Info, "Informações") { openDetails() },
                    )
                )
                deviceUri != null -> Column {
                    // Why it will not reach Iris, where it can be fixed at once.
                    notInBackup?.let { NotInBackupBanner(it) }
                    ViewerActionBar(
                        listOf(
                            ViewerAction(Icons.Outlined.Share, "Compartilhar") {
                                shareDeviceMedia(context, Uri.parse(deviceUri), deviceDetails?.mimeType)
                            },
                            ViewerAction(Icons.Outlined.Info, "Informações") { openDetails() },
                        )
                    )
                }
            }
        },
        panel = {
            val details = deviceDetails
            when {
                record != null && uiState != null -> MediaDetailsSheet(
                    state = uiState,
                    onPersonClick = onPersonClick,
                    onMediaClick = { index ->
                        showDetails = false
                        ViewerSequence.setRecords(uiState.similarRecords)
                        onMediaClick(index)
                    },
                    onSearchText = onSearchText,
                )
                details != null -> DeviceMediaSheet(details, deviceBackup, notInBackup)
            }
        },
        photo = { photoModifier ->
            HorizontalPager(
                state = pagerState,
                userScrollEnabled = !zoomed,
                key = { sequence[it].key },
                beyondViewportPageCount = 1,
                modifier = photoModifier
            ) { page ->
                val isCurrent = page == pagerState.currentPage
                val gestures = PageGestures(
                    onTap = { if (showDetails) showDetails = false else chromeVisible = !chromeVisible },
                    onZoomChanged = { if (isCurrent) zoomed = it },
                    onSwipe = { swipe ->
                        when (swipe) {
                            // With the panel up, dragging the photo down lowers the panel first.
                            ViewerGestures.Swipe.CLOSE -> if (showDetails) showDetails = false else onBack()
                            ViewerGestures.Swipe.SHOW_INFO -> openDetails()
                            ViewerGestures.Swipe.NONE -> Unit
                        }
                    },
                    onTurnPage = { step ->
                        val target = (pagerState.currentPage + step).coerceIn(0, sequence.size - 1)
                        scope.launch { pagerState.animateScrollToPage(target) }
                    },
                )
                when (val item = sequence[page]) {
                    is ViewerItem.Server -> {
                        val pageViewModel = viewModelFor(item.index)
                        val pageState by pageViewModel.uiState.collectAsStateWithLifecycle()
                        ServerPage(item, pageState, apiClient, isCurrent, gestures, onRetry = { pageViewModel.loadDetail() })
                    }
                    is ViewerItem.Device -> DevicePage(item, isCurrent, gestures)
                }
            }
        },
    )

    if (pickingSpace && record != null) {
        SpacePickerDialog(
            repository = application.irisRepository,
            onDismiss = { pickingSpace = false },
            onPick = { space ->
                pickingSpace = false
                val dbId = record.dbId
                if (dbId == null) {
                    viewModel.showNotice("Esta mídia ainda não pode ser enviada")
                } else {
                    scope.launch {
                        application.irisRepository.addToSpace(space.id, dbId)
                            .onSuccess { added ->
                                viewModel.showNotice(
                                    if (added.created) "Enviada para ${space.name}"
                                    else "Já estava em ${space.name}"
                                )
                            }
                            .onFailure { e ->
                                viewModel.showNotice(e.localizedMessage ?: "Não foi possível enviar")
                            }
                    }
                }
            }
        )
    }

    if (renaming && record != null && uiState != null) {
        RenameDialog(
            currentName = record.cleanFilename,
            isWorking = uiState.isRenaming,
            onDismiss = { renaming = false },
            onConfirm = { novo ->
                renaming = false
                viewModel.rename(novo)
            }
        )
    }
}

/** What every page reports up to the viewer. */
private class PageGestures(
    val onTap: () -> Unit,
    val onZoomChanged: (Boolean) -> Unit,
    val onSwipe: (ViewerGestures.Swipe) -> Unit,
    val onTurnPage: (Int) -> Unit,
)

/** An item of Iris: its loading and error states, and the media itself. */
@Composable
private fun ServerPage(
    item: ViewerItem.Server,
    state: MediaDetailUiState,
    apiClient: IrisApiClient,
    isCurrent: Boolean,
    gestures: PageGestures,
    onRetry: () -> Unit,
) {
    val record = state.record
    when {
        record != null -> {
            val mediaUrl = apiClient.resolveMediaUrl(record.resolvedPath ?: record.caminho)
            val placeholder = remember(record.thumbHash) { decodeThumbHash(record.thumbHash) }
            ZoomableMedia(
                content = MediaContent(
                    key = item.key,
                    image = if (record.isVideo) apiClient.resolveThumbnailUrl(record.thumbnailUrl) else mediaUrl,
                    placeholder = placeholder,
                    isVideo = record.isVideo,
                    description = record.cleanFilename,
                ),
                isCurrent = isCurrent,
                onTap = gestures.onTap,
                onZoomChanged = gestures.onZoomChanged,
                onSwipe = gestures.onSwipe,
                onTurnPage = gestures.onTurnPage,
                video = { VideoStage(mediaUrl = mediaUrl, okHttpClient = apiClient.authenticatedOkHttpClient) },
            )
        }
        state.error != null -> EmptyState(
            title = "Erro ao carregar",
            message = state.error,
            actionLabel = "Tentar novamente",
            onAction = onRetry
        )
        else -> Box(Modifier.fillMaxSize(), Alignment.Center) {
            CircularProgressIndicator(color = IrisAccent)
        }
    }
}

/** An item only on the phone, read straight from its media store URI. */
@Composable
private fun DevicePage(item: ViewerItem.Device, isCurrent: Boolean, gestures: PageGestures) {
    val context = LocalContext.current
    val uri = remember(item.uri) { Uri.parse(item.uri) }
    val video = remember(item.uri) { isVideo(context, uri) }
    ZoomableMedia(
        // Coil draws a video's frame from its content URI.
        content = MediaContent(key = item.key, image = uri, isVideo = video, description = "Mídia do aparelho"),
        isCurrent = isCurrent,
        onTap = gestures.onTap,
        onZoomChanged = gestures.onZoomChanged,
        onSwipe = gestures.onSwipe,
        onTurnPage = gestures.onTurnPage,
        video = { LocalVideoPlayer(uri) },
    )
}

// Custom data source and shutter colour are Media3 APIs marked unstable.
@androidx.annotation.OptIn(markerClass = [androidx.media3.common.util.UnstableApi::class])
@Composable
private fun VideoStage(
    mediaUrl: String,
    okHttpClient: okhttp3.OkHttpClient,
) {
    val context = LocalContext.current
    val lifecycleOwner = LocalLifecycleOwner.current

    val exoPlayer = remember(mediaUrl) {
        ExoPlayer.Builder(context)
            .setMediaSourceFactory(
                DefaultMediaSourceFactory(IrisMediaDataSourceFactory(okHttpClient))
            )
            .build()
            .apply {
                setMediaItem(MediaItem.fromUri(mediaUrl))
                prepare()
                // Shown only after the play button was pressed.
                playWhenReady = true
            }
    }

    DisposableEffect(lifecycleOwner, exoPlayer) {
        val observer = LifecycleEventObserver { _, event ->
            when (event) {
                Lifecycle.Event.ON_PAUSE -> exoPlayer.pause()
                else -> Unit
            }
        }
        lifecycleOwner.lifecycle.addObserver(observer)
        onDispose {
            lifecycleOwner.lifecycle.removeObserver(observer)
            exoPlayer.release()
        }
    }

    AndroidView(
        factory = { ctx ->
            PlayerView(ctx).apply {
                player = exoPlayer
                useController = true
                setShutterBackgroundColor(AndroidColor.TRANSPARENT)
            }
        },
        modifier = Modifier.fillMaxSize()
    )
}

@Composable
private fun RenameDialog(
    currentName: String,
    isWorking: Boolean,
    onDismiss: () -> Unit,
    onConfirm: (String) -> Unit,
) {
    // The server keeps the extension, so editing it here would be a lie.
    var value by remember(currentName) {
        mutableStateOf(currentName.substringBeforeLast('.', currentName))
    }
    androidx.compose.material3.AlertDialog(
        onDismissRequest = onDismiss,
        containerColor = IrisBackground,
        title = { Text("Renomear", color = Color.White) },
        text = {
            Column {
                OutlinedTextField(
                    value = value,
                    onValueChange = { value = it },
                    singleLine = true,
                    keyboardOptions = KeyboardOptions(imeAction = ImeAction.Done),
                    label = { Text("Nome") }
                )
                Spacer(Modifier.height(8.dp))
                Text(
                    "A extensão é mantida pelo servidor.",
                    fontSize = 12.sp,
                    color = Color.White.copy(alpha = 0.6f)
                )
            }
        },
        confirmButton = {
            TextButton(
                onClick = { onConfirm(value.trim()) },
                enabled = !isWorking && value.isNotBlank()
            ) { Text("Renomear", color = IrisAccent) }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) { Text("Cancelar", color = Color.White) }
        }
    )
}

/**
 * Light status bar icons while the viewer, black in every theme, is shown, and
 * navigation bar icons that suit what lies under them; the app's own come back
 * when it closes.
 */
@Composable
private fun ViewerSystemBarIcons(darkNavigationIcons: Boolean) {
    val view = LocalView.current
    val controller = remember(view) {
        (view.context as? Activity)?.window?.let { WindowCompat.getInsetsController(it, view) }
    }
    DisposableEffect(controller) {
        val status = controller?.isAppearanceLightStatusBars
        val navigation = controller?.isAppearanceLightNavigationBars
        controller?.isAppearanceLightStatusBars = false
        onDispose {
            status?.let { controller.isAppearanceLightStatusBars = it }
            navigation?.let { controller.isAppearanceLightNavigationBars = it }
        }
    }
    SideEffect { controller?.isAppearanceLightNavigationBars = darkNavigationIcons }
}
