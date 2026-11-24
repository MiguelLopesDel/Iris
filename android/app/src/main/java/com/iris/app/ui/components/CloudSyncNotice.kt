package com.iris.app.ui.components

import androidx.compose.foundation.layout.Column
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
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import com.iris.app.R
import com.iris.app.data.model.CloudConnectionState
import com.iris.app.data.model.CloudSyncStatus
import com.iris.app.data.net.Tailscale
import com.iris.app.ui.theme.IrisDanger
import com.iris.app.ui.theme.IrisSurface

@Composable
fun CloudSyncNotice(
    status: CloudSyncStatus,
    modifier: Modifier = Modifier,
    serverUnavailable: Boolean = false,
    queueRefreshFailed: Boolean = false,
    /** Why the server did not answer, in plain words, added to the offline message when known. */
    offlineReason: String? = null,
) {
    val context = LocalContext.current
    val offline = serverUnavailable || status.connectionState == CloudConnectionState.OFFLINE
    val tailscaleIntent = if (offline) Tailscale.launchIntent(context) else null
    val tailscaleHint = Tailscale.hint(
        offline = offline,
        installed = tailscaleIntent != null,
        vpnActive = offline && Tailscale.vpnActive(context),
    )
    val message = when {
        offline -> listOfNotNull(
            stringResource(R.string.cloud_connection_unavailable),
            offlineReason,
            if (tailscaleHint == Tailscale.Hint.OPEN_VPN_OFF) stringResource(R.string.tailscale_vpn_off) else null,
        ).joinToString("\n\n")
        status.syncError != null -> stringResource(R.string.cloud_sync_retrying)
        queueRefreshFailed -> stringResource(R.string.cloud_queue_numbers_stale)
        else -> null
    } ?: return

    Card(
        modifier = modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = IrisSurface),
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
            Column(modifier = Modifier.padding(start = 10.dp)) {
                Text(text = message, style = MaterialTheme.typography.bodyMedium)
                if (tailscaleHint != Tailscale.Hint.NONE && tailscaleIntent != null) {
                    TextButton(onClick = { context.startActivity(tailscaleIntent) }) {
                        Text(stringResource(R.string.tailscale_open))
                    }
                }
            }
        }
    }
}
