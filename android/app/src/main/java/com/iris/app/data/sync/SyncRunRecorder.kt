package com.iris.app.data.sync

import com.iris.app.data.local.UploadDatabaseHelper
import com.iris.app.data.model.SyncRunOutcome
import com.iris.app.data.model.SyncRunTrigger
import android.util.Log
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update

/**
 * Writes one worker execution into the persisted sync history. The row is
 * created when the run starts, so a run whose process is killed still leaves
 * a trace; checkpoints keep its byte count current until then. History is
 * diagnostic: a storage failure here is logged and never stops the sync.
 */
internal class SyncRunRecorder(
    private val dbHelper: UploadDatabaseHelper,
    private val speedMeter: UploadSpeedMeter,
    private val accountKey: String,
    private val wallClockMillis: () -> Long = System::currentTimeMillis,
) {
    private var runId: Long? = null
    /** The meter run this worker's own queue pass started, if it reached the queue. */
    @Volatile private var meterRun: Long? = null
    @Volatile private var finishedTotals: UploadSpeedSnapshot? = null

    suspend fun start(trigger: SyncRunTrigger, startedInForeground: Boolean) {
        val id = bestEffort("start") {
            dbHelper.insertSyncRun(accountKey, wallClockMillis(), trigger, startedInForeground)
        } ?: return
        runId = id
        activeRuns.update { it + id }
    }

    /** Hooks for [SyncUploadManager.processQueue], so only this worker's pass is credited to it. */
    fun queueRunStarted(meterRunNumber: Long) {
        meterRun = meterRunNumber
    }

    fun queueRunFinished(totals: UploadSpeedSnapshot) {
        if (totals.runNumber == meterRun) finishedTotals = totals
    }

    suspend fun checkpoint() {
        val id = runId ?: return
        val totals = totals()
        bestEffort("checkpoint") {
            dbHelper.updateSyncRunProgress(accountKey, id, totals.bytes, totals.items, totals.uploadMillis)
        }
    }

    suspend fun finish(outcome: SyncRunOutcome, stopReason: Int? = null, detail: String? = null) {
        val id = runId ?: return
        runId = null
        try {
            val totals = totals()
            bestEffort("finish") {
                if (outcome == SyncRunOutcome.COMPLETED && totals.bytes == 0L && totals.items == 0L) {
                    // A periodic check with nothing to send is not worth a history row.
                    dbHelper.deleteSyncRun(accountKey, id)
                } else {
                    dbHelper.finishSyncRun(
                        accountKey, id, wallClockMillis(), totals.bytes, totals.items,
                        totals.uploadMillis, outcome, stopReason, detail,
                    )
                    if (outcome == SyncRunOutcome.SKIPPED) {
                        // Every periodic check repeats the same skip; the latest one says it.
                        dbHelper.deleteOtherSyncRuns(accountKey, SyncRunOutcome.SKIPPED, keepId = id)
                    }
                }
            }
        } finally {
            activeRuns.update { it - id }
        }
    }

    private suspend fun <T> bestEffort(step: String, block: suspend () -> T): T? = try {
        block()
    } catch (cancelled: CancellationException) {
        throw cancelled
    } catch (failure: Exception) {
        Log.w(TAG, "Sync history step=$step error=${failure.javaClass.simpleName}")
        null
    }

    private data class Totals(val bytes: Long, val items: Long, val uploadMillis: Long)

    private fun totals(): Totals {
        // The meter is shared: while this worker waits for the queue or after
        // its pass ends, it describes another worker's pass, not this one.
        val snapshot = finishedTotals
            ?: speedMeter.snapshot().takeIf { meterRun != null && it.runNumber == meterRun }
            ?: return Totals(0L, 0L, 0L)
        return Totals(snapshot.runBytes, snapshot.runItems, snapshot.runElapsedMillis)
    }

    companion object {
        private const val TAG = "SyncRunRecorder"
        private val activeRuns = MutableStateFlow<Set<Long>>(emptySet())

        /** Runs executing in this process. A stored RUNNING row not among them was interrupted. */
        val activeRunIds: StateFlow<Set<Long>> = activeRuns.asStateFlow()
    }
}
