package com.iris.app.ui.screens.sync

import androidx.work.WorkInfo
import java.util.Locale
import kotlin.math.roundToLong

/**
 * Text for the sync speed card and history. Sizes and rates are decimal
 * (1 MB = 1,000,000 bytes) and rates also show Mbps, so they compare directly
 * with an Internet speed test.
 */
internal object SyncMetricsFormat {

    fun speed(bytesPerSecond: Double, locale: Locale = Locale.getDefault()): String {
        val megabytes = bytesPerSecond.coerceAtLeast(0.0) / 1_000_000.0
        val megabits = megabytes * 8.0
        return String.format(locale, "%.1f MB/s · %.0f Mbps", megabytes, megabits)
    }

    fun bytes(bytes: Long, locale: Locale = Locale.getDefault()): String {
        val value = bytes.coerceAtLeast(0L).toDouble()
        return when {
            value >= 1e9 -> String.format(locale, "%.2f GB", value / 1e9)
            value >= 1e6 -> String.format(locale, "%.1f MB", value / 1e6)
            value >= 1e3 -> String.format(locale, "%.0f kB", value / 1e3)
            else -> String.format(locale, "%d B", bytes.coerceAtLeast(0L))
        }
    }

    fun duration(millis: Long): String {
        val totalSeconds = (millis.coerceAtLeast(0L) / 1_000.0).roundToLong()
        val hours = totalSeconds / 3_600
        val minutes = (totalSeconds % 3_600) / 60
        val seconds = totalSeconds % 60
        return when {
            hours > 0 -> "%d h %02d min".format(hours, minutes)
            minutes > 0 -> "%d min %d s".format(minutes, seconds)
            else -> "%d s".format(seconds)
        }
    }

    /** Time left at [bytesPerSecond], or null when there is no rate to extrapolate from. */
    fun remainingMillis(remainingBytes: Long, bytesPerSecond: Double): Long? {
        if (remainingBytes <= 0L || bytesPerSecond < 1.0) return null
        return (remainingBytes / bytesPerSecond * 1_000.0).roundToLong()
    }

    /** Why the system stopped a run, grouped into what the user can act on. */
    fun stopCause(stopReason: Int?): StopCause = when (stopReason) {
        null, WorkInfo.STOP_REASON_NOT_STOPPED -> StopCause.NONE
        WorkInfo.STOP_REASON_TIMEOUT,
        WorkInfo.STOP_REASON_FOREGROUND_SERVICE_TIMEOUT -> StopCause.TIME_LIMIT
        WorkInfo.STOP_REASON_CONSTRAINT_CONNECTIVITY -> StopCause.CONNECTION_LOST
        WorkInfo.STOP_REASON_CONSTRAINT_CHARGING,
        WorkInfo.STOP_REASON_CONSTRAINT_BATTERY_NOT_LOW,
        WorkInfo.STOP_REASON_CONSTRAINT_DEVICE_IDLE,
        WorkInfo.STOP_REASON_CONSTRAINT_STORAGE_NOT_LOW -> StopCause.CONDITIONS_CHANGED
        WorkInfo.STOP_REASON_APP_STANDBY,
        WorkInfo.STOP_REASON_QUOTA,
        WorkInfo.STOP_REASON_BACKGROUND_RESTRICTION,
        WorkInfo.STOP_REASON_DEVICE_STATE -> StopCause.BATTERY_RESTRICTION
        WorkInfo.STOP_REASON_CANCELLED_BY_APP -> StopCause.REPLACED
        WorkInfo.STOP_REASON_USER -> StopCause.USER
        else -> StopCause.OTHER
    }

    enum class StopCause {
        NONE, TIME_LIMIT, CONNECTION_LOST, CONDITIONS_CHANGED, BATTERY_RESTRICTION, REPLACED, USER, OTHER
    }
}
