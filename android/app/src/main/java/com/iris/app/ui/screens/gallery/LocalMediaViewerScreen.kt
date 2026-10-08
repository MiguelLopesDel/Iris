package com.iris.app.ui.screens.gallery

import android.content.ClipData
import android.content.Context
import android.content.Intent
import android.graphics.Color as AndroidColor
import android.net.Uri
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.gestures.calculatePan
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.ScrollState
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.CloudOff
import androidx.compose.material.icons.outlined.CloudDone
import androidx.compose.material.icons.outlined.CloudUpload
import androidx.compose.material.icons.outlined.ErrorOutline
import androidx.compose.material.icons.outlined.ExpandLess
import androidx.compose.material.icons.outlined.ExpandMore
import androidx.compose.material.icons.outlined.Image
import androidx.compose.material.icons.outlined.Info
import androidx.compose.material.icons.outlined.MobileOff
import androidx.compose.material.icons.outlined.Movie
import androidx.compose.material.icons.outlined.Share
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.media3.common.MediaItem
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.ui.PlayerView
import coil.compose.AsyncImage
import com.iris.app.IrisApplication
import com.iris.app.R
import com.iris.app.data.local.DeviceBackupState
import com.iris.app.data.local.DeviceMediaDetails
import com.iris.app.ui.screens.detail.DetailCard
import com.iris.app.ui.screens.detail.DetailLine
import com.iris.app.ui.screens.detail.DeviceCopyCard
import com.iris.app.ui.screens.detail.SmallChip
import com.iris.app.ui.screens.detail.ViewerAction
import com.iris.app.ui.screens.detail.ViewerActionBar
import com.iris.app.ui.screens.detail.ViewerGestures
import com.iris.app.ui.screens.detail.ViewerScaffold
import com.iris.app.ui.screens.detail.ViewerTitle
import com.iris.app.ui.screens.detail.ViewerTopBar
import com.iris.app.ui.screens.detail.appLabel
import com.iris.app.ui.screens.detail.megapixels
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/**
 * Why a device item will not be backed up, with the action that fixes it.
 * [message] already names the folder or media kind left out.
 */
data class NotInBackupNotice(val message: String, val onInclude: () -> Unit)

/**
 * Viewer for media that is only on the phone, not on the server yet. It
 * looks and behaves like the server viewer (date as the title, the same bars,
 * the same information panel), with what the phone's media store knows: when
 * it was taken, the file, its folder, the app that made it, and where it
 * stands with the backup.
 */
@Composable
fun LocalMediaViewerScreen(
    mediaUri: String,
    onBack: () -> Unit,
    notInBackup: NotInBackupNotice? = null,
) {
    val uri = remember(mediaUri) { Uri.parse(mediaUri) }
    val context = LocalContext.current
    val application = context.applicationContext as IrisApplication
    val details by produceState<DeviceMediaDetails?>(initialValue = null, uri) {
        value = withContext(Dispatchers.IO) { DeviceMediaDetails.read(context.contentResolver, uri) }
    }
    val isVideo = details?.isVideo ?: remember(context, uri) { isVideo(context, uri) }

    var chromeVisible by remember { mutableStateOf(true) }
    var panelOpen by remember { mutableStateOf(false) }
    val backup by produceState<DeviceBackupState?>(initialValue = null, mediaUri, panelOpen) {
        // The producer's State survives key changes. Refresh on every opening
        // instead of treating the first lookup as a live view of the queue.
        if (panelOpen) {
            value = runCatching { DeviceBackupState.of(mediaUri, application.irisRepository.getUploadQueue()) }
                .getOrDefault(DeviceBackupState.NOT_QUEUED)
        }
    }

    ViewerScaffold(
        panelOpen = panelOpen,
        onClosePanel = { panelOpen = false },
        chromeVisible = chromeVisible,
        topBar = {
            val title = remember(details) {
                ViewerTitle.of(
                    capturedAt = null,
                    fileMtime = details?.takenAtMillis?.let { it / 1000.0 },
                    place = null,
                    fileName = details?.name ?: "Mídia do aparelho",
                )
            }
            ViewerTopBar(
                title = title,
                onBack = onBack,
                onTitleClick = { panelOpen = true },
                menu = listOf(ViewerAction(Icons.Outlined.Info, "Sobre") { panelOpen = true }),
            )
        },
        bottomBar = {
            Column {
                // Why it will not reach Iris, where it can be fixed at once.
                if (notInBackup != null) NotInBackupBanner(notInBackup)
                ViewerActionBar(
                    listOf(
                        ViewerAction(Icons.Outlined.Share, "Compartilhar") { shareDeviceMedia(context, uri, details?.mimeType) },
                        ViewerAction(Icons.Outlined.Info, "Informações") { panelOpen = true },
                    )
                )
            }
        },
        panel = {
            details?.let { DeviceMediaSheet(it, backup, notInBackup) }
        },
        photo = { photoModifier ->
            val swipeThreshold = with(LocalDensity.current) { 96.dp.toPx() }
            Box(
                modifier = photoModifier
                    .pointerInput(Unit) {
                        detectTapGestures(onTap = {
                            if (panelOpen) panelOpen = false else chromeVisible = !chromeVisible
                        })
                    }
                    .pointerInput(isVideo) {
                        if (isVideo) return@pointerInput
                        // Up opens the panel, down closes it or the viewer, as in the server viewer.
                        awaitEachGesture {
                            awaitFirstDown(requireUnconsumed = false)
                            var total = Offset.Zero
                            var vertical = false
                            do {
                                val event = awaitPointerEvent()
                                total += event.calculatePan()
                                if (!vertical && ViewerGestures.isVertical(total, viewConfiguration.touchSlop)) vertical = true
                                if (vertical) event.changes.forEach { it.consume() }
                            } while (event.changes.any { it.pressed })
                            if (vertical) {
                                when (ViewerGestures.swipeVerdict(total.y, swipeThreshold)) {
                                    ViewerGestures.Swipe.SHOW_INFO -> panelOpen = true
                                    ViewerGestures.Swipe.CLOSE -> if (panelOpen) panelOpen = false else onBack()
                                    ViewerGestures.Swipe.NONE -> Unit
                                }
                            }
                        }
                    },
                contentAlignment = Alignment.Center
            ) {
                if (isVideo) {
                    LocalVideoPlayer(uri)
                } else {
                    AsyncImage(
                        model = uri,
                        contentDescription = details?.name ?: "Mídia do aparelho",
                        contentScale = ContentScale.Fit,
                        modifier = Modifier.fillMaxSize()
                    )
                }
            }
        },
    )
}

/** The panel for a device item: the date, where it stands with the backup, the copy on the phone and the file. */
@Composable
private fun DeviceMediaSheet(
    details: DeviceMediaDetails,
    backup: DeviceBackupState?,
    notInBackup: NotInBackupNotice?,
) {
    val scroll = remember(details) { ScrollState(0) }
    var fileExpanded by remember(details) { mutableStateOf(false) }
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .verticalScroll(scroll)
            .padding(horizontal = 20.dp)
            .navigationBarsPadding()
            .padding(bottom = 24.dp)
    ) {
        Text(
            text = ViewerTitle.fullDate(null, details.takenAtMillis?.let { it / 1000.0 }) ?: "Data desconhecida",
            fontSize = 19.sp,
            color = MaterialTheme.colorScheme.onSurface,
            modifier = Modifier.padding(top = 4.dp, bottom = 4.dp)
        )

        Spacer(Modifier.height(20.dp))
        Text(
            "Detalhes",
            fontSize = 19.sp,
            color = MaterialTheme.colorScheme.onSurface,
            modifier = Modifier.padding(bottom = 10.dp)
        )

        BackupCard(backup, notInBackup)
        Spacer(Modifier.height(4.dp))
        DeviceCopyCard(details)
        Spacer(Modifier.height(4.dp))

        DetailCard(
            icon = if (details.isVideo) Icons.Outlined.Movie else Icons.Outlined.Image,
            onClick = { fileExpanded = !fileExpanded },
            trailing = {
                Icon(
                    if (fileExpanded) Icons.Outlined.ExpandLess else Icons.Outlined.ExpandMore,
                    contentDescription = if (fileExpanded) "Mostrar menos" else "Mostrar mais",
                    tint = MaterialTheme.colorScheme.onSurfaceVariant
                )
            }
        ) {
            Text(
                details.name,
                fontSize = 16.sp,
                color = MaterialTheme.colorScheme.onSurface,
                maxLines = if (fileExpanded) Int.MAX_VALUE else 2,
                overflow = TextOverflow.Ellipsis
            )
            val width = details.width
            val height = details.height
            val facts = listOfNotNull(
                if (width != null && height != null) megapixels(width, height) else null,
                if (width != null && height != null) "$width × $height" else null,
                details.durationMillis?.let(::formatDuration),
            )
            if (facts.isNotEmpty()) {
                Spacer(Modifier.height(6.dp))
                Row { facts.forEach { SmallChip(it); Spacer(Modifier.width(8.dp)) } }
            }
            if (fileExpanded) {
                Spacer(Modifier.height(8.dp))
                details.mimeType?.let { DetailLine("Tipo", it) }
                details.ownerPackage?.let { DetailLine("Criada por", appLabel(it)) }
            }
        }
    }
}

@Composable
private fun BackupCard(backup: DeviceBackupState?, notInBackup: NotInBackupNotice?) {
    val (icon, title, note) = when {
        notInBackup != null -> Triple(Icons.Outlined.CloudOff, "Fora do backup", notInBackup.message)
        backup == DeviceBackupState.QUEUED -> Triple(Icons.Outlined.CloudUpload, "Na fila para o Iris", "Vai no próximo envio")
        backup == DeviceBackupState.SENDING -> Triple(Icons.Outlined.CloudUpload, "Enviando para o Iris", null)
        backup == DeviceBackupState.SAVED -> Triple(Icons.Outlined.CloudDone, "Salva no Iris", "Qualidade original")
        backup == DeviceBackupState.FAILED ->
            Triple(Icons.Outlined.ErrorOutline, "O envio falhou", "Não será reenviada automaticamente")
        backup == DeviceBackupState.FAILED_PROCESSING ->
            Triple(Icons.Outlined.ErrorOutline, "Original salvo no Iris", "O processamento no servidor falhou; o arquivo continua disponível")
        else -> Triple(Icons.Outlined.CloudOff, "Ainda não está no Iris", "Entra no próximo backup")
    }
    DetailCard(icon = icon) {
        Text(title, fontSize = 16.sp, color = MaterialTheme.colorScheme.onSurface)
        note?.let { Text(it, fontSize = 14.sp, color = MaterialTheme.colorScheme.onSurfaceVariant) }
        if (notInBackup != null) {
            TextButton(onClick = notInBackup.onInclude, modifier = Modifier.padding(top = 2.dp)) {
                Text(stringResource(R.string.local_media_include_folder), fontSize = 15.sp)
            }
        }
    }
}

@Composable
private fun NotInBackupBanner(notice: NotInBackupNotice) {
    Surface(
        color = MaterialTheme.colorScheme.surfaceVariant,
        shape = RoundedCornerShape(12.dp),
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 16.dp, vertical = 8.dp)
    ) {
        Row(
            modifier = Modifier.padding(horizontal = 14.dp, vertical = 10.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            Icon(
                Icons.Outlined.MobileOff,
                contentDescription = null,
                tint = MaterialTheme.colorScheme.onSurfaceVariant
            )
            Text(
                text = notice.message,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier
                    .weight(1f)
                    .padding(horizontal = 10.dp)
            )
            TextButton(onClick = notice.onInclude) {
                Text(stringResource(R.string.local_media_include_folder))
            }
        }
    }
}

/** "1:05", the way a player shows a length. */
internal fun formatDuration(millis: Long): String {
    val seconds = millis / 1000
    val hours = seconds / 3600
    return if (hours > 0) {
        "%d:%02d:%02d".format(hours, (seconds % 3600) / 60, seconds % 60)
    } else {
        "%d:%02d".format(seconds / 60, seconds % 60)
    }
}

/** Shares the phone's own file: it is already here, nothing to fetch. */
private fun shareDeviceMedia(context: Context, uri: Uri, mimeType: String?) {
    val send = Intent(Intent.ACTION_SEND).apply {
        type = mimeType ?: context.contentResolver.getType(uri) ?: "image/*"
        putExtra(Intent.EXTRA_STREAM, uri)
        clipData = ClipData.newRawUri(null, uri)
        addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
    }
    context.startActivity(
        Intent.createChooser(send, "Compartilhar").addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
    )
}

private fun isVideo(context: Context, uri: Uri): Boolean = runCatching {
    context.contentResolver.getType(uri)?.startsWith("video/") == true
}.getOrDefault(false)

@androidx.annotation.OptIn(markerClass = [androidx.media3.common.util.UnstableApi::class])
@Composable
private fun LocalVideoPlayer(uri: Uri) {
    val context = LocalContext.current
    val lifecycleOwner = LocalLifecycleOwner.current
    val exoPlayer = remember(uri) {
        ExoPlayer.Builder(context).build().apply {
            setMediaItem(MediaItem.fromUri(uri))
            prepare()
        }
    }
    DisposableEffect(lifecycleOwner, exoPlayer) {
        val observer = LifecycleEventObserver { _, event ->
            if (event == Lifecycle.Event.ON_PAUSE) exoPlayer.pause()
        }
        lifecycleOwner.lifecycle.addObserver(observer)
        onDispose {
            lifecycleOwner.lifecycle.removeObserver(observer)
            exoPlayer.release()
        }
    }
    AndroidView(
        factory = { viewContext ->
            PlayerView(viewContext).apply {
                player = exoPlayer
                useController = true
                setShutterBackgroundColor(AndroidColor.BLACK)
            }
        },
        modifier = Modifier.fillMaxSize()
    )
}
