package com.iris.app.ui.screens.detail

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.ScrollState
import androidx.compose.foundation.background
import androidx.compose.foundation.border
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
import androidx.compose.material.icons.filled.ExpandLess
import androidx.compose.material.icons.filled.ExpandMore
import androidx.compose.material.icons.filled.Person
import androidx.compose.material.icons.outlined.CloudDone
import androidx.compose.material.icons.outlined.ContentCopy
import androidx.compose.material.icons.outlined.Image
import androidx.compose.material.icons.outlined.Movie
import androidx.compose.material.icons.outlined.PhoneAndroid
import androidx.compose.material.icons.outlined.Place
import androidx.compose.material.icons.outlined.Search
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.getValue
import androidx.compose.runtime.setValue
import androidx.compose.runtime.Composable
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import coil.compose.AsyncImage
import com.iris.app.IrisApplication
import com.iris.app.data.local.DeviceMediaDetails
import com.iris.app.data.model.MediaPersonRef
import com.iris.app.ui.components.MediaCard
import java.util.Locale

/**
 * The information panel, laid out like a phone gallery's: the date in full on
 * top, then who is in the photo and what Iris found in it (its description,
 * the text in the picture with actions on it, similar photos), then the
 * details as cards: where the item is kept, and the file.
 *
 * It uses the theme's colours, so it follows the system's light or dark
 * setting; the caller provides that theme.
 */
@Composable
fun MediaDetailsSheet(
    state: MediaDetailUiState,
    onPersonClick: (Int, String) -> Unit,
    onMediaClick: (Int) -> Unit,
    onSearchText: (String) -> Unit,
) {
    val record = state.record ?: return
    val curated = state.metadata?.curated
    val full = state.metadata?.full

    // Not saveable: the panel opens at the top each time, as a gallery's does.
    val scroll = remember(record.index) { ScrollState(0) }
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .verticalScroll(scroll)
            .padding(horizontal = 20.dp)
            .navigationBarsPadding()
            .padding(bottom = 24.dp)
    ) {
        Text(
            text = ViewerTitle.fullDate(curated?.textOf("captured_at"), record.fileMtime) ?: "Data desconhecida",
            fontSize = 19.sp,
            color = MaterialTheme.colorScheme.onSurface,
            modifier = Modifier.padding(top = 4.dp, bottom = 4.dp)
        )
        curated?.textOf("location_label")?.let { place ->
            Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(vertical = 4.dp)) {
                Icon(
                    Icons.Outlined.Place,
                    contentDescription = null,
                    tint = MaterialTheme.colorScheme.primary,
                    modifier = Modifier.size(22.dp)
                )
                Spacer(Modifier.width(10.dp))
                Text(place, fontSize = 16.sp, color = MaterialTheme.colorScheme.onSurface)
            }
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
            Section("Texto na foto") {
                PhotoTextCard(
                    text = text,
                    thumbnailUrl = record.thumbnailUrl,
                    onSearch = { onSearchText(text) },
                )
            }
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

        Spacer(Modifier.height(20.dp))
        Text(
            "Detalhes",
            fontSize = 19.sp,
            color = MaterialTheme.colorScheme.onSurface,
            modifier = Modifier.padding(bottom = 10.dp)
        )

        // Where the item is kept: Iris holds the original as it was sent.
        DetailCard(icon = Icons.Outlined.CloudDone) {
            Text(
                listOfNotNull("Salva no Iris", record.fileSize?.let(::formatBytes)).joinToString(" • "),
                fontSize = 16.sp,
                color = MaterialTheme.colorScheme.onSurface
            )
            Text("Qualidade original", fontSize = 14.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        state.deviceCopy?.let { copy ->
            Spacer(Modifier.height(4.dp))
            DeviceCopyCard(copy)
        }
        Spacer(Modifier.height(4.dp))

        // The file: its name and size at a glance, the rest one tap away.
        var fileExpanded by remember(record.index) { mutableStateOf(false) }
        DetailCard(
            icon = if (record.isVideo) Icons.Outlined.Movie else Icons.Outlined.Image,
            onClick = { fileExpanded = !fileExpanded },
            trailing = {
                Icon(
                    if (fileExpanded) Icons.Default.ExpandLess else Icons.Default.ExpandMore,
                    contentDescription = if (fileExpanded) "Mostrar menos" else "Mostrar mais",
                    tint = MaterialTheme.colorScheme.onSurfaceVariant
                )
            }
        ) {
            Text(
                record.cleanFilename,
                fontSize = 16.sp,
                color = MaterialTheme.colorScheme.onSurface,
                maxLines = if (fileExpanded) Int.MAX_VALUE else 2,
                overflow = TextOverflow.Ellipsis
            )
            val width = full?.textOf("width")?.toIntOrNull()
            val height = full?.textOf("height")?.toIntOrNull()
            val facts = listOfNotNull(
                if (width != null && height != null) megapixels(width, height) else null,
                if (width != null && height != null) "$width × $height" else null,
                full?.textOf("duration"),
            )
            if (facts.isNotEmpty()) {
                Spacer(Modifier.height(6.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    facts.forEach { SmallChip(it) }
                }
            }
            if (fileExpanded) {
                Spacer(Modifier.height(8.dp))
                full?.textOf("format")?.let { DetailLine("Formato", it) }
                curated?.textOf("device")?.let { DetailLine("Aparelho", it) }
                (state.deviceCopy?.ownerPackage?.let { appLabel(it) } ?: curated?.textOf("source_app"))
                    ?.let { DetailLine("Criada por", it) }
                record.resolvedPath?.let { DetailLine("No servidor", it) }
            }
        }
    }
}

@Composable
internal fun Section(
    title: String,
    content: @Composable () -> Unit,
) {
    Spacer(Modifier.height(18.dp))
    Text(
        text = title,
        fontSize = 15.sp,
        fontWeight = FontWeight.SemiBold,
        color = MaterialTheme.colorScheme.onSurfaceVariant
    )
    Spacer(Modifier.height(8.dp))
    content()
}

@Composable
private fun TextCard(text: String) {
    Box(
        modifier = Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(16.dp))
            .background(MaterialTheme.colorScheme.surfaceVariant)
            .padding(14.dp)
    ) {
        Text(text = text, fontSize = 16.sp, color = MaterialTheme.colorScheme.onSurface)
    }
}

/**
 * The text Iris read in the photo, as a card with what can be done with it:
 * copy it, or search the library for it.
 */
@Composable
internal fun PhotoTextCard(text: String, thumbnailUrl: String?, onSearch: () -> Unit) {
    val apiClient = (LocalContext.current.applicationContext as IrisApplication).apiClient
    val clipboard = LocalClipboardManager.current
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .border(BorderStroke(1.dp, MaterialTheme.colorScheme.outline), RoundedCornerShape(16.dp))
            .padding(14.dp)
    ) {
        Row(verticalAlignment = Alignment.Top) {
            AsyncImage(
                model = apiClient.resolveThumbnailUrl(thumbnailUrl),
                contentDescription = null,
                contentScale = ContentScale.Crop,
                modifier = Modifier
                    .size(56.dp)
                    .clip(RoundedCornerShape(12.dp))
                    .background(MaterialTheme.colorScheme.surfaceVariant)
            )
            Spacer(Modifier.width(14.dp))
            Text(
                text = text,
                fontSize = 16.sp,
                color = MaterialTheme.colorScheme.onSurface,
                maxLines = 4,
                overflow = TextOverflow.Ellipsis
            )
        }
        Spacer(Modifier.height(12.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            ActionChip(Icons.Outlined.ContentCopy, "Copiar texto") { clipboard.setText(AnnotatedString(text)) }
            ActionChip(Icons.Outlined.Search, "Buscar no Iris", onSearch)
        }
    }
}

@Composable
private fun ActionChip(icon: ImageVector, label: String, onClick: () -> Unit) {
    Row(
        verticalAlignment = Alignment.CenterVertically,
        modifier = Modifier
            .clip(RoundedCornerShape(12.dp))
            .border(BorderStroke(1.dp, MaterialTheme.colorScheme.outline), RoundedCornerShape(12.dp))
            .clickable(onClick = onClick, role = Role.Button)
            .padding(horizontal = 14.dp, vertical = 10.dp)
    ) {
        Icon(icon, contentDescription = null, tint = MaterialTheme.colorScheme.onSurface, modifier = Modifier.size(20.dp))
        Spacer(Modifier.width(8.dp))
        Text(label, fontSize = 15.sp, color = MaterialTheme.colorScheme.onSurface)
    }
}

/** A rounded card with a leading icon, as the gallery shows each detail. */
@Composable
internal fun DetailCard(
    icon: ImageVector,
    onClick: (() -> Unit)? = null,
    trailing: (@Composable () -> Unit)? = null,
    content: @Composable () -> Unit,
) {
    // Top-aligned: an expanded card keeps its icon by the first line, as the gallery does.
    Row(
        verticalAlignment = Alignment.Top,
        modifier = Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(16.dp))
            .background(MaterialTheme.colorScheme.surfaceVariant)
            .then(if (onClick != null) Modifier.clickable(onClick = onClick) else Modifier)
            .padding(horizontal = 16.dp, vertical = 14.dp)
    ) {
        Icon(
            icon,
            contentDescription = null,
            tint = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(top = 1.dp).size(26.dp)
        )
        Spacer(Modifier.width(16.dp))
        Column(modifier = Modifier.weight(1f)) { content() }
        trailing?.let {
            Spacer(Modifier.width(8.dp))
            it()
        }
    }
}

@Composable
internal fun SmallChip(text: String) {
    Box(
        modifier = Modifier
            .clip(RoundedCornerShape(8.dp))
            .background(MaterialTheme.colorScheme.surface)
            .padding(horizontal = 10.dp, vertical = 5.dp)
    ) {
        Text(text, fontSize = 14.sp, color = MaterialTheme.colorScheme.onSurface)
    }
}

@Composable
internal fun DetailLine(label: String, value: String) {
    Column(modifier = Modifier.padding(vertical = 4.dp)) {
        Text(label, fontSize = 13.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Text(value, fontSize = 15.sp, color = MaterialTheme.colorScheme.onSurface)
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

/** "1,2 MP", the way a gallery sizes a photo. */
internal fun megapixels(width: Int, height: Int): String =
    String.format(Locale("pt", "BR"), "%.1f MP", width.toLong() * height / 1_000_000.0)

internal fun formatBytes(bytes: Long): String = when {
    bytes >= 1_073_741_824 -> String.format(Locale("pt", "BR"), "%.1f GB", bytes / 1_073_741_824.0)
    bytes >= 1_048_576 -> String.format(Locale("pt", "BR"), "%.1f MB", bytes / 1_048_576.0)
    bytes >= 1024 -> String.format(Locale("pt", "BR"), "%.0f kB", bytes / 1024.0)
    else -> "$bytes B"
}

/** The copy on this phone: its size and the folder it sits in, as the gallery shows it. */
@Composable
internal fun DeviceCopyCard(copy: DeviceMediaDetails) {
    DetailCard(icon = Icons.Outlined.PhoneAndroid) {
        Text(
            listOfNotNull("No dispositivo", copy.sizeBytes?.let(::formatBytes)).joinToString(" • "),
            fontSize = 16.sp,
            color = MaterialTheme.colorScheme.onSurface
        )
        copy.folder?.let {
            Text(it, fontSize = 14.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

/**
 * The name people know an app by. Android may hide other apps from this one;
 * then the package is shown as the gallery does ("App desconhecido (…)").
 */
@Composable
internal fun appLabel(packageName: String): String {
    val packageManager = LocalContext.current.packageManager
    return remember(packageName) {
        try {
            val info = packageManager.getApplicationInfo(packageName, 0)
            packageManager.getApplicationLabel(info).toString()
        } catch (_: Exception) {
            "App desconhecido ($packageName)"
        }
    }
}

