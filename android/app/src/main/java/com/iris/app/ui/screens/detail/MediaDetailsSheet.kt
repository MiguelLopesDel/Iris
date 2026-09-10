package com.iris.app.ui.screens.detail

import androidx.compose.foundation.background
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
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ExpandLess
import androidx.compose.material.icons.filled.ExpandMore
import androidx.compose.material3.AssistChip
import androidx.compose.material3.AssistChipDefaults
import androidx.compose.material3.Icon
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
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.iris.app.ui.components.MediaCard
import com.iris.app.ui.theme.IrisAccentLime
import com.iris.app.ui.theme.IrisDarkSurface
import com.iris.app.ui.theme.IrisTextMuted
import com.iris.app.ui.theme.IrisTextSoft
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * What the viewer shows when asked for details.
 *
 * Two tiers on purpose: the top is what someone looking at a photo actually
 * wants — when, on what, in which album, tagged how. Everything derived by the
 * indexer (OCR, the generated caption, dimensions, similar media) sits behind
 * "Avançados", because it is useful occasionally and noise the rest of the time.
 */
@Composable
fun MediaDetailsSheet(
    state: MediaDetailUiState,
    onPersonClick: (Int, String) -> Unit,
    onMediaClick: (Int) -> Unit,
) {
    val record = state.record ?: return
    var advancedVisible by remember(record.index) { mutableStateOf(false) }

    Column(
        modifier = Modifier
            .fillMaxWidth()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 20.dp)
            .navigationBarsPadding()
            .padding(bottom = 24.dp)
    ) {
        Text(
            text = record.cleanFilename,
            fontSize = 17.sp,
            fontWeight = FontWeight.SemiBold,
            color = androidx.compose.ui.graphics.Color.White
        )
        Spacer(Modifier.height(14.dp))

        // ── Básico ───────────────────────────────────────────────────────────
        val curated = state.metadata?.curated
        DetailRow("Data", formatCaptureDate(record.fileMtime, curated))
        curated?.text("device")?.let { DetailRow("Dispositivo", it) }
        curated?.text("source_app")?.let { DetailRow("Origem", it) }
        curated?.text("location_label")?.let { DetailRow("Local", it) }
        DetailRow("Tipo", if (record.isVideo) "Vídeo" else "Imagem")
        record.fileSize?.let { DetailRow("Tamanho", formatBytes(it)) }

        if (record.collections.isNotEmpty()) {
            Spacer(Modifier.height(12.dp))
            SectionLabel("Álbuns")
            ChipRow(record.collections.map { it.name.ifBlank { "Álbum #${it.id}" } })
        }

        if (record.tagsList.isNotEmpty()) {
            Spacer(Modifier.height(12.dp))
            SectionLabel("Tags")
            ChipRow(record.tagsList)
        }

        if (record.persons.isNotEmpty()) {
            Spacer(Modifier.height(12.dp))
            SectionLabel("Pessoas")
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .horizontalScroll(rememberScrollState()),
                horizontalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                record.persons.forEach { person ->
                    val nome = person.name?.ifBlank { null } ?: "Pessoa #${person.id}"
                    AssistChip(
                        onClick = { onPersonClick(person.id, nome) },
                        label = { Text(nome) },
                        colors = AssistChipDefaults.assistChipColors(labelColor = IrisAccentLime)
                    )
                }
            }
        }

        // ── Avançados ────────────────────────────────────────────────────────
        Spacer(Modifier.height(18.dp))
        TextButton(onClick = { advancedVisible = !advancedVisible }) {
            Text(
                text = "Avançados",
                color = IrisAccentLime,
                fontSize = 14.sp,
                fontWeight = FontWeight.SemiBold
            )
            Spacer(Modifier.width(4.dp))
            Icon(
                imageVector = if (advancedVisible) Icons.Default.ExpandLess else Icons.Default.ExpandMore,
                contentDescription = null,
                tint = IrisAccentLime,
                modifier = Modifier.size(18.dp)
            )
        }

        if (advancedVisible) {
            record.resolvedPath?.let { DetailRow("Caminho", it) }
            state.metadata?.full?.let { full ->
                full.text("format")?.let { DetailRow("Formato", it) }
                val largura = full.text("width")
                val altura = full.text("height")
                if (largura != null && altura != null) DetailRow("Dimensões", "$largura × $altura")
                full.text("duration")?.let { DetailRow("Duração", it) }
            }

            if (!record.descricaoIa.isNullOrBlank()) {
                Spacer(Modifier.height(12.dp))
                SectionLabel("Descrição automática")
                BodyText(record.descricaoIa)
            }

            if (!record.textoExtraido.isNullOrBlank()) {
                Spacer(Modifier.height(12.dp))
                SectionLabel("Texto reconhecido")
                BodyText(record.textoExtraido)
            }

            if (state.similarRecords.isNotEmpty()) {
                Spacer(Modifier.height(12.dp))
                SectionLabel("Parecidas")
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
}

@Composable
private fun SectionLabel(text: String) {
    Text(text = text, fontSize = 12.sp, color = IrisTextMuted)
    Spacer(Modifier.height(6.dp))
}

@Composable
private fun DetailRow(label: String, value: String) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 5.dp),
        verticalAlignment = Alignment.Top
    ) {
        Text(text = label, fontSize = 13.sp, color = IrisTextMuted, modifier = Modifier.width(112.dp))
        Text(text = value, fontSize = 13.sp, color = IrisTextSoft)
    }
}

@Composable
private fun BodyText(text: String) {
    Box(
        modifier = Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(10.dp))
            .background(IrisDarkSurface)
            .padding(12.dp)
    ) {
        Text(text = text, fontSize = 13.sp, color = IrisTextSoft)
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
                    .clip(RoundedCornerShape(8.dp))
                    .background(IrisDarkSurface)
                    .padding(horizontal = 10.dp, vertical = 6.dp)
            ) {
                Text(text = value, fontSize = 12.sp, color = IrisTextSoft)
            }
        }
    }
}

/** Reads a field out of the server's free-form metadata object. */
private fun JsonObject.text(key: String): String? {
    val element = this[key] as? JsonPrimitive ?: return null
    val value = element.content
    return value.ifBlank { null }
}

private fun formatCaptureDate(fileMtime: Double?, curated: JsonObject?): String {
    curated?.text("captured_at")?.let { return it }
    if (fileMtime == null || fileMtime <= 0.0) return "Desconhecida"
    val formatter = SimpleDateFormat("d 'de' MMMM 'de' yyyy, HH:mm", Locale("pt", "BR"))
    return formatter.format(Date((fileMtime * 1000).toLong()))
}

private fun formatBytes(bytes: Long): String = when {
    bytes >= 1_073_741_824 -> String.format(Locale.US, "%.1f GB", bytes / 1_073_741_824.0)
    bytes >= 1_048_576 -> String.format(Locale.US, "%.1f MB", bytes / 1_048_576.0)
    bytes >= 1024 -> String.format(Locale.US, "%.0f KB", bytes / 1024.0)
    else -> "$bytes B"
}
