package com.iris.app.ui.screens.detail

import android.graphics.Color as AndroidColor
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.outlined.DriveFileRenameOutline
import androidx.compose.material.icons.outlined.FileDownload
import androidx.compose.material.icons.outlined.Info
import androidx.compose.material.icons.outlined.Share
import androidx.compose.animation.core.animate
import androidx.compose.foundation.background
import androidx.compose.ui.draw.clipToBounds
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.gestures.calculatePan
import androidx.compose.foundation.gestures.calculateZoom
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.pager.HorizontalPager
import androidx.compose.foundation.pager.rememberPagerState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.getValue
import androidx.compose.runtime.setValue
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.unit.toSize
import androidx.compose.ui.viewinterop.AndroidView
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.media3.common.MediaItem
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.source.DefaultMediaSourceFactory
import androidx.media3.ui.PlayerView
import coil.compose.AsyncImage
import coil.request.ImageRequest
import com.iris.app.IrisApplication
import com.iris.app.data.model.MediaRecord
import com.iris.app.data.remote.IrisApiClient
import com.iris.app.data.remote.IrisMediaDataSourceFactory
import com.iris.app.ui.components.EmptyState
import com.iris.app.ui.components.decodeThumbHash
import com.iris.app.ui.components.rememberMediaDownload
import com.iris.app.ui.components.rememberMediaShare
import com.iris.app.ui.screens.spaces.SpacePickerDialog
import com.iris.app.ui.theme.IrisAccent
import com.iris.app.ui.theme.IrisBackground
import kotlinx.coroutines.launch

/**
 * Full-bleed media viewer, behaving like the phone's own gallery.
 *
 * It opens with its controls showing, so it is plain what can be done; a tap
 * hides them. Swiping sideways goes to the next or previous item of the grid
 * it was opened from ([ViewerSequence]); a double tap zooms; dragging down
 * closes and dragging up opens the information panel. Every gesture also has
 * a visible button, for whoever does not know it.
 *
 * The information panel rises from the bottom and pushes the photo up rather
 * than covering it, so the photo it describes stays in view.
 */
@Composable
fun MediaDetailScreen(
    startIndex: Int,
    viewModelFor: @Composable (Int) -> MediaDetailViewModel,
    onBack: () -> Unit,
    onMediaClick: (Int) -> Unit,
    onPersonClick: (Int, String) -> Unit,
    onSearchText: (String) -> Unit,
) {
    val context = LocalContext.current
    val application = context.applicationContext as IrisApplication
    val apiClient = application.apiClient
    val sequence = remember(startIndex) { ViewerSequence.around(startIndex) }
    val pagerState = rememberPagerState(initialPage = sequence.indexOf(startIndex)) { sequence.size }

    var chromeVisible by remember { mutableStateOf(true) }
    var zoomed by remember { mutableStateOf(false) }
    var showDetails by remember { mutableStateOf(false) }
    var renaming by remember { mutableStateOf(false) }
    var pickingSpace by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()

    LaunchedEffect(pagerState.currentPage) { zoomed = false }

    val viewModel = viewModelFor(sequence[pagerState.currentPage])
    val uiState by viewModel.uiState.collectAsStateWithLifecycle()
    val download = rememberMediaDownload { viewModel.showNotice(it) }
    val share = rememberMediaShare { viewModel.showNotice(it) }
    val record = uiState.record
    val mediaUrl = record?.let { apiClient.resolveMediaUrl(it.resolvedPath ?: it.caminho) }

    uiState.notice?.let { notice ->
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
        panelOpen = showDetails && record != null,
        onClosePanel = { showDetails = false },
        chromeVisible = chromeVisible,
        topBar = {
            val title = remember(record, uiState.metadata) {
                ViewerTitle.of(
                    capturedAt = uiState.metadata?.curated?.textOf("captured_at"),
                    fileMtime = record?.fileMtime,
                    place = uiState.metadata?.curated?.textOf("location_label"),
                    fileName = record?.cleanFilename.orEmpty(),
                )
            }
            ViewerTopBar(
                title = title,
                onBack = onBack,
                onTitleClick = record?.let { { showDetails = true } },
                menu = if (record == null || mediaUrl == null) emptyList() else listOf(
                    ViewerAction(Icons.Outlined.Info, "Sobre") { showDetails = true },
                    ViewerAction(Icons.Outlined.DriveFileRenameOutline, "Renomear") { renaming = true },
                    ViewerAction(Icons.Outlined.FileDownload, "Salvar no celular") {
                        download(mediaUrl, record.cleanFilename)
                    },
                ),
            )
        },
        bottomBar = {
            if (record != null && mediaUrl != null) {
                ViewerActionBar(
                    listOf(
                        ViewerAction(Icons.Outlined.Share, "Compartilhar") { share(mediaUrl, record.cleanFilename) },
                        ViewerAction(Icons.Default.Add, "Adicionar a") { pickingSpace = true },
                        ViewerAction(Icons.Outlined.FileDownload, "Salvar no celular") {
                            download(mediaUrl, record.cleanFilename)
                        },
                        ViewerAction(Icons.Outlined.Info, "Informações") { showDetails = true },
                    )
                )
            }
        },
        panel = {
            MediaDetailsSheet(
                state = uiState,
                onPersonClick = onPersonClick,
                onMediaClick = { index ->
                    showDetails = false
                    ViewerSequence.set(uiState.similarRecords.map { it.index })
                    onMediaClick(index)
                },
                onSearchText = onSearchText,
            )
        },
        photo = { photoModifier ->
            HorizontalPager(
                state = pagerState,
                userScrollEnabled = !zoomed,
                key = { sequence[it] },
                beyondViewportPageCount = 1,
                modifier = photoModifier
            ) { page ->
                val pageViewModel = viewModelFor(sequence[page])
                val pageState by pageViewModel.uiState.collectAsStateWithLifecycle()
                val isCurrent = page == pagerState.currentPage
                MediaPage(
                    state = pageState,
                    apiClient = apiClient,
                    isCurrent = isCurrent,
                    onToggleChrome = { if (showDetails) showDetails = false else chromeVisible = !chromeVisible },
                    onZoomChanged = { if (isCurrent) zoomed = it },
                    onSwipe = { swipe ->
                        when (swipe) {
                            // With the panel up, dragging the photo down lowers the panel first.
                            ViewerGestures.Swipe.CLOSE -> if (showDetails) showDetails = false else onBack()
                            ViewerGestures.Swipe.SHOW_INFO -> showDetails = true
                            ViewerGestures.Swipe.NONE -> Unit
                        }
                    },
                    onRetry = { pageViewModel.loadDetail() }
                )
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

    if (renaming && record != null) {
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

/** One item of the pager: its loading and error states, and the media itself. */
@Composable
private fun MediaPage(
    state: MediaDetailUiState,
    apiClient: IrisApiClient,
    isCurrent: Boolean,
    onToggleChrome: () -> Unit,
    onZoomChanged: (Boolean) -> Unit,
    onSwipe: (ViewerGestures.Swipe) -> Unit,
    onRetry: () -> Unit,
) {
    val record = state.record
    when {
        record != null -> MediaStage(record, apiClient, isCurrent, onToggleChrome, onZoomChanged, onSwipe)
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

/**
 * The media itself, filling the screen. A tap toggles the controls, a double
 * tap zooms in or back out, two fingers zoom, and once zoomed one finger
 * pans. Unzoomed, a sideways drag is left to the pager and a vertical one
 * closes the viewer (down) or opens the information panel (up).
 */
@Composable
private fun MediaStage(
    record: MediaRecord,
    apiClient: IrisApiClient,
    isCurrent: Boolean,
    onToggleChrome: () -> Unit,
    onZoomChanged: (Boolean) -> Unit,
    onSwipe: (ViewerGestures.Swipe) -> Unit,
) {
    val context = LocalContext.current
    val fullMediaUrl = apiClient.resolveMediaUrl(record.resolvedPath ?: record.caminho)
    val scope = rememberCoroutineScope()
    val swipeThreshold = with(LocalDensity.current) { 96.dp.toPx() }

    var scale by remember(record.index) { mutableFloatStateOf(1f) }
    var offset by remember(record.index) { mutableStateOf(Offset.Zero) }
    var dragY by remember(record.index) { mutableFloatStateOf(0f) }
    var size by remember { mutableStateOf(Size.Zero) }
    var playing by remember(record.index) { mutableStateOf(false) }
    val zoomable = !record.isVideo

    // Leaving a page puts it back the way it opened.
    LaunchedEffect(isCurrent) {
        if (!isCurrent) {
            scale = 1f
            offset = Offset.Zero
            playing = false
        }
    }

    fun zoomTo(target: Float, point: Offset) {
        val startScale = scale
        val startOffset = offset
        scope.launch {
            animate(startScale, target) { value, _ ->
                scale = value
                offset = if (target > startScale) {
                    ViewerGestures.offsetKeeping(point, size, value)
                } else {
                    // Zooming out: shrink the translation along with the scale.
                    val progress = if (startScale > 1f) (value - 1f) / (startScale - 1f) else 0f
                    startOffset * progress
                }
            }
            onZoomChanged(scale > 1f)
        }
    }

    Box(
        modifier = Modifier
            .fillMaxSize()
            // A zoomed image stays inside the page, off the status bar and the next page.
            .clipToBounds()
            .onSizeChanged { size = it.toSize() }
            .pointerInput(record.index) {
                detectTapGestures(
                    onTap = { onToggleChrome() },
                    onDoubleTap = { point ->
                        if (!zoomable) return@detectTapGestures
                        if (scale > 1f) zoomTo(1f, point) else zoomTo(ViewerGestures.DOUBLE_TAP_SCALE, point)
                    }
                )
            }
            .pointerInput(record.index, playing) {
                if (playing) return@pointerInput
                awaitEachGesture {
                    awaitFirstDown(requireUnconsumed = false)
                    var total = Offset.Zero
                    var vertical = false
                    var transformed = false
                    var panning = false
                    do {
                        val event = awaitPointerEvent()
                        val pressed = event.changes.count { it.pressed }
                        when {
                            zoomable && pressed >= 2 -> {
                                scale = (scale * event.calculateZoom()).coerceIn(1f, ViewerGestures.MAX_SCALE)
                                offset = if (scale > 1f) {
                                    ViewerGestures.clampOffset(offset + event.calculatePan(), size, scale)
                                } else {
                                    Offset.Zero
                                }
                                transformed = true
                                onZoomChanged(scale > 1f)
                                event.changes.forEach { it.consume() }
                            }
                            scale > 1f -> {
                                total += event.calculatePan()
                                // A still finger is a tap (the double tap that zooms back out);
                                // only a real drag pans and is kept from the tap detector.
                                if (panning || total.getDistance() > viewConfiguration.touchSlop) {
                                    panning = true
                                    offset = ViewerGestures.clampOffset(offset + event.calculatePan(), size, scale)
                                    event.changes.forEach { it.consume() }
                                }
                            }
                            !transformed -> {
                                total += event.calculatePan()
                                if (!vertical && ViewerGestures.isVertical(total, viewConfiguration.touchSlop)) {
                                    vertical = true
                                }
                                if (vertical) {
                                    dragY = total.y
                                    event.changes.forEach { it.consume() }
                                }
                            }
                        }
                    } while (event.changes.any { it.pressed })
                    if (vertical) {
                        onSwipe(ViewerGestures.swipeVerdict(dragY, swipeThreshold))
                        val from = dragY
                        scope.launch { animate(from, 0f) { value, _ -> dragY = value } }
                    }
                }
            }
            .graphicsLayer {
                // Follows the finger down while closing; the panel takes over going up.
                translationY = dragY.coerceAtLeast(0f)
                alpha = 1f - (dragY.coerceAtLeast(0f) / (swipeThreshold * 4f)).coerceAtMost(0.5f)
            },
        contentAlignment = Alignment.Center
    ) {
        if (record.isVideo) {
            if (playing) {
                VideoStage(
                    mediaUrl = fullMediaUrl,
                    okHttpClient = apiClient.authenticatedOkHttpClient,
                )
            } else {
                // A still with a large play button: plain to see, and the pager
                // can still be swiped, which a live player view would capture.
                AsyncImage(
                    model = apiClient.resolveThumbnailUrl(record.thumbnailUrl),
                    contentDescription = record.cleanFilename,
                    contentScale = ContentScale.Fit,
                    modifier = Modifier.fillMaxSize()
                )
                Box(
                    modifier = Modifier
                        .size(80.dp)
                        .background(Color.Black.copy(alpha = 0.55f), CircleShape)
                        .pointerInput(Unit) { detectTapGestures(onTap = { playing = true }) },
                    contentAlignment = Alignment.Center
                ) {
                    Icon(
                        Icons.Default.PlayArrow,
                        contentDescription = "Reproduzir vídeo",
                        tint = Color.White,
                        modifier = Modifier.size(48.dp)
                    )
                }
            }
        } else {
            val placeholder = remember(record.thumbHash) { decodeThumbHash(record.thumbHash) }
            Box(
                modifier = Modifier
                    .fillMaxSize()
                    .graphicsLayer {
                        scaleX = scale
                        scaleY = scale
                        translationX = offset.x
                        translationY = offset.y
                    },
                contentAlignment = Alignment.Center
            ) {
                if (placeholder != null) {
                    androidx.compose.foundation.Image(
                        bitmap = placeholder,
                        contentDescription = null,
                        contentScale = ContentScale.Fit,
                        modifier = Modifier.fillMaxSize()
                    )
                }
                AsyncImage(
                    model = ImageRequest.Builder(context).data(fullMediaUrl).crossfade(true).build(),
                    contentDescription = record.cleanFilename,
                    contentScale = ContentScale.Fit,
                    modifier = Modifier.fillMaxSize()
                )
            }
        }
    }
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
