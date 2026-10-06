package com.iris.app

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.iris.app.data.local.UploadDatabaseHelper
import com.iris.app.data.model.SyncRunOutcome
import com.iris.app.data.model.SyncRunTrigger
import com.iris.app.data.model.UploadJobState
import com.iris.app.data.sync.SyncRunRecorder
import com.iris.app.data.sync.UploadSpeedMeter
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith

/** The persisted upload history: schema migration, storage, and what the recorder keeps. */
@RunWith(AndroidJUnit4::class)
class SyncRunHistoryTest {
    private val context = InstrumentationRegistry.getInstrumentation().targetContext
    private val databaseName = "sync-run-history-test.db"
    private lateinit var db: UploadDatabaseHelper

    @Before
    fun setUp() {
        context.deleteDatabase(databaseName)
        db = UploadDatabaseHelper(context, databaseName)
    }

    @After
    fun tearDown() {
        db.close()
        context.deleteDatabase(databaseName)
    }

    @Test
    fun upgrading_from_version_4_adds_the_history_and_keeps_the_queue() = runBlocking {
        db.insertOrIgnoreJob("account-a", "content://media/1", "a.jpg", 10L, "h1", "2026-01-01T00:00:00Z")
        db.writableDatabase.execSQL("DROP TABLE sync_runs")
        db.writableDatabase.version = 4
        db.close()

        db = UploadDatabaseHelper(context, databaseName)
        assertEquals(UploadDatabaseHelper.DATABASE_VERSION, db.readableDatabase.version)
        assertEquals(1, db.countsByState("account-a")[UploadJobState.QUEUED])
        val id = db.insertSyncRun("account-a", 1_000L, SyncRunTrigger.MANUAL, startedInForeground = true)
        assertEquals(listOf(id), db.recentSyncRuns("account-a", 10).map { it.id })
    }

    @Test
    fun upgrading_from_version_5_adds_the_foreground_service_column() = runBlocking {
        val before = db.insertSyncRun("account-a", 1_000L, SyncRunTrigger.AUTOMATIC, startedInForeground = false)
        db.writableDatabase.execSQL("ALTER TABLE sync_runs DROP COLUMN foreground_service")
        db.writableDatabase.version = 5
        db.close()

        db = UploadDatabaseHelper(context, databaseName)
        assertEquals(UploadDatabaseHelper.DATABASE_VERSION, db.readableDatabase.version)
        assertNull("A run from before the upgrade has no answer", db.recentSyncRuns("account-a", 10).single().foregroundService)
        db.markSyncRunForeground("account-a", before, started = true)
        assertEquals(true, db.recentSyncRuns("account-a", 10).single().foregroundService)
    }

    @Test
    fun upgrading_from_version_10_requeues_items_refused_for_their_hash() = runBlocking {
        // Photos hashed before location access was granted were refused once
        // for a hash mismatch; after the upgrade they are hashed again and sent.
        val refused = db.insertOrIgnoreJob("account-a", "content://media/1", "a.jpg", 10L, "h1", "2026-01-01T00:00:00Z")
        db.updateJobState("account-a", refused, UploadJobState.FAILED, "Hash do arquivo não confere")
        val other = db.insertOrIgnoreJob("account-a", "content://media/2", "b.jpg", 10L, "h2", "2026-01-01T00:00:00Z")
        db.updateJobState("account-a", other, UploadJobState.FAILED, "O arquivo não existe mais no aparelho")
        db.writableDatabase.execSQL("ALTER TABLE upload_jobs DROP COLUMN hashed_with_location")
        db.writableDatabase.version = 10
        db.close()

        db = UploadDatabaseHelper(context, databaseName)
        assertEquals(UploadDatabaseHelper.DATABASE_VERSION, db.readableDatabase.version)
        val jobs = db.getAllJobs("account-a").associateBy { it.filename }
        assertEquals(UploadJobState.QUEUED, jobs.getValue("a.jpg").state)
        assertEquals("Only hash refusals are retried", UploadJobState.FAILED, jobs.getValue("b.jpg").state)
        assertEquals("Rows from before read as hashed without location", false, jobs.getValue("a.jpg").hashedWithLocation)
    }

    @Test
    fun a_run_is_stored_with_progress_outcome_and_stop_reason() = runBlocking {
        val id = db.insertSyncRun("account-a", 1_000L, SyncRunTrigger.PERIODIC, startedInForeground = false)
        db.updateSyncRunProgress("account-a", id, bytes = 5_000L, items = 2L, uploadMillis = 700L)

        val running = db.recentSyncRuns("account-a", 10).single()
        assertEquals(SyncRunOutcome.RUNNING, running.outcome)
        assertNull(running.endedAtMillis)
        assertEquals(5_000L, running.bytes)
        assertEquals(false, running.startedInForeground)

        db.finishSyncRun("account-a", id, 9_000L, 8_000L, 3L, 900L, SyncRunOutcome.STOPPED, 8, null)
        // A checkpoint arriving after the end must not reopen or overwrite the run.
        db.updateSyncRunProgress("account-a", id, bytes = 1L, items = 1L, uploadMillis = 1L)

        val finished = db.recentSyncRuns("account-a", 10).single()
        assertEquals(SyncRunOutcome.STOPPED, finished.outcome)
        assertEquals(9_000L, finished.endedAtMillis)
        assertEquals(8_000L, finished.bytes)
        assertEquals(8, finished.stopReason)
        assertEquals(SyncRunTrigger.PERIODIC, finished.trigger)
    }

    @Test
    fun history_is_per_account_and_bounded() = runBlocking {
        repeat(UploadDatabaseHelper.SYNC_RUN_HISTORY_LIMIT + 5) { index ->
            db.insertSyncRun("account-a", index.toLong(), SyncRunTrigger.AUTOMATIC, startedInForeground = false)
        }
        db.insertSyncRun("account-b", 0L, SyncRunTrigger.MANUAL, startedInForeground = true)

        val runs = db.recentSyncRuns("account-a", 1_000)
        assertEquals(UploadDatabaseHelper.SYNC_RUN_HISTORY_LIMIT, runs.size)
        assertEquals((UploadDatabaseHelper.SYNC_RUN_HISTORY_LIMIT + 4).toLong(), runs.first().startedAtMillis)
        assertEquals(1, db.recentSyncRuns("account-b", 1_000).size)
    }

    @Test
    fun remaining_bytes_count_only_what_is_still_to_send() = runBlocking {
        val queued = db.insertOrIgnoreJob("account-a", "content://media/1", "a.jpg", 1_000L, "h1", "t")
        db.insertOrIgnoreJob("account-a", "content://media/2", "b.jpg", 500L, "h2", "t")
        val done = db.insertOrIgnoreJob("account-a", "content://media/3", "c.jpg", 9_999L, "h3", "t")
        db.insertOrIgnoreJob("account-b", "content://media/4", "d.jpg", 7_777L, "h4", "t")
        db.updateOffsetTransactionally("account-a", queued, 400L)
        db.updateJobState("account-a", done, UploadJobState.READY)

        assertEquals(600L + 500L, db.remainingUploadBytes("account-a"))
    }

    @Test
    fun recorder_drops_idle_runs_and_keeps_real_ones() = runBlocking {
        val meter = UploadSpeedMeter()

        val idle = SyncRunRecorder(db, meter, "account-a")
        idle.start(SyncRunTrigger.PERIODIC, startedInForeground = false)
        assertEquals(1, SyncRunRecorder.activeRunIds.value.size)
        idle.finish(SyncRunOutcome.COMPLETED)
        assertTrue(db.recentSyncRuns("account-a", 10).isEmpty())
        assertTrue(SyncRunRecorder.activeRunIds.value.isEmpty())

        val busy = SyncRunRecorder(db, meter, "account-a")
        busy.start(SyncRunTrigger.MANUAL, startedInForeground = true)
        busy.queueRunStarted(meter.startRun())
        meter.recordAcknowledged(3_000_000L)
        meter.recordConfirmedItem()
        meter.finishRun()
        busy.queueRunFinished(meter.snapshot())
        busy.finish(SyncRunOutcome.COMPLETED)

        val run = db.recentSyncRuns("account-a", 10).single()
        assertEquals(3_000_000L, run.bytes)
        assertEquals(1L, run.items)
        assertEquals(SyncRunOutcome.COMPLETED, run.outcome)
    }

    @Test
    fun concurrent_runs_each_credit_only_their_own_queue_pass() = runBlocking {
        val meter = UploadSpeedMeter()
        val first = SyncRunRecorder(db, meter, "account-a")
        val second = SyncRunRecorder(db, meter, "account-a")
        first.start(SyncRunTrigger.PERIODIC, startedInForeground = false)
        second.start(SyncRunTrigger.MANUAL, startedInForeground = false)
        assertEquals(2, SyncRunRecorder.activeRunIds.value.size)

        // The first owns the queue; the second waits for it.
        first.queueRunStarted(meter.startRun())
        meter.recordAcknowledged(5_000_000L)
        meter.finishRun()
        first.queueRunFinished(meter.snapshot())

        // The second's own pass starts before the first records its end.
        second.queueRunStarted(meter.startRun())
        meter.recordAcknowledged(2_000_000L)
        first.finish(SyncRunOutcome.COMPLETED)
        assertEquals("One run is still active", 1, SyncRunRecorder.activeRunIds.value.size)
        meter.finishRun()
        second.queueRunFinished(meter.snapshot())
        second.finish(SyncRunOutcome.COMPLETED)

        val runs = db.recentSyncRuns("account-a", 10).associateBy { it.trigger }
        assertEquals(5_000_000L, runs.getValue(SyncRunTrigger.PERIODIC).bytes)
        assertEquals(2_000_000L, runs.getValue(SyncRunTrigger.MANUAL).bytes)
        assertTrue(SyncRunRecorder.activeRunIds.value.isEmpty())
    }

    @Test
    fun recorder_does_not_credit_a_run_with_an_earlier_runs_bytes() = runBlocking {
        val meter = UploadSpeedMeter()
        meter.startRun()
        meter.recordAcknowledged(9_000_000L)
        meter.finishRun()

        // This run never reached the upload queue (the server was unreachable).
        val recorder = SyncRunRecorder(db, meter, "account-a")
        recorder.start(SyncRunTrigger.AUTOMATIC, startedInForeground = false)
        recorder.finish(SyncRunOutcome.RETRY, detail = "server_unreachable")

        val run = db.recentSyncRuns("account-a", 10).single()
        assertEquals(0L, run.bytes)
        assertEquals("server_unreachable", run.detail)
    }

    @Test
    fun only_the_latest_skipped_run_is_kept() = runBlocking {
        val meter = UploadSpeedMeter()
        repeat(3) {
            val recorder = SyncRunRecorder(db, meter, "account-a")
            recorder.start(SyncRunTrigger.PERIODIC, startedInForeground = false)
            recorder.finish(SyncRunOutcome.SKIPPED, detail = "wifi_required")
        }
        val runs = db.recentSyncRuns("account-a", 10)
        assertEquals(1, runs.size)
        assertEquals(SyncRunOutcome.SKIPPED, runs.single().outcome)
    }

    @Test
    fun the_history_table_exists_on_a_fresh_install() {
        val tables = db.readableDatabase.rawQuery(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'sync_runs'", null
        ).use { it.count }
        assertEquals(1, tables)
    }
}
