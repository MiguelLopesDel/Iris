package com.iris.app.ui.screens.sync

import android.Manifest
import android.annotation.SuppressLint
import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.PowerManager
import android.provider.Settings
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.ContextCompat
import com.iris.app.R
import com.iris.app.ui.theme.IrisSurface
import com.iris.app.ui.theme.IrisTextSoft

/**
 * What Android allows the backup to do with the app closed. Without the
 * battery exemption a background run cannot become a foreground service and
 * is stopped after a few minutes; without notifications it runs unseen.
 */
internal data class BackgroundSyncAccess(
    val batteryUnrestricted: Boolean,
    val notificationsAllowed: Boolean,
) {
    val complete: Boolean get() = batteryUnrestricted && notificationsAllowed

    companion object {
        fun current(context: Context): BackgroundSyncAccess {
            val power = context.getSystemService(PowerManager::class.java)
            val battery = power?.isIgnoringBatteryOptimizations(context.packageName) ?: true
            val notifications = Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU ||
                ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) ==
                PackageManager.PERMISSION_GRANTED
            return BackgroundSyncAccess(battery, notifications)
        }
    }
}

/** Opens Android's prompt to exempt Iris from battery optimization, or the settings list as a fallback. */
@SuppressLint("BatteryLife") // Backing up media with the app closed is this app's core function.
internal fun requestBatteryExemption(context: Context) {
    val prompt = Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS)
        .setData(Uri.parse("package:${context.packageName}"))
    try {
        context.startActivity(prompt)
    } catch (_: ActivityNotFoundException) {
        context.startActivity(Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS))
    }
}

@Composable
internal fun BackgroundSyncAccessCard(
    access: BackgroundSyncAccess,
    onAllowBattery: () -> Unit,
    onAllowNotifications: () -> Unit,
) {
    Card(
        shape = RoundedCornerShape(16.dp),
        colors = CardDefaults.cardColors(containerColor = IrisSurface),
        modifier = Modifier.fillMaxWidth()
    ) {
        Column(modifier = Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(stringResource(R.string.sync_background_title), fontWeight = FontWeight.SemiBold, fontSize = 15.sp)
            if (!access.batteryUnrestricted) {
                Text(stringResource(R.string.sync_background_battery), fontSize = 12.sp, color = IrisTextSoft)
                OutlinedButton(onClick = onAllowBattery, modifier = Modifier.fillMaxWidth()) {
                    Text(stringResource(R.string.sync_background_battery_action), fontSize = 13.sp)
                }
            }
            if (!access.notificationsAllowed) {
                Text(stringResource(R.string.sync_background_notifications), fontSize = 12.sp, color = IrisTextSoft)
                OutlinedButton(onClick = onAllowNotifications, modifier = Modifier.fillMaxWidth()) {
                    Text(stringResource(R.string.sync_background_notifications_action), fontSize = 13.sp)
                }
            }
        }
    }
}
