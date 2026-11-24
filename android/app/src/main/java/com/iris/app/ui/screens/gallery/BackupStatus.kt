package com.iris.app.ui.screens.gallery

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
enum class BackupState(val description: String) {
    SIGNED_OUT("Entrar para fazer backup"),
    OFFLINE("Sem conexão com o servidor"),
    ATTENTION("O backup precisa de atenção"),
    OK("Backup em dia"),
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
