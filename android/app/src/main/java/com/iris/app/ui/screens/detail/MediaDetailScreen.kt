package com.iris.app.ui.screens.detail

import android.content.Intent
import android.graphics.Color as AndroidColor
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.slideInVertically
import androidx.compose.animation.slideOutVertically
import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.gestures.rememberTransformableState
import androidx.compose.foundation.gestures.transformable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Download
import androidx.compose.material.icons.filled.DriveFileRenameOutline
import androidx.compose.material.icons.filled.Info
import androidx.compose.material.icons.filled.Share
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.media3.common.MediaItem
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.source.DefaultMediaSourceFactory
import androidx.media3.ui.PlayerView
import coil.compose.AsyncImage
import coil.request.ImageRequest
import com.iris.app.IrisApplication
import com.iris.app.data.MediaDownloader
import com.iris.app.data.model.MediaRecord
import com.iris.app.data.remote.IrisMediaDataSourceFactory
import com.iris.app.ui.components.EmptyState
import com.iris.app.ui.components.decodeThumbHash
import com.iris.app.ui.theme.IrisAccentLime
import com.iris.app.ui.theme.IrisDarkBg
import kotlinx.coroutines.launch

/**
 * Full-bleed media viewer.
 *
 * The photo owns the whole screen and the controls stay out of the way until
 * asked for: a tap toggles them, the way a photo app is expected to behave.
 * What used to be here was the opposite — a 340dp box inside a scrolling page
 * under a permanent title bar, so the image was never the subject.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun MediaDetailScreen(
    viewModel: MediaDetailViewModel,
    onBack: () -> Unit,
    onMediaClick: (Int) -> Unit,
    onPersonClick: (Int, String) -> Unit
) {
    val uiState by viewModel.uiState.collectAsState()
    val context = LocalContext.current
    val application = context.applicationContext as IrisApplication
    val apiClient = application.apiClient
    val scope = rememberCoroutineScope()

    // Opens immersive; the controls are one tap away.
    var chromeVisible by remember { mutableStateOf(false) }
    var showDetails by remember { mutableStateOf(false) }
    var renaming by remember { mutableStateOf(false) }
    val sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = false)

    uiState.notice?.let { notice ->
        LaunchedEffect(notice) {
            android.widget.Toast.makeText(context, notice, android.widget.Toast.LENGTH_SHORT).show()
            viewModel.clearNotice()
        }
    }

    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(Color.Black)
    ) {
        when {
            uiState.isLoading -> Box(Modifier.fillMaxSize(), Alignment.Center) {
                CircularProgressIndicator(color = IrisAccentLime)
            }

            uiState.error != null -> EmptyState(
                title = "Erro ao carregar",
                message = uiState.error ?: "",
                actionLabel = "Tentar novamente",
                onAction = { viewModel.loadDetail() }
            )

            uiState.record != null -> {
                val record = uiState.record!!
                MediaStage(
                    record = record,
                    apiClient = apiClient,
                    onToggleChrome = { chromeVisible = !chromeVisible }
                )

                AnimatedVisibility(
                    visible = chromeVisible,
                    enter = fadeIn() + slideInVertically { -it },
                    exit = fadeOut() + slideOutVertically { -it },
                    modifier = Modifier.align(Alignment.TopCenter)
                ) {
                    ViewerTopBar(title = record.cleanFilename, onBack = onBack)
                }

                AnimatedVisibility(
                    visible = chromeVisible,
                    enter = fadeIn() + slideInVertically { it },
                    exit = fadeOut() + slideOutVertically { it },
                    modifier = Modifier.align(Alignment.BottomCenter)
                ) {
                    ViewerActionBar(
                        onDownload = {
                            val url = apiClient.resolveMediaUrl(record.resolvedPath ?: record.caminho)
                            viewModel.showNotice("Baixando…")
                            scope.launch {
                                val result = MediaDownloader(context, apiClient)
                                    .download(url, record.cleanFilename)
                                viewModel.showNotice(
                                    when (result) {
                                        is MediaDownloader.Result.Saved ->
                                            "Salvo em Downloads: ${result.displayName}"
                                        is MediaDownloader.Result.Failed ->
                                            "Falha ao baixar: ${result.reason}"
                                    }
                                )
                            }
                        },
                        onDetails = { showDetails = true },
                        onShare = { shareRecord(context, record, apiClient) },
                        onRename = { renaming = true }
                    )
                }
            }
        }
    }

    if (showDetails && uiState.record != null) {
        ModalBottomSheet(
            onDismissRequest = { showDetails = false },
            sheetState = sheetState,
            containerColor = IrisDarkBg
        ) {
            MediaDetailsSheet(
                state = uiState,
                onPersonClick = onPersonClick,
                onMediaClick = { index ->
                    showDetails = false
                    onMediaClick(index)
                }
            )
        }
    }

    if (renaming && uiState.record != null) {
        RenameDialog(
            currentName = uiState.record!!.cleanFilename,
            isWorking = uiState.isRenaming,
            onDismiss = { renaming = false },
            onConfirm = { novo ->
                renaming = false
                viewModel.rename(novo)
            }
        )
    }
}

/** The media itself, filling the screen. Tap toggles chrome; pinch zooms. */
@Composable
private fun MediaStage(
    record: MediaRecord,
    apiClient: com.iris.app.data.remote.IrisApiClient,
    onToggleChrome: () -> Unit,
) {
    val context = LocalContext.current
    val fullMediaUrl = apiClient.resolveMediaUrl(record.resolvedPath ?: record.caminho)

    Box(
        modifier = Modifier
            .fillMaxSize()
            .pointerInput(record.index) {
                detectTapGestures(onTap = { onToggleChrome() })
            },
        contentAlignment = Alignment.Center
    ) {
        if (record.isVideo) {
            VideoStage(
                mediaUrl = fullMediaUrl,
                thumbnailUrl = apiClient.resolveThumbnailUrl(record.thumbnailUrl),
                okHttpClient = apiClient.authenticatedOkHttpClient,
            )
        } else {
            var scale by remember(record.index) { mutableFloatStateOf(1f) }
            var offsetX by remember(record.index) { mutableFloatStateOf(0f) }
            var offsetY by remember(record.index) { mutableFloatStateOf(0f) }
            val transformState = rememberTransformableState { zoom, pan, _ ->
                scale = (scale * zoom).coerceIn(1f, 5f)
                // Panning only makes sense once the image is larger than the
                // screen; otherwise it drifts away from centre for no reason.
                if (scale > 1f) {
                    offsetX += pan.x
                    offsetY += pan.y
                } else {
                    offsetX = 0f
                    offsetY = 0f
                }
            }

            val placeholder = remember(record.thumbHash) { decodeThumbHash(record.thumbHash) }
            Box(
                modifier = Modifier
                    .fillMaxSize()
                    .graphicsLayer {
                        scaleX = scale
                        scaleY = scale
                        translationX = offsetX
                        translationY = offsetY
                    }
                    .transformable(transformState),
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

@Composable
private fun VideoStage(
    mediaUrl: String,
    thumbnailUrl: String,
    okHttpClient: okhttp3.OkHttpClient,
) {
    val context = LocalContext.current
    val lifecycleOwner = LocalLifecycleOwner.current

    if (thumbnailUrl.isNotBlank()) {
        AsyncImage(
            model = thumbnailUrl,
            contentDescription = null,
            contentScale = ContentScale.Fit,
            modifier = Modifier.fillMaxSize()
        )
    }

    val exoPlayer = remember(mediaUrl) {
        ExoPlayer.Builder(context)
            .setMediaSourceFactory(
                DefaultMediaSourceFactory(IrisMediaDataSourceFactory(okHttpClient))
            )
            .build()
            .apply {
                setMediaItem(MediaItem.fromUri(mediaUrl))
                prepare()
                playWhenReady = false
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
private fun ViewerTopBar(title: String, onBack: () -> Unit) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .background(Color.Black.copy(alpha = 0.55f))
            .pointerInput(Unit) { detectTapGestures { } }
            .statusBarsPadding()
            .padding(horizontal = 4.dp, vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        IconButtonWithLabel(Icons.AutoMirrored.Filled.ArrowBack, "Voltar", onBack)
        Spacer(Modifier.width(4.dp))
        Text(
            text = title,
            color = Color.White,
            fontSize = 15.sp,
            fontWeight = FontWeight.Medium,
            maxLines = 1
        )
    }
}

@Composable
private fun ViewerActionBar(
    onDownload: () -> Unit,
    onDetails: () -> Unit,
    onShare: () -> Unit,
    onRename: () -> Unit,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .background(Color.Black.copy(alpha = 0.55f))
            // Sem isto, um toque no espaço entre dois botões atravessa a barra,
            // chega na foto e fecha os controles — parece que o botão falhou.
            .pointerInput(Unit) { detectTapGestures { } }
            .navigationBarsPadding()
            .padding(vertical = 10.dp),
        horizontalArrangement = Arrangement.SpaceEvenly,
        verticalAlignment = Alignment.CenterVertically
    ) {
        ActionItem(Icons.Default.Download, "Baixar", onDownload)
        ActionItem(Icons.Default.Share, "Compartilhar", onShare)
        ActionItem(Icons.Default.DriveFileRenameOutline, "Renomear", onRename)
        ActionItem(Icons.Default.Info, "Detalhes", onDetails)
    }
}

@Composable
private fun ActionItem(
    icon: androidx.compose.ui.graphics.vector.ImageVector,
    label: String,
    onClick: () -> Unit,
) {
    Column(
        horizontalAlignment = Alignment.CenterHorizontally,
        modifier = Modifier
            .padding(horizontal = 6.dp)
            .pointerInput(label) { detectTapGestures(onTap = { onClick() }) }
    ) {
        Icon(icon, contentDescription = label, tint = Color.White, modifier = Modifier.size(22.dp))
        Spacer(Modifier.height(4.dp))
        Text(label, color = Color.White, fontSize = 11.sp)
    }
}

@Composable
private fun IconButtonWithLabel(
    icon: androidx.compose.ui.graphics.vector.ImageVector,
    label: String,
    onClick: () -> Unit,
) {
    Box(
        modifier = Modifier
            .size(44.dp)
            .pointerInput(label) { detectTapGestures(onTap = { onClick() }) },
        contentAlignment = Alignment.Center
    ) {
        Icon(icon, contentDescription = label, tint = Color.White)
    }
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
        containerColor = IrisDarkBg,
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
            ) { Text("Renomear", color = IrisAccentLime) }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) { Text("Cancelar", color = Color.White) }
        }
    )
}

private fun shareRecord(
    context: android.content.Context,
    record: MediaRecord,
    apiClient: com.iris.app.data.remote.IrisApiClient,
) {
    val mediaUrl = apiClient.resolveMediaUrl(record.resolvedPath ?: record.caminho)
    val intent = Intent(Intent.ACTION_SEND).apply {
        type = "text/plain"
        putExtra(
            Intent.EXTRA_TEXT,
            buildString {
                appendLine(record.cleanFilename)
                if (!record.descricaoIa.isNullOrBlank()) appendLine("\n${record.descricaoIa}")
                appendLine("\n$mediaUrl")
            }
        )
    }
    context.startActivity(Intent.createChooser(intent, "Compartilhar"))
}
