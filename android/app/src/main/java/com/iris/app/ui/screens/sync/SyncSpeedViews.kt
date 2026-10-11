package com.iris.app.ui.screens.sync

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.iris.app.R
import com.iris.app.data.model.SyncRun
import com.iris.app.data.model.SyncRunOutcome
import com.iris.app.data.model.SyncRunTrigger
import com.iris.app.data.sync.ServerSpeedTest
import com.iris.app.data.sync.UploadSpeedSnapshot
import com.iris.app.ui.theme.IrisAccent
import com.iris.app.ui.theme.IrisDanger
import com.iris.app.ui.theme.IrisSurface
import com.iris.app.ui.theme.IrisTextMuted
import com.iris.app.ui.theme.IrisTextSoft
import java.text.DateFormat
import java.util.Date

/**
 * Live throughput while uploading, and what the last run achieved once it
 * ends. Rates count only bytes the server confirmed as saved.
 */
@Composable
internal fun UploadSpeedPanel(speed: UploadSpeedSnapshot, remainingBytes: Long) {
    if (speed.runNumber == 0L) return
    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        if (speed.running) {
            Text(
                stringResource(R.string.sync_speed_now, SyncMetricsFormat.speed(speed.currentBytesPerSecond)),
                fontSize = 15.sp,
                fontWeight = FontWeight.SemiBold,
                color = IrisAccent
            )
            Text(
                stringResource(R.string.sync_speed_average, SyncMetricsFormat.speed(speed.averageBytesPerSecond)),
                fontSize = 12.sp,
                color = IrisTextSoft
            )
            Text(
                stringResource(
                    R.string.sync_speed_run,
                    speed.runItems,
                    SyncMetricsFormat.bytes(speed.runBytes),
                    SyncMetricsFormat.duration(speed.runElapsedMillis)
                ),
                fontSize = 12.sp,
                color = IrisTextSoft
            )
            if (remainingBytes > 0L) {
                val eta = SyncMetricsFormat.remainingMillis(remainingBytes, speed.averageBytesPerSecond)
                Text(
                    if (eta != null) {
                        stringResource(
                            R.string.sync_speed_remaining,
                            SyncMetricsFormat.bytes(remainingBytes),
                            SyncMetricsFormat.duration(eta)
                        )
                    } else {
                        stringResource(R.string.sync_speed_remaining_unknown, SyncMetricsFormat.bytes(remainingBytes))
                    },
                    fontSize = 12.sp,
                    color = IrisTextSoft
                )
            }
        } else if (speed.runBytes > 0L) {
            Text(
                stringResource(
                    R.string.sync_speed_last_run,
                    SyncMetricsFormat.speed(speed.averageBytesPerSecond),
                    SyncMetricsFormat.bytes(speed.runBytes),
                    SyncMetricsFormat.duration(speed.runElapsedMillis)
                ),
                fontSize = 12.sp,
                color = IrisTextSoft
            )
        }
    }
}

/** One persisted run: when, how it started, what it sent, and why it ended. */
@Composable
internal fun SyncRunCard(run: SyncRun, activeRunIds: Set<Long>) {
    val started = remember(run.startedAtMillis) {
        DateFormat.getDateTimeInstance(DateFormat.SHORT, DateFormat.SHORT).format(Date(run.startedAtMillis))
    }
    val trigger = stringResource(
        when (run.trigger) {
            SyncRunTrigger.MANUAL -> R.string.sync_history_trigger_manual
            SyncRunTrigger.AUTOMATIC -> R.string.sync_history_trigger_automatic
            SyncRunTrigger.PERIODIC -> R.string.sync_history_trigger_periodic
        }
    )
    val appState = stringResource(
        if (run.startedInForeground) R.string.sync_history_foreground else R.string.sync_history_background
    )
    val interrupted = run.outcome == SyncRunOutcome.RUNNING && run.id !in activeRunIds
    val status = when {
        interrupted -> stringResource(R.string.sync_history_interrupted)
        run.outcome == SyncRunOutcome.RUNNING -> stringResource(R.string.sync_history_running)
        run.outcome == SyncRunOutcome.COMPLETED -> stringResource(R.string.sync_history_completed)
        run.outcome == SyncRunOutcome.RETRY -> stringResource(R.string.sync_history_retry)
        run.outcome == SyncRunOutcome.STOPPED -> stringResource(R.string.sync_history_stopped)
        else -> stringResource(R.string.sync_history_skipped)
    }
    val reason = runReason(run)
    val average = if (run.uploadMillis > 0L) run.bytes * 1_000.0 / run.uploadMillis else 0.0
    val failed = interrupted || run.outcome == SyncRunOutcome.STOPPED || run.outcome == SyncRunOutcome.RETRY

    Card(
        shape = RoundedCornerShape(12.dp),
        colors = CardDefaults.cardColors(containerColor = IrisSurface),
        modifier = Modifier.fillMaxWidth()
    ) {
        Column(modifier = Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(3.dp)) {
            Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                Text(started, fontSize = 13.sp, fontWeight = FontWeight.SemiBold)
                Text("$trigger · $appState", fontSize = 12.sp, color = IrisTextMuted)
            }
            Text(
                stringResource(
                    R.string.sync_history_totals,
                    SyncMetricsFormat.bytes(run.bytes),
                    run.items,
                    SyncMetricsFormat.duration(run.uploadMillis)
                ),
                fontSize = 12.sp,
                color = IrisTextSoft
            )
            if (run.bytes > 0L) {
                Text(
                    stringResource(R.string.sync_history_average, SyncMetricsFormat.speed(average)),
                    fontSize = 12.sp,
                    color = IrisTextSoft
                )
            }
            run.foregroundService?.let { asService ->
                Text(
                    stringResource(
                        if (asService) R.string.sync_history_foreground_service
                        else R.string.sync_history_foreground_refused
                    ),
                    fontSize = 12.sp,
                    color = IrisTextMuted
                )
            }
            Text(
                if (reason != null) "$status: $reason" else status,
                fontSize = 12.sp,
                color = if (failed) IrisDanger else IrisTextSoft
            )
        }
    }
}

@Composable
private fun runReason(run: SyncRun): String? {
    val cause = SyncMetricsFormat.stopCause(run.stopReason)
    if (cause != SyncMetricsFormat.StopCause.NONE) {
        return when (cause) {
            SyncMetricsFormat.StopCause.TIME_LIMIT -> stringResource(R.string.sync_stop_time_limit)
            SyncMetricsFormat.StopCause.CONNECTION_LOST -> stringResource(R.string.sync_stop_connection_lost)
            SyncMetricsFormat.StopCause.CONDITIONS_CHANGED -> stringResource(R.string.sync_stop_conditions_changed)
            SyncMetricsFormat.StopCause.BATTERY_RESTRICTION -> stringResource(R.string.sync_stop_battery_restriction)
            SyncMetricsFormat.StopCause.REPLACED -> stringResource(R.string.sync_stop_replaced)
            SyncMetricsFormat.StopCause.USER -> stringResource(R.string.sync_stop_user)
            else -> stringResource(R.string.sync_stop_other, run.stopReason ?: 0)
        }
    }
    val detail = run.detail ?: return null
    return when {
        detail == "server_unreachable" -> stringResource(R.string.sync_detail_server_unreachable)
        detail == "upload_incomplete" -> stringResource(R.string.sync_detail_upload_incomplete)
        detail == "session_changed" -> stringResource(R.string.sync_detail_session_changed)
        detail == "wifi_required" -> stringResource(R.string.sync_detail_wifi_required)
        detail == "charging_required" -> stringResource(R.string.sync_detail_charging_required)
        detail == "constraints" -> stringResource(R.string.sync_detail_constraints)
        detail.startsWith("error_") -> stringResource(R.string.sync_detail_error, detail.removePrefix("error_"))
        else -> detail
    }
}

/** Speed test to the server: the ceiling the backup speed can be compared with. */
@Composable
internal fun ServerSpeedTestPanel(
    running: Boolean,
    results: List<ServerSpeedTest.Result>,
    error: String?,
    enabled: Boolean,
    onRun: () -> Unit,
) {
    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        OutlinedButton(onClick = onRun, enabled = enabled && !running, modifier = Modifier.fillMaxWidth()) {
            Text(
                stringResource(if (running) R.string.sync_speed_test_running else R.string.sync_speed_test_action),
                fontSize = 13.sp
            )
        }
        if (results.isEmpty() && error == null) {
            Text(stringResource(R.string.sync_speed_test_hint), fontSize = 11.sp, color = IrisTextMuted)
        }
        results.forEach { result ->
            val speed = SyncMetricsFormat.speed(result.bytesPerSecond)
            Text(
                when (result.phase) {
                    ServerSpeedTest.Phase.SINGLE_CONNECTION -> stringResource(R.string.sync_speed_test_single, speed)
                    ServerSpeedTest.Phase.PARALLEL_CONNECTIONS -> stringResource(
                        R.string.sync_speed_test_parallel, ServerSpeedTest.DEFAULT_PARALLEL_CONNECTIONS, speed
                    )
                    ServerSpeedTest.Phase.SERVER_DISK -> stringResource(R.string.sync_speed_test_disk, speed)
                },
                fontSize = 12.sp,
                color = IrisTextSoft
            )
        }
        if (error != null) {
            Text(stringResource(R.string.sync_speed_test_failed, error), fontSize = 12.sp, color = IrisDanger)
        }
    }
}
