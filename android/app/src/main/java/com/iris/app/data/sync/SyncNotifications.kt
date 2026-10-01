package com.iris.app.data.sync

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.work.ForegroundInfo
import com.iris.app.MainActivity
import com.iris.app.R
import com.iris.app.ui.screens.sync.SyncMetricsFormat

/**
 * The ongoing notification of a backup running as a foreground data-sync
 * service. Being a foreground service is what lets an upload continue past
 * Android's few-minute limit for background work with the app closed.
 */
object SyncNotifications {
    const val CHANNEL_ID = "iris_sync"
    const val NOTIFICATION_ID = 4101

    /** What the notification says, decided without Android so it can be tested on the JVM. */
    sealed interface Content {
        data object Preparing : Content
        data class Uploading(val bytesPerSecond: Double, val items: Long, val remainingBytes: Long) : Content
    }

    fun content(speed: UploadSpeedSnapshot, remainingBytes: Long): Content =
        if (!speed.running || (speed.runBytes == 0L && speed.runItems == 0L)) {
            Content.Preparing
        } else {
            Content.Uploading(speed.currentBytesPerSecond, speed.runItems, remainingBytes)
        }

    fun foregroundInfo(context: Context, content: Content): ForegroundInfo {
        ensureChannel(context)
        val text = when (content) {
            Content.Preparing -> context.getString(R.string.sync_notification_preparing)
            // Speed counts acknowledged bytes; until the first large chunk is
            // acknowledged there is no rate yet, and "0 MB/s" would be false.
            is Content.Uploading -> if (content.bytesPerSecond < 1.0) {
                context.getString(
                    R.string.sync_notification_progress_no_rate,
                    content.items,
                    SyncMetricsFormat.bytes(content.remainingBytes),
                )
            } else if (content.remainingBytes > 0L) {
                context.getString(
                    R.string.sync_notification_progress,
                    SyncMetricsFormat.speed(content.bytesPerSecond),
                    content.items,
                    SyncMetricsFormat.bytes(content.remainingBytes),
                )
            } else {
                context.getString(
                    R.string.sync_notification_progress_done,
                    SyncMetricsFormat.speed(content.bytesPerSecond),
                    content.items,
                )
            }
        }
        val openApp = PendingIntent.getActivity(
            context,
            0,
            Intent(context, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val notification = NotificationCompat.Builder(context, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_sync_notification)
            .setContentTitle(context.getString(R.string.sync_notification_title))
            .setContentText(text)
            .setContentIntent(openApp)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setSilent(true)
            .setForegroundServiceBehavior(NotificationCompat.FOREGROUND_SERVICE_IMMEDIATE)
            .build()
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            ForegroundInfo(NOTIFICATION_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC)
        } else {
            ForegroundInfo(NOTIFICATION_ID, notification)
        }
    }

    private fun ensureChannel(context: Context) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val manager = context.getSystemService(NotificationManager::class.java) ?: return
        if (manager.getNotificationChannel(CHANNEL_ID) != null) return
        manager.createNotificationChannel(
            NotificationChannel(
                CHANNEL_ID,
                context.getString(R.string.sync_notification_channel),
                NotificationManager.IMPORTANCE_LOW,
            ).apply { setShowBadge(false) }
        )
    }
}
