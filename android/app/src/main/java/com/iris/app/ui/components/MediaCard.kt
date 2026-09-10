package com.iris.app.ui.components

import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.outlined.BrokenImage
import androidx.compose.material.icons.outlined.HourglassEmpty
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import coil.compose.AsyncImage
import coil.request.ImageRequest
import com.iris.app.IrisApplication
import com.iris.app.data.model.MediaRecord
import com.iris.app.performance.Metric
import com.iris.app.performance.PerformanceMonitor

@Composable
fun MediaCard(
    record: MediaRecord,
    performanceMonitor: PerformanceMonitor? = null,
    onClick: () -> Unit,
    modifier: Modifier = Modifier
) {
    val context = LocalContext.current
    val apiClient = (context.applicationContext as IrisApplication).apiClient

    val imageRequest = remember(record.index, record.thumbnailUrl, record.resolvedPath, record.caminho) {
        val resolvedThumbUrl = if (!record.thumbnailUrl.isNullOrBlank()) {
            apiClient.resolveThumbnailUrl(record.thumbnailUrl)
        } else {
            apiClient.resolveMediaUrl(record.resolvedPath ?: record.caminho)
        }
        ImageRequest.Builder(context)
            .data(resolvedThumbUrl)
            .crossfade(false)
            .build()
    }
    val finishPreview = remember(record.index, record.thumbnailUrl, record.resolvedPath, record.caminho) {
        performanceMonitor?.begin(if (record.isVideo) Metric.PreviewVideo else Metric.PreviewImage)
    }
    var previewState by remember(record.index) { mutableStateOf(PreviewState.Loading) }
    val placeholder = remember(record.thumbHash) { decodeThumbHash(record.thumbHash) }

    Box(
        modifier = modifier
            .fillMaxWidth()
            .aspectRatio(1f)
            .clip(RoundedCornerShape(12.dp))
            .background(MaterialTheme.colorScheme.surfaceVariant)
            .clickable(onClick = onClick)
    ) {
        Box(modifier = Modifier.fillMaxSize()) {
            // AsyncImage resolves the request to the actual card dimensions.
            // This prevents a missing server thumbnail from decoding an original
            // camera image at full resolution during a fling.
            AsyncImage(
                model = imageRequest,
                contentDescription = record.cleanFilename,
                contentScale = ContentScale.Crop,
                modifier = Modifier.fillMaxSize(),
                onLoading = { previewState = PreviewState.Loading },
                onSuccess = {
                    finishPreview?.invoke()
                    previewState = PreviewState.Ready
                },
                onError = {
                    finishPreview?.invoke()
                    previewState = PreviewState.Error
                }
            )
            when (previewState) {
                PreviewState.Ready -> Unit
                PreviewState.Error -> PreviewError(isVideo = record.isVideo)
                PreviewState.Loading -> if (placeholder != null) {
                    // Upscaled from a 6x6 grid, so the default bilinear filter
                    // renders it as a soft colour field rather than blocks.
                    Image(
                        bitmap = placeholder,
                        contentDescription = null,
                        contentScale = ContentScale.Crop,
                        modifier = Modifier.fillMaxSize()
                    )
                } else {
                    PreviewPlaceholder(isVideo = record.isVideo)
                }
            }

            // Video badge
            if (record.isVideo) {
                Box(
                    modifier = Modifier
                        .padding(6.dp)
                        .align(Alignment.TopStart)
                        .size(24.dp)
                        .background(Color.Black.copy(alpha = 0.6f), CircleShape),
                    contentAlignment = Alignment.Center
                ) {
                    Icon(
                        imageVector = Icons.Default.PlayArrow,
                        contentDescription = "Vídeo",
                        tint = Color.White,
                        modifier = Modifier.size(16.dp)
                    )
                }
            }

        }
    }
}

private enum class PreviewState { Loading, Ready, Error }

@Composable
private fun PreviewPlaceholder(isVideo: Boolean) {
    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(MaterialTheme.colorScheme.surfaceVariant),
        contentAlignment = Alignment.Center
    ) {
        Icon(
            imageVector = if (isVideo) Icons.Default.PlayArrow else Icons.Outlined.HourglassEmpty,
            contentDescription = "Carregando prévia",
            tint = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.55f),
            modifier = Modifier.size(30.dp)
        )
    }
}

@Composable
private fun PreviewError(isVideo: Boolean) {
    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(MaterialTheme.colorScheme.surfaceVariant),
        contentAlignment = Alignment.Center
    ) {
        Icon(
            imageVector = if (isVideo) Icons.Default.PlayArrow else Icons.Outlined.BrokenImage,
            contentDescription = "Prévia indisponível",
            tint = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.65f),
            modifier = Modifier.size(32.dp)
        )
    }
}
