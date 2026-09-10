package com.iris.app.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.iris.app.data.model.ServerInfo
import com.iris.app.ui.theme.IrisAccentLime
import com.iris.app.ui.theme.IrisDanger
import com.iris.app.ui.theme.IrisDarkSurfaceBright
import com.iris.app.ui.theme.IrisTextSoft

@Composable
fun ServerStatusBadge(
    serverInfo: ServerInfo?,
    isConnecting: Boolean,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    isServerOnline: Boolean = false,
    totalRecords: Int = 0,
    isDeviceLoggedIn: Boolean = true
) {
    val isOnline = isServerOnline || serverInfo != null
    val effectiveRecords = if (totalRecords > 0) totalRecords else (serverInfo?.records ?: 0)

    val dotColor = when {
        isConnecting -> Color(0xFFFFB300)
        !isOnline -> IrisDanger
        !isDeviceLoggedIn -> Color(0xFFFFB300)
        else -> IrisAccentLime
    }
    val label = when {
        isConnecting -> "Conectando…"
        !isOnline -> "Offline"
        effectiveRecords > 0 -> "$effectiveRecords mídias"
        !isDeviceLoggedIn -> "Login nec."
        else -> "Online"
    }

    Row(
        modifier = modifier
            .clip(RoundedCornerShape(20.dp))
            .background(IrisDarkSurfaceBright)
            .clickable(onClick = onClick)
            .padding(horizontal = 10.dp, vertical = 5.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Box(
            modifier = Modifier
                .size(8.dp)
                .background(dotColor, CircleShape)
        )
        Spacer(modifier = Modifier.width(6.dp))
        Text(
            text = label,
            fontSize = 12.sp,
            fontWeight = FontWeight.Medium,
            color = if (isOnline) MaterialTheme.colorScheme.onSurface else IrisTextSoft
        )
    }
}
