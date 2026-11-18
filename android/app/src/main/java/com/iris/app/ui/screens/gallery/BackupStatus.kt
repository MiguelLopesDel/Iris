package com.iris.app.ui.screens.gallery

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.ui.Alignment
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.iris.app.ui.theme.IrisSurfaceBright
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.AccountCircle
import androidx.compose.material.icons.filled.CloudDone
import androidx.compose.material.icons.filled.CloudOff
import androidx.compose.material.icons.filled.SyncProblem
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.graphics.vector.ImageVector
import com.iris.app.data.model.CloudConnectionState
import com.iris.app.ui.theme.IrisAccent
import com.iris.app.ui.theme.IrisDanger
import com.iris.app.ui.theme.IrisTextSoft

const val BACKUP_STATUS_TAG = "backup-status"

/** What the gallery's top bar says about the backup, at a glance. */
enum class BackupState(val description: String, val short: String) {
    SIGNED_OUT("Entrar para fazer backup", "Entrar"),
    OFFLINE("Sem conexão com o servidor", "Sem conexão"),
    ATTENTION("O backup precisa de atenção", "Backup com problema"),
    OK("Backup em dia", "Backup em dia"),
}

/** "Backup em dia · 1.234 itens": the state in words, with the library's size when known. */
fun backupLabel(state: BackupState, totalItems: Int): String {
    if (totalItems <= 0 || state == BackupState.SIGNED_OUT) return state.short
    val count = java.text.NumberFormat.getIntegerInstance(java.util.Locale("pt", "BR")).format(totalItems)
    return "${state.short} · $count ${if (totalItems == 1) "item" else "itens"}"
}

fun backupState(
    signedIn: Boolean,
    serverOnline: Boolean?,
    connectionState: CloudConnectionState,
    syncError: String?,
): BackupState = when {
    !signedIn -> BackupState.SIGNED_OUT
    serverOnline == false || connectionState == CloudConnectionState.OFFLINE -> BackupState.OFFLINE
    syncError != null -> BackupState.ATTENTION
    else -> BackupState.OK
}

/** The backup's state as one icon; tapping it opens the backup screen. */
@Composable
fun BackupStatusButton(state: BackupState, onClick: () -> Unit) {
    val (icon: ImageVector, tint) = when (state) {
        BackupState.SIGNED_OUT -> Icons.Default.AccountCircle to IrisTextSoft
        BackupState.OFFLINE -> Icons.Default.CloudOff to IrisDanger
        BackupState.ATTENTION -> Icons.Default.SyncProblem to MaterialTheme.colorScheme.secondary
        BackupState.OK -> Icons.Default.CloudDone to IrisAccent
    }
    IconButton(onClick = onClick, modifier = Modifier.testTag(BACKUP_STATUS_TAG)) {
        Icon(imageVector = icon, contentDescription = state.description, tint = tint)
    }
}

/** The backup's state as a short sentence with the library's size; tapping it opens the backup screen. */
@Composable
fun BackupStatusPill(state: BackupState, totalItems: Int, onClick: () -> Unit) {
    val (icon: ImageVector, tint) = when (state) {
        BackupState.SIGNED_OUT -> Icons.Default.AccountCircle to IrisTextSoft
        BackupState.OFFLINE -> Icons.Default.CloudOff to IrisDanger
        BackupState.ATTENTION -> Icons.Default.SyncProblem to MaterialTheme.colorScheme.secondary
        BackupState.OK -> Icons.Default.CloudDone to IrisAccent
    }
    Row(
        modifier = Modifier
            .testTag(BACKUP_STATUS_TAG)
            .clip(RoundedCornerShape(20.dp))
            .background(IrisSurfaceBright)
            .clickable(onClick = onClick)
            .padding(horizontal = 12.dp, vertical = 6.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Icon(imageVector = icon, contentDescription = null, tint = tint, modifier = Modifier.size(18.dp))
        Spacer(Modifier.width(8.dp))
        Text(
            text = backupLabel(state, totalItems),
            fontSize = 14.sp,
            fontWeight = FontWeight.SemiBold,
            color = MaterialTheme.colorScheme.onSurface,
            maxLines = 1,
        )
    }
}
