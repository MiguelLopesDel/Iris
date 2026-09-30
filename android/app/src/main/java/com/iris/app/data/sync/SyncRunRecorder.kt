package com.iris.app.data.sync

import com.iris.app.data.local.UploadDatabaseHelper
import com.iris.app.data.model.SyncRunOutcome
import com.iris.app.data.model.SyncRunTrigger
import android.util.Log
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

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
    private var meterRunAtStart = 0L

    suspend fun start(trigger: SyncRunTrigger, startedInForeground: Boolean) {
        meterRunAtStart = speedMeter.snapshot().runNumber
        val id = bestEffort("start") {
            dbHelper.insertSyncRun(accountKey, wallClockMillis(), trigger, startedInForeground)
        } ?: return
        runId = id
        activeRun.value = id
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
            activeRun.compareAndSet(id, null)
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
        val snapshot = speedMeter.snapshot()
        // No queue pass since this run started: the meter still describes an
        // earlier run, whose bytes are not this run's.
        if (snapshot.runNumber == meterRunAtStart) return Totals(0L, 0L, 0L)
        return Totals(snapshot.runBytes, snapshot.runItems, snapshot.runElapsedMillis)
    }

    companion object {
        private const val TAG = "SyncRunRecorder"
        private val activeRun = MutableStateFlow<Long?>(null)

        /** The run executing in this process, if any. A stored RUNNING row that is not this one was interrupted. */
        val activeRunId: StateFlow<Long?> = activeRun.asStateFlow()
    }
}
