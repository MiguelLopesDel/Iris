package com.iris.app.ui.components

import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CloudOff
import androidx.compose.material.icons.filled.Error
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import com.iris.app.R
import com.iris.app.data.model.CloudConnectionState
import com.iris.app.data.model.CloudSyncStatus
import com.iris.app.ui.theme.IrisDanger
import com.iris.app.ui.theme.IrisDarkSurface

@Composable
fun CloudSyncNotice(
    status: CloudSyncStatus,
    modifier: Modifier = Modifier,
    serverUnavailable: Boolean = false,
    queueRefreshFailed: Boolean = false,
) {
    val message = when {
        serverUnavailable || status.connectionState == CloudConnectionState.OFFLINE ->
            stringResource(R.string.cloud_connection_unavailable)
        status.syncError != null -> stringResource(R.string.cloud_sync_retrying)
        queueRefreshFailed -> stringResource(R.string.cloud_queue_numbers_stale)
        else -> null
    } ?: return
    val offline = serverUnavailable || status.connectionState == CloudConnectionState.OFFLINE

    Card(
        modifier = modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = IrisDarkSurface),
    ) {
        Row(
            modifier = Modifier.padding(horizontal = 14.dp, vertical = 12.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Icon(
                imageVector = if (offline) Icons.Default.CloudOff else Icons.Default.Error,
                contentDescription = null,
                tint = if (offline) IrisDanger else MaterialTheme.colorScheme.secondary,
            )
            Text(
                text = message,
                modifier = Modifier.padding(start = 10.dp),
                style = MaterialTheme.typography.bodyMedium,
            )
        }
    }
}
