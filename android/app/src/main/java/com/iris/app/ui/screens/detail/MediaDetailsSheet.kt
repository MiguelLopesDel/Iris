package com.iris.app.ui.screens.detail

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ContentCopy
import androidx.compose.material.icons.filled.ExpandLess
import androidx.compose.material.icons.filled.ExpandMore
import androidx.compose.material.icons.filled.Person
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import coil.compose.AsyncImage
import com.iris.app.IrisApplication
import com.iris.app.data.model.MediaPersonRef
import com.iris.app.ui.components.MediaCard
import java.util.Locale

/**
 * The information panel, ordered by what people look for: when and where,
 * who is in it, then what Iris found in it (its description, the text in the
 * picture, similar photos). File details come last and closed.
 *
 * It uses the theme's colours, so it follows the system's light or dark
 * setting; the caller provides that theme.
 */
@Composable
fun MediaDetailsSheet(
    state: MediaDetailUiState,
    onPersonClick: (Int, String) -> Unit,
    onMediaClick: (Int) -> Unit,
) {
    val record = state.record ?: return
    val curated = state.metadata?.curated
    var fileDetailsVisible by remember(record.index) { mutableStateOf(false) }

    Column(
        modifier = Modifier
            .fillMaxWidth()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 20.dp)
            .navigationBarsPadding()
            .padding(bottom = 24.dp)
    ) {
        Section("Quando e onde") {
            BodyLine(
                ViewerTitle.fullDate(curated?.textOf("captured_at"), record.fileMtime) ?: "Data desconhecida"
            )
            curated?.textOf("location_label")?.let { BodyLine(it) }
        }

        if (record.persons.isNotEmpty()) {
            Section("Quem aparece") {
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .horizontalScroll(rememberScrollState()),
                    horizontalArrangement = Arrangement.spacedBy(12.dp)
                ) {
                    record.persons.forEach { person ->
                        PersonFace(person, onPersonClick)
                    }
                }
            }
        }

        record.descricaoIa?.takeIf { it.isNotBlank() }?.let { description ->
            Section("O que o Iris viu nesta foto") { TextCard(description) }
        }

        record.textoExtraido?.takeIf { it.isNotBlank() }?.let { text ->
            val clipboard = LocalClipboardManager.current
            Section(
                title = "Texto na foto",
                action = {
                    TextButton(onClick = { clipboard.setText(AnnotatedString(text)) }) {
                        Icon(Icons.Default.ContentCopy, contentDescription = null, modifier = Modifier.size(18.dp))
                        Spacer(Modifier.width(6.dp))
                        Text("Copiar", fontSize = 15.sp)
                    }
                }
            ) { TextCard(text) }
        }

        if (record.collections.isNotEmpty()) {
            Section("Álbuns") {
                ChipRow(record.collections.map { it.name.ifBlank { "Álbum #${it.id}" } })
            }
        }

        if (record.tagsList.isNotEmpty()) {
            Section("Assuntos") { ChipRow(record.tagsList) }
        }

        if (state.isLoadingSimilars || state.similarRecords.isNotEmpty()) {
            Section("Fotos parecidas") {
                if (state.similarRecords.isEmpty()) {
                    CircularProgressIndicator(modifier = Modifier.size(24.dp), strokeWidth = 2.dp)
                } else {
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .horizontalScroll(rememberScrollState()),
                        horizontalArrangement = Arrangement.spacedBy(8.dp)
                    ) {
                        state.similarRecords.take(12).forEach { similar ->
                            Box(modifier = Modifier.width(110.dp)) {
                                MediaCard(record = similar, onClick = { onMediaClick(similar.index) })
                            }
                        }
                    }
                }
            }
        }

        Spacer(Modifier.height(12.dp))
        TextButton(onClick = { fileDetailsVisible = !fileDetailsVisible }) {
            Text("Detalhes do arquivo", fontSize = 16.sp, fontWeight = FontWeight.SemiBold)
            Spacer(Modifier.width(4.dp))
            Icon(
                imageVector = if (fileDetailsVisible) Icons.Default.ExpandLess else Icons.Default.ExpandMore,
                contentDescription = null,
                modifier = Modifier.size(20.dp)
            )
        }

        if (fileDetailsVisible) {
            val full = state.metadata?.full
            DetailRow("Nome", record.cleanFilename)
            DetailRow("Tipo", if (record.isVideo) "Vídeo" else "Imagem")
            record.fileSize?.let { DetailRow("Tamanho", formatBytes(it)) }
            full?.textOf("format")?.let { DetailRow("Formato", it) }
            val width = full?.textOf("width")
            val height = full?.textOf("height")
            if (width != null && height != null) DetailRow("Dimensões", "$width × $height")
            full?.textOf("duration")?.let { DetailRow("Duração", it) }
            curated?.textOf("device")?.let { DetailRow("Aparelho", it) }
            curated?.textOf("source_app")?.let { DetailRow("Origem", it) }
            record.resolvedPath?.let { DetailRow("Caminho", it) }
        }
    }
}

@Composable
private fun Section(
    title: String,
    action: (@Composable () -> Unit)? = null,
    content: @Composable () -> Unit,
) {
    Spacer(Modifier.height(16.dp))
    Row(
        modifier = Modifier.fillMaxWidth(),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Text(
            text = title,
            fontSize = 15.sp,
            fontWeight = FontWeight.SemiBold,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.weight(1f)
        )
        action?.invoke()
    }
    Spacer(Modifier.height(6.dp))
    content()
}

@Composable
private fun BodyLine(text: String) {
    Text(
        text = text,
        fontSize = 16.sp,
        color = MaterialTheme.colorScheme.onSurface,
        modifier = Modifier.padding(vertical = 2.dp)
    )
}

@Composable
private fun TextCard(text: String) {
    Box(
        modifier = Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(12.dp))
            .background(MaterialTheme.colorScheme.surfaceVariant)
            .padding(14.dp)
    ) {
        Text(text = text, fontSize = 16.sp, color = MaterialTheme.colorScheme.onSurface)
    }
}

/** A face in a circle with the name under it; a tap opens that person's photos. */
@Composable
private fun PersonFace(person: MediaPersonRef, onPersonClick: (Int, String) -> Unit) {
    val apiClient = (LocalContext.current.applicationContext as IrisApplication).apiClient
    val name = person.name.ifBlank { "Sem nome" }
    Column(
        horizontalAlignment = Alignment.CenterHorizontally,
        modifier = Modifier
            .width(76.dp)
            .clip(RoundedCornerShape(12.dp))
            .clickable { onPersonClick(person.id, name) }
            .padding(vertical = 4.dp)
    ) {
        Box(
            modifier = Modifier
                .size(64.dp)
                .clip(CircleShape)
                .background(MaterialTheme.colorScheme.surfaceVariant),
            contentAlignment = Alignment.Center
        ) {
            val faceId = person.faceId
            if (faceId != null) {
                AsyncImage(
                    model = apiClient.resolveFaceThumbnailUrl(faceId),
                    contentDescription = name,
                    contentScale = ContentScale.Crop,
                    modifier = Modifier.size(64.dp)
                )
            } else {
                Icon(
                    Icons.Default.Person,
                    contentDescription = name,
                    tint = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.size(32.dp)
                )
            }
        }
        Spacer(Modifier.height(4.dp))
        Text(
            text = name,
            fontSize = 14.sp,
            color = MaterialTheme.colorScheme.onSurface,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
            textAlign = TextAlign.Center
        )
    }
}

@Composable
private fun DetailRow(label: String, value: String) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 5.dp),
        verticalAlignment = Alignment.Top
    ) {
        Text(
            text = label,
            fontSize = 15.sp,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.width(112.dp)
        )
        Text(text = value, fontSize = 15.sp, color = MaterialTheme.colorScheme.onSurface)
    }
}

@Composable
private fun ChipRow(values: List<String>) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .horizontalScroll(rememberScrollState()),
        horizontalArrangement = Arrangement.spacedBy(8.dp)
    ) {
        values.forEach { value ->
            Box(
                modifier = Modifier
                    .clip(RoundedCornerShape(10.dp))
                    .background(MaterialTheme.colorScheme.surfaceVariant)
                    .padding(horizontal = 12.dp, vertical = 8.dp)
            ) {
                Text(text = value, fontSize = 15.sp, color = MaterialTheme.colorScheme.onSurface)
            }
        }
    }
}

private fun formatBytes(bytes: Long): String = when {
    bytes >= 1_073_741_824 -> String.format(Locale.US, "%.1f GB", bytes / 1_073_741_824.0)
    bytes >= 1_048_576 -> String.format(Locale.US, "%.1f MB", bytes / 1_048_576.0)
    bytes >= 1024 -> String.format(Locale.US, "%.0f KB", bytes / 1024.0)
    else -> "$bytes B"
}
