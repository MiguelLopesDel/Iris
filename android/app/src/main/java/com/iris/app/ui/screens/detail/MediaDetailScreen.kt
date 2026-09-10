package com.iris.app.ui.screens.detail

import android.content.Intent
import android.widget.Toast
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.rememberTransformableState
import androidx.compose.foundation.gestures.transformable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.ContentCopy
import androidx.compose.material.icons.filled.Info
import androidx.compose.material.icons.filled.Person
import androidx.compose.material.icons.filled.Share
import androidx.compose.material.icons.outlined.Photo
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SuggestionChip
import androidx.compose.material3.SuggestionChipDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.platform.LocalContext
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.media3.common.MediaItem
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.source.DefaultMediaSourceFactory
import com.iris.app.data.remote.IrisMediaDataSourceFactory
import androidx.media3.ui.PlayerView
import coil.compose.AsyncImage
import coil.request.ImageRequest
import com.iris.app.IrisApplication
import com.iris.app.ui.components.EmptyState
import com.iris.app.ui.components.MediaCard
import com.iris.app.ui.theme.IrisAccentInk
import com.iris.app.ui.theme.IrisAccentLime
import com.iris.app.ui.theme.IrisDarkBg
import com.iris.app.ui.theme.IrisDarkSurface
import com.iris.app.ui.theme.IrisDarkSurfaceBright
import com.iris.app.ui.theme.IrisTextMuted
import com.iris.app.ui.theme.IrisTextSoft
import com.iris.app.ui.theme.IrisViolet

@OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)
@Composable
fun MediaDetailScreen(
    viewModel: MediaDetailViewModel,
    onBack: () -> Unit,
    onMediaClick: (Int) -> Unit,
    onPersonClick: (Int, String) -> Unit
) {
    val uiState by viewModel.uiState.collectAsState()
    val context = LocalContext.current
    val clipboardManager = LocalClipboardManager.current
    val apiClient = (context.applicationContext as IrisApplication).apiClient

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Text(
                        text = uiState.record?.cleanFilename ?: "Detalhes da Mídia",
                        maxLines = 1,
                        fontSize = 18.sp,
                        fontWeight = FontWeight.SemiBold
                    )
                },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(
                            imageVector = Icons.AutoMirrored.Filled.ArrowBack,
                            contentDescription = "Voltar"
                        )
                    }
                },
                actions = {
                    uiState.record?.let { rec ->
                        IconButton(onClick = {
                            val shareIntent = Intent(Intent.ACTION_SEND).apply {
                                type = "text/plain"
                                val mediaUrl = apiClient.resolveMediaUrl(rec.resolvedPath ?: rec.caminho)
                                val textContent = buildString {
                                    appendLine(rec.cleanFilename)
                                    if (!rec.descricaoIa.isNullOrBlank()) appendLine("\nDescrição IA: ${rec.descricaoIa}")
                                    if (!rec.textoExtraido.isNullOrBlank()) appendLine("\nOCR: ${rec.textoExtraido}")
                                    appendLine("\nLink: $mediaUrl")
                                }
                                putExtra(Intent.EXTRA_TEXT, textContent)
                            }
                            context.startActivity(Intent.createChooser(shareIntent, "Compartilhar Mídia"))
                        }) {
                            Icon(imageVector = Icons.Default.Share, contentDescription = "Compartilhar")
                        }
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(containerColor = IrisDarkBg)
            )
        },
        containerColor = IrisDarkBg
    ) { paddingValues ->
        when {
            uiState.isLoading -> {
                Box(
                    modifier = Modifier
                        .fillMaxSize()
                        .padding(paddingValues),
                    contentAlignment = Alignment.Center
                ) {
                    CircularProgressIndicator(color = IrisAccentLime)
                }
            }

            uiState.error != null -> {
                EmptyState(
                    title = "Erro ao carregar",
                    message = uiState.error ?: "",
                    actionLabel = "Tentar novamente",
                    onAction = { viewModel.loadDetail() },
                    modifier = Modifier.padding(paddingValues)
                )
            }

            uiState.record != null -> {
                val record = uiState.record!!
                val fullMediaUrl = apiClient.resolveMediaUrl(record.resolvedPath ?: record.caminho)

                Column(
                    modifier = Modifier
                        .fillMaxSize()
                        .padding(paddingValues)
                        .verticalScroll(rememberScrollState())
                ) {
                    // Media Stage: Image with Pinch-to-Zoom or Video Player with ExoPlayer
                    Box(
                        modifier = Modifier
                            .fillMaxWidth()
                            .height(340.dp)
                            .background(Color.Black),
                        contentAlignment = Alignment.Center
                    ) {
                        if (record.isVideo) {
                            // ExoPlayer Video Player initialized synchronously so PlayerView has a non-null player on frame 1
                            val lifecycleOwner = LocalLifecycleOwner.current
                            val exoPlayer = remember(fullMediaUrl) {
                                val dataSourceFactory = IrisMediaDataSourceFactory(apiClient.authenticatedOkHttpClient)
                                val mediaSourceFactory = DefaultMediaSourceFactory(context)
                                    .setDataSourceFactory(dataSourceFactory)
                                ExoPlayer.Builder(context)
                                    .setMediaSourceFactory(mediaSourceFactory)
                                    .build().apply {
                                        setMediaItem(MediaItem.fromUri(fullMediaUrl))
                                        prepare()
                                        playWhenReady = true
                                    }
                            }

                            DisposableEffect(exoPlayer, lifecycleOwner) {
                                val observer = LifecycleEventObserver { _, event ->
                                    when (event) {
                                        Lifecycle.Event.ON_PAUSE, Lifecycle.Event.ON_STOP -> {
                                            exoPlayer.pause()
                                        }
                                        else -> {}
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
                                        this.player = exoPlayer
                                        useController = true
                                    }
                                },
                                update = { playerView ->
                                    if (playerView.player != exoPlayer) {
                                        playerView.player = exoPlayer
                                    }
                                },
                                onRelease = { playerView ->
                                    playerView.player = null
                                },
                                modifier = Modifier.fillMaxSize()
                            )
                        } else {
                            // High-res Image with pinch-to-zoom
                            var scale by remember { mutableFloatStateOf(1f) }
                            var offset by remember { mutableStateOf(Offset.Zero) }
                            val transformableState = rememberTransformableState { zoomChange, panChange, _ ->
                                scale = (scale * zoomChange).coerceIn(1f, 4f)
                                offset += panChange
                            }

                            AsyncImage(
                                model = ImageRequest.Builder(context)
                                    .data(fullMediaUrl)
                                    .crossfade(true)
                                    .build(),
                                contentDescription = record.cleanFilename,
                                contentScale = ContentScale.Fit,
                                modifier = Modifier
                                    .fillMaxSize()
                                    .graphicsLayer {
                                        scaleX = scale
                                        scaleY = scale
                                        translationX = offset.x
                                        translationY = offset.y
                                    }
                                    .transformable(state = transformableState)
                            )
                        }
                    }

                    // Metadata details section
                    Column(
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(16.dp)
                    ) {
                        // Title & Format
                        Text(
                            text = record.cleanFilename,
                            fontSize = 20.sp,
                            fontWeight = FontWeight.Bold,
                            color = MaterialTheme.colorScheme.onBackground
                        )

                        Spacer(modifier = Modifier.height(4.dp))
                        Row(
                            horizontalArrangement = Arrangement.spacedBy(8.dp),
                            verticalAlignment = Alignment.CenterVertically
                        ) {
                            Text(
                                text = if (record.isVideo) "VÍDEO" else "IMAGEM",
                                fontSize = 11.sp,
                                fontWeight = FontWeight.Bold,
                                color = IrisAccentLime,
                                modifier = Modifier
                                    .background(IrisDarkSurfaceBright, RoundedCornerShape(4.dp))
                                    .padding(horizontal = 6.dp, vertical = 2.dp)
                            )
                            if (record.fileSize != null && record.fileSize > 0) {
                                val sizeMb = record.fileSize / (1024.0 * 1024.0)
                                Text(
                                    text = String.format("%.2f MB", sizeMb),
                                    fontSize = 12.sp,
                                    color = IrisTextSoft
                                )
                            }
                            Text(
                                text = "Índice #${record.index}",
                                fontSize = 12.sp,
                                color = IrisTextMuted
                            )
                        }

                        // Persons chips (facial recognition)
                        if (record.persons.isNotEmpty()) {
                            Spacer(modifier = Modifier.height(16.dp))
                            Text(
                                text = "Pessoas identificadas",
                                fontSize = 13.sp,
                                fontWeight = FontWeight.SemiBold,
                                color = IrisTextSoft
                            )
                            Spacer(modifier = Modifier.height(8.dp))
                            FlowRow(
                                horizontalArrangement = Arrangement.spacedBy(8.dp),
                                verticalArrangement = Arrangement.spacedBy(6.dp)
                            ) {
                                record.persons.forEach { person ->
                                    SuggestionChip(
                                        onClick = { onPersonClick(person.id, person.name) },
                                        label = { Text(person.name.ifBlank { "Pessoa #${person.id}" }) },
                                        icon = {
                                            Icon(
                                                imageVector = Icons.Default.Person,
                                                contentDescription = null,
                                                modifier = Modifier.size(16.dp)
                                            )
                                        },
                                        colors = SuggestionChipDefaults.suggestionChipColors(
                                            containerColor = IrisDarkSurfaceBright,
                                            labelColor = IrisAccentLime,
                                            iconContentColor = IrisAccentLime
                                        )
                                    )
                                }
                            }
                        }

                        // OCR Text
                        if (!record.textoExtraido.isNullOrBlank()) {
                            Spacer(modifier = Modifier.height(16.dp))
                            Card(
                                shape = RoundedCornerShape(12.dp),
                                colors = CardDefaults.cardColors(containerColor = IrisDarkSurface),
                                modifier = Modifier.fillMaxWidth()
                            ) {
                                Column(modifier = Modifier.padding(14.dp)) {
                                    Row(
                                        modifier = Modifier.fillMaxWidth(),
                                        horizontalArrangement = Arrangement.SpaceBetween,
                                        verticalAlignment = Alignment.CenterVertically
                                    ) {
                                        Text(
                                            text = "Texto extraído (OCR)",
                                            fontSize = 13.sp,
                                            fontWeight = FontWeight.SemiBold,
                                            color = IrisViolet
                                        )
                                        IconButton(
                                            onClick = {
                                                clipboardManager.setText(AnnotatedString(record.textoExtraido))
                                                Toast.makeText(context, "Texto copiado!", Toast.LENGTH_SHORT).show()
                                            },
                                            modifier = Modifier.size(28.dp)
                                        ) {
                                            Icon(
                                                imageVector = Icons.Default.ContentCopy,
                                                contentDescription = "Copiar",
                                                tint = IrisTextSoft,
                                                modifier = Modifier.size(16.dp)
                                            )
                                        }
                                    }
                                    Spacer(modifier = Modifier.height(6.dp))
                                    Text(
                                        text = record.textoExtraido,
                                        fontSize = 14.sp,
                                        color = MaterialTheme.colorScheme.onSurface
                                    )
                                }
                            }
                        }

                        // AI Caption & Visual Description
                        if (!record.descricaoIa.isNullOrBlank()) {
                            Spacer(modifier = Modifier.height(14.dp))
                            Card(
                                shape = RoundedCornerShape(12.dp),
                                colors = CardDefaults.cardColors(containerColor = IrisDarkSurface),
                                modifier = Modifier.fillMaxWidth()
                            ) {
                                Column(modifier = Modifier.padding(14.dp)) {
                                    Text(
                                        text = "Descrição da IA (Florence-2 / VLM)",
                                        fontSize = 13.sp,
                                        fontWeight = FontWeight.SemiBold,
                                        color = IrisAccentLime
                                    )
                                    Spacer(modifier = Modifier.height(6.dp))
                                    Text(
                                        text = record.descricaoIa,
                                        fontSize = 14.sp,
                                        color = MaterialTheme.colorScheme.onSurface
                                    )
                                }
                            }
                        }

                        // Tags
                        if (record.tagsList.isNotEmpty()) {
                            Spacer(modifier = Modifier.height(14.dp))
                            Text(
                                text = "Tags",
                                fontSize = 13.sp,
                                fontWeight = FontWeight.SemiBold,
                                color = IrisTextSoft
                            )
                            Spacer(modifier = Modifier.height(6.dp))
                            FlowRow(
                                horizontalArrangement = Arrangement.spacedBy(6.dp),
                                verticalArrangement = Arrangement.spacedBy(6.dp)
                            ) {
                                record.tagsList.forEach { tag ->
                                    Box(
                                        modifier = Modifier
                                            .background(IrisDarkSurfaceBright, RoundedCornerShape(8.dp))
                                            .padding(horizontal = 8.dp, vertical = 4.dp)
                                    ) {
                                        Text(text = "#$tag", fontSize = 12.sp, color = IrisTextSoft)
                                    }
                                }
                            }
                        }

                        // Similar media section
                        if (uiState.similarRecords.isNotEmpty()) {
                            Spacer(modifier = Modifier.height(24.dp))
                            HorizontalDivider(color = IrisDarkSurfaceBright)
                            Spacer(modifier = Modifier.height(16.dp))
                            Text(
                                text = "Mídias Similares (Embeddings CLIP)",
                                fontSize = 16.sp,
                                fontWeight = FontWeight.Bold,
                                color = MaterialTheme.colorScheme.onBackground
                            )
                            Spacer(modifier = Modifier.height(10.dp))
                            LazyRow(
                                horizontalArrangement = Arrangement.spacedBy(8.dp),
                                contentPadding = PaddingValues(end = 16.dp)
                            ) {
                                items(
                                    items = uiState.similarRecords,
                                    key = { it.index }
                                ) { simRecord ->
                                    MediaCard(
                                        record = simRecord,
                                        onClick = { onMediaClick(simRecord.index) },
                                        modifier = Modifier.size(100.dp)
                                    )
                                }
                            }
                        }

                        Spacer(modifier = Modifier.height(32.dp))
                    }
                }
            }
        }
    }
}
