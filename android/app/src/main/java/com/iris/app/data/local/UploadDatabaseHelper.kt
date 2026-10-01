package com.iris.app.data.local

import android.content.ContentValues
import android.content.Context
import android.database.sqlite.SQLiteDatabase
import android.database.sqlite.SQLiteOpenHelper
import com.iris.app.data.model.LocalUploadJob
import com.iris.app.data.model.SyncRun
import com.iris.app.data.model.SyncRunOutcome
import com.iris.app.data.model.SyncRunTrigger
import com.iris.app.data.model.UploadJobState
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext

class UploadDatabaseHelper(
    context: Context,
    databaseName: String = DATABASE_NAME
) : SQLiteOpenHelper(
    context,
    databaseName,
    null,
    DATABASE_VERSION
) {

    private val writeMutex = Mutex()

    override fun onConfigure(db: SQLiteDatabase) {
        super.onConfigure(db)
        db.enableWriteAheadLogging()
        try {
            db.execSQL("PRAGMA busy_timeout = 5000")
        } catch (_: Exception) {}
    }

    suspend fun <T> runInWriteTransaction(block: (SQLiteDatabase) -> T): T = withContext(Dispatchers.IO) {
        writeMutex.withLock {
            val db = writableDatabase
            db.beginTransactionNonExclusive()
            try {
                val result = block(db)
                db.setTransactionSuccessful()
                result
            } finally {
                db.endTransaction()
            }
        }
    }

    override fun onCreate(db: SQLiteDatabase) {
        createUploadJobsTable(db)
        createSyncCursorsTable(db)
        createSyncRunsTable(db)
    }

    override fun onUpgrade(db: SQLiteDatabase, oldVersion: Int, newVersion: Int) {
        if (oldVersion < 2) {
            db.execSQL("ALTER TABLE upload_jobs ADD COLUMN source_id TEXT")
            db.execSQL("ALTER TABLE upload_jobs ADD COLUMN source_name TEXT")
            db.execSQL("ALTER TABLE upload_jobs ADD COLUMN source_relative_path TEXT")
            db.execSQL("ALTER TABLE upload_jobs ADD COLUMN source_volume TEXT")
            db.execSQL("ALTER TABLE upload_jobs ADD COLUMN source_media_store_id TEXT")
            db.execSQL("ALTER TABLE upload_jobs ADD COLUMN source_generation INTEGER NOT NULL DEFAULT 0")
            db.execSQL("ALTER TABLE upload_jobs ADD COLUMN source_media_kind TEXT")
        }
        if (oldVersion < 3) {
            // Existing rows predate account-scoped queues, so their owner is
            // unknowable. Preserve them as unassigned instead of risking that
            // the currently active account uploads another account's media.
            db.execSQL("ALTER TABLE upload_jobs RENAME TO upload_jobs_unassigned_legacy")
            dropUploadJobIndexes(db)
            createUploadJobsTable(db)
            db.execSQL(
                """
                INSERT INTO upload_jobs (
                    id, account_key, local_uri, filename, byte_size, sha256, captured_at,
                    source_id, source_name, source_relative_path, source_volume,
                    source_media_store_id, source_generation, source_media_kind,
                    upload_id, next_byte_offset, chunk_size, state, error_message, updated_at
                )
                SELECT id, NULL, local_uri, filename, byte_size, sha256, captured_at,
                    source_id, source_name, source_relative_path, source_volume,
                    source_media_store_id, source_generation, source_media_kind,
                    upload_id, next_byte_offset, chunk_size, state, error_message, updated_at
                FROM upload_jobs_unassigned_legacy
                """.trimIndent()
            )
            db.execSQL("DROP TABLE upload_jobs_unassigned_legacy")

            // A cursor is meaningful only within its account's change feed.
            // Keep the old value for recovery/debugging, but never reuse it.
            db.execSQL("ALTER TABLE sync_cursor RENAME TO sync_cursor_unassigned_legacy")
            createSyncCursorsTable(db)
        }
        if (oldVersion < 4) {
            // A local media URI can legitimately be backed up to more than one
            // account on this device. Keep the orphaned pre-account row while
            // allowing each authenticated account to create its own fresh job.
            db.execSQL("ALTER TABLE upload_jobs RENAME TO upload_jobs_v3")
            dropUploadJobIndexes(db)
            createUploadJobsTable(db)
            db.execSQL(
                """
                INSERT INTO upload_jobs (
                    id, account_key, local_uri, filename, byte_size, sha256, captured_at,
                    source_id, source_name, source_relative_path, source_volume,
                    source_media_store_id, source_generation, source_media_kind,
                    upload_id, next_byte_offset, chunk_size, state, error_message, updated_at
                )
                SELECT id, account_key, local_uri, filename, byte_size, sha256, captured_at,
                    source_id, source_name, source_relative_path, source_volume,
                    source_media_store_id, source_generation, source_media_kind,
                    upload_id, next_byte_offset, chunk_size, state, error_message, updated_at
                FROM upload_jobs_v3
                """.trimIndent()
            )
            db.execSQL("DROP TABLE upload_jobs_v3")
        }
        if (oldVersion < 5) {
            createSyncRunsTable(db)
        } else if (oldVersion < 6) {
            db.execSQL("ALTER TABLE sync_runs ADD COLUMN foreground_service INTEGER")
        }
    }

    suspend fun insertOrIgnoreJob(
        accountKey: String,
        localUri: String,
        filename: String,
        byteSize: Long,
        sha256: String,
        capturedAt: String,
        source: com.iris.app.data.model.UploadSource? = null
    ): Long = withContext(Dispatchers.IO) {
        require(accountKey.isNotBlank()) { "An account key is required for every upload job" }
        writableDatabase.let { db ->
            val values = ContentValues().apply {
                put("account_key", accountKey)
                put("local_uri", localUri)
                put("filename", filename)
                put("byte_size", byteSize)
                put("sha256", sha256)
                put("captured_at", capturedAt)
                put("source_id", source?.id)
                put("source_name", source?.name)
                put("source_relative_path", source?.relativePath)
                put("source_volume", source?.volume)
                put("source_media_store_id", source?.mediaStoreId)
                put("source_generation", source?.generation ?: 0L)
                put("source_media_kind", source?.mediaKind)
                put("state", UploadJobState.QUEUED.name)
                put("updated_at", System.currentTimeMillis())
            }
            db.beginTransaction()
            try {
                val insertedId = db.insertWithOnConflict(
                    "upload_jobs",
                    null,
                    values,
                    SQLiteDatabase.CONFLICT_IGNORE
                )
                if (insertedId > 0L) {
                    // This scan has just confirmed that the media is present
                    // and eligible for the current account. Replace only the
                    // unowned legacy row; never resume its upload ID/offset.
                    db.delete(
                        "upload_jobs",
                        "account_key IS NULL AND local_uri = ?",
                        arrayOf(localUri)
                    )
                }
                db.setTransactionSuccessful()
                insertedId
            } finally {
                db.endTransaction()
            }
        }
    }

    suspend fun isUriEnqueued(accountKey: String, localUri: String): Boolean = withContext(Dispatchers.IO) {
        require(accountKey.isNotBlank()) { "An account key is required to inspect upload jobs" }
        readableDatabase.let { db ->
            val cursor = db.rawQuery(
                "SELECT 1 FROM upload_jobs WHERE account_key = ? AND local_uri = ? LIMIT 1",
                arrayOf(accountKey, localUri)
            )
            cursor.use { it.moveToFirst() }
        }
    }

    suspend fun updateUploadStarted(accountKey: String, id: Long, uploadId: String, offset: Long, chunkSize: Int) = withContext(Dispatchers.IO) {
        writableDatabase.let { db ->
            val values = ContentValues().apply {
                put("upload_id", uploadId)
                put("next_byte_offset", offset)
                put("chunk_size", chunkSize)
                put("state", UploadJobState.UPLOADING.name)
                put("updated_at", System.currentTimeMillis())
            }
            db.update("upload_jobs", values, "id = ? AND account_key = ?", arrayOf(id.toString(), accountKey))
        }
    }

    suspend fun updateOffsetTransactionally(accountKey: String, id: Long, newOffset: Long) = withContext(Dispatchers.IO) {
        writableDatabase.let { db ->
            db.beginTransaction()
            try {
                val values = ContentValues().apply {
                    put("next_byte_offset", newOffset)
                    put("state", UploadJobState.UPLOADING.name)
                    put("updated_at", System.currentTimeMillis())
                }
                db.update("upload_jobs", values, "id = ? AND account_key = ?", arrayOf(id.toString(), accountKey))
                db.setTransactionSuccessful()
            } finally {
                db.endTransaction()
            }
        }
    }

    suspend fun updateJobState(accountKey: String, id: Long, state: UploadJobState, errorMessage: String? = null) = withContext(Dispatchers.IO) {
        writableDatabase.let { db ->
            val values = ContentValues().apply {
                put("state", state.name)
                put("error_message", errorMessage)
                put("updated_at", System.currentTimeMillis())
            }
            db.update("upload_jobs", values, "id = ? AND account_key = ?", arrayOf(id.toString(), accountKey))
        }
    }

    suspend fun resetUploadProgress(accountKey: String, id: Long) = withContext(Dispatchers.IO) {
        writableDatabase.let { db ->
            val values = ContentValues().apply {
                putNull("upload_id")
                put("next_byte_offset", 0L)
                put("state", UploadJobState.QUEUED.name)
                putNull("error_message")
                put("updated_at", System.currentTimeMillis())
            }
            db.update("upload_jobs", values, "id = ? AND account_key = ?", arrayOf(id.toString(), accountKey))
        }
    }

    suspend fun updateJobStateByUploadId(accountKey: String, uploadId: String, state: UploadJobState, errorMessage: String? = null) = withContext(Dispatchers.IO) {
        writableDatabase.let { db ->
            val values = ContentValues().apply {
                put("state", state.name)
                put("error_message", errorMessage)
                put("updated_at", System.currentTimeMillis())
            }
            db.update("upload_jobs", values, "upload_id = ? AND account_key = ?", arrayOf(uploadId, accountKey))
        }
    }

    suspend fun claimNextPendingJob(
        accountKey: String,
        excludedIds: Set<Long> = emptySet()
    ): LocalUploadJob? = withContext(Dispatchers.IO) {
        require(accountKey.isNotBlank()) { "An account key is required to claim upload jobs" }
        runInWriteTransaction { db ->
            val exclusionClause = if (excludedIds.isEmpty()) "" else {
                "AND id NOT IN (${excludedIds.joinToString(",") { "?" }})"
            }
            val cursor = db.rawQuery(
                """
                SELECT id, local_uri, filename, byte_size, sha256, captured_at, upload_id, next_byte_offset, chunk_size, state, error_message, updated_at,
                       source_id, source_name, source_relative_path, source_volume, source_media_store_id, source_generation, source_media_kind
                FROM upload_jobs
                WHERE account_key = ? AND state IN ('QUEUED', 'UPLOADING') $exclusionClause
                ORDER BY id ASC
                LIMIT 1
                """.trimIndent(),
                (listOf(accountKey) + excludedIds.map(Long::toString)).toTypedArray()
            )
            val job = cursor.use {
                if (it.moveToFirst()) {
                    cursorToJob(it)
                } else null
            }
            if (job != null && job.state == UploadJobState.QUEUED) {
                val values = ContentValues().apply {
                    put("state", UploadJobState.UPLOADING.name)
                    put("updated_at", System.currentTimeMillis())
                }
                db.update("upload_jobs", values, "id = ? AND account_key = ?", arrayOf(job.id.toString(), accountKey))
            }
            job
        }
    }

    suspend fun getNextPendingJob(accountKey: String): LocalUploadJob? = claimNextPendingJob(accountKey)

    suspend fun getAllJobs(accountKey: String): List<LocalUploadJob> = withContext(Dispatchers.IO) {
        require(accountKey.isNotBlank()) { "An account key is required to read upload jobs" }
        readableDatabase.let { db ->
            val cursor = db.rawQuery(
                "$JOB_COLUMNS WHERE account_key = ? ORDER BY id DESC",
                arrayOf(accountKey)
            )
            val list = mutableListOf<LocalUploadJob>()
            cursor.use {
                while (it.moveToNext()) {
                    list.add(cursorToJob(it))
                }
            }
            list
        }
    }

    /**
     * Counts per state, so the screen can describe the queue without loading it.
     *
     * A first full sync enqueues thousands of rows. Reading them all to display
     * a header made the sync screen deserialize the entire queue every few
     * seconds; this answers the same question with one grouped query.
     */
    suspend fun countsByState(accountKey: String): Map<UploadJobState, Int> = withContext(Dispatchers.IO) {
        require(accountKey.isNotBlank()) { "An account key is required to count upload jobs" }
        val counts = mutableMapOf<UploadJobState, Int>()
        readableDatabase.rawQuery(
            "SELECT state, COUNT(*) FROM upload_jobs WHERE account_key = ? GROUP BY state",
            arrayOf(accountKey)
        ).use { cursor ->
            while (cursor.moveToNext()) {
                val state = runCatching { UploadJobState.valueOf(cursor.getString(0)) }.getOrNull()
                if (state != null) counts[state] = cursor.getInt(1)
            }
        }
        counts
    }

    /**
     * The newest jobs, bounded. Callers show a window, never the whole queue.
     */
    suspend fun getRecentJobs(accountKey: String, limit: Int): List<LocalUploadJob> = withContext(Dispatchers.IO) {
        require(accountKey.isNotBlank()) { "An account key is required to read upload jobs" }
        val list = mutableListOf<LocalUploadJob>()
        readableDatabase.rawQuery(
            "$JOB_COLUMNS WHERE account_key = ? ORDER BY id DESC LIMIT ?",
            arrayOf(accountKey, limit.coerceAtLeast(0).toString())
        ).use { cursor ->
            while (cursor.moveToNext()) {
                list.add(cursorToJob(cursor))
            }
        }
        list
    }

    suspend fun getLastSyncCursor(accountKey: String): Long = withContext(Dispatchers.IO) {
        require(accountKey.isNotBlank()) { "An account key is required to read a sync cursor" }
        readableDatabase.let { db ->
            val cursor = db.rawQuery("SELECT last_cursor FROM sync_cursors WHERE account_key = ?", arrayOf(accountKey))
            cursor.use {
                if (it.moveToFirst()) it.getLong(0) else 0L
            }
        }
    }

    suspend fun saveSyncCursor(accountKey: String, newCursor: Long) = withContext(Dispatchers.IO) {
        require(accountKey.isNotBlank()) { "An account key is required to save a sync cursor" }
        writableDatabase.let { db ->
            val values = ContentValues().apply {
                put("account_key", accountKey)
                put("last_cursor", newCursor)
                put("updated_at", System.currentTimeMillis())
            }
            db.insertWithOnConflict("sync_cursors", null, values, SQLiteDatabase.CONFLICT_REPLACE)
        }
    }

    /** Bytes still to send for queued and in-flight jobs, for the time-remaining estimate. */
    suspend fun remainingUploadBytes(accountKey: String): Long = withContext(Dispatchers.IO) {
        require(accountKey.isNotBlank()) { "An account key is required to read upload jobs" }
        readableDatabase.rawQuery(
            "SELECT COALESCE(SUM(MAX(byte_size - next_byte_offset, 0)), 0) FROM upload_jobs " +
                "WHERE account_key = ? AND state IN ('QUEUED', 'UPLOADING')",
            arrayOf(accountKey)
        ).use { cursor -> if (cursor.moveToFirst()) cursor.getLong(0) else 0L }
    }

    suspend fun insertSyncRun(
        accountKey: String,
        startedAtMillis: Long,
        trigger: SyncRunTrigger,
        startedInForeground: Boolean,
    ): Long = withContext(Dispatchers.IO) {
        require(accountKey.isNotBlank()) { "An account key is required to record a sync run" }
        writeMutex.withLock {
            val db = writableDatabase
            db.beginTransactionNonExclusive()
            try {
                val id = db.insertOrThrow("sync_runs", null, ContentValues().apply {
                    put("account_key", accountKey)
                    put("started_at", startedAtMillis)
                    put("run_trigger", trigger.name)
                    put("started_in_foreground", if (startedInForeground) 1 else 0)
                    put("outcome", SyncRunOutcome.RUNNING.name)
                })
                // History is a window, not an archive: keep the newest runs only.
                db.execSQL(
                    "DELETE FROM sync_runs WHERE account_key = ? AND id NOT IN " +
                        "(SELECT id FROM sync_runs WHERE account_key = ? ORDER BY id DESC LIMIT ?)",
                    arrayOf<Any>(accountKey, accountKey, SYNC_RUN_HISTORY_LIMIT)
                )
                db.setTransactionSuccessful()
                id
            } finally {
                db.endTransaction()
            }
        }
    }

    /** Saves progress mid-run, so a run whose process is killed still shows what it sent. */
    suspend fun updateSyncRunProgress(
        accountKey: String,
        id: Long,
        bytes: Long,
        items: Long,
        uploadMillis: Long,
    ) = withContext(Dispatchers.IO) {
        writableDatabase.update("sync_runs", ContentValues().apply {
            put("bytes", bytes)
            put("items", items)
            put("upload_millis", uploadMillis)
        }, "id = ? AND account_key = ? AND outcome = ?", arrayOf(id.toString(), accountKey, SyncRunOutcome.RUNNING.name))
    }

    suspend fun finishSyncRun(
        accountKey: String,
        id: Long,
        endedAtMillis: Long,
        bytes: Long,
        items: Long,
        uploadMillis: Long,
        outcome: SyncRunOutcome,
        stopReason: Int?,
        detail: String?,
    ) = withContext(Dispatchers.IO) {
        writableDatabase.update("sync_runs", ContentValues().apply {
            put("ended_at", endedAtMillis)
            put("bytes", bytes)
            put("items", items)
            put("upload_millis", uploadMillis)
            put("outcome", outcome.name)
            if (stopReason != null) put("stop_reason", stopReason) else putNull("stop_reason")
            if (detail != null) put("detail", detail) else putNull("detail")
        }, "id = ? AND account_key = ?", arrayOf(id.toString(), accountKey))
    }

    suspend fun markSyncRunForeground(accountKey: String, id: Long, started: Boolean) = withContext(Dispatchers.IO) {
        writableDatabase.update("sync_runs", ContentValues().apply {
            put("foreground_service", if (started) 1 else 0)
        }, "id = ? AND account_key = ?", arrayOf(id.toString(), accountKey))
    }

    /** Drops a run that had nothing to do, so idle periodic checks do not bury real ones. */
    suspend fun deleteSyncRun(accountKey: String, id: Long) = withContext(Dispatchers.IO) {
        writableDatabase.delete("sync_runs", "id = ? AND account_key = ?", arrayOf(id.toString(), accountKey))
    }

    suspend fun deleteOtherSyncRuns(accountKey: String, outcome: SyncRunOutcome, keepId: Long) =
        withContext(Dispatchers.IO) {
            writableDatabase.delete(
                "sync_runs",
                "account_key = ? AND outcome = ? AND id != ?",
                arrayOf(accountKey, outcome.name, keepId.toString())
            )
        }

    suspend fun recentSyncRuns(accountKey: String, limit: Int): List<SyncRun> = withContext(Dispatchers.IO) {
        require(accountKey.isNotBlank()) { "An account key is required to read sync runs" }
        val runs = mutableListOf<SyncRun>()
        readableDatabase.rawQuery(
            "SELECT id, started_at, ended_at, run_trigger, started_in_foreground, bytes, items, " +
                "upload_millis, outcome, stop_reason, detail, foreground_service FROM sync_runs " +
                "WHERE account_key = ? ORDER BY id DESC LIMIT ?",
            arrayOf(accountKey, limit.coerceAtLeast(0).toString())
        ).use { cursor ->
            while (cursor.moveToNext()) {
                runs += SyncRun(
                    id = cursor.getLong(0),
                    startedAtMillis = cursor.getLong(1),
                    endedAtMillis = if (cursor.isNull(2)) null else cursor.getLong(2),
                    trigger = runCatching { SyncRunTrigger.valueOf(cursor.getString(3)) }
                        .getOrDefault(SyncRunTrigger.AUTOMATIC),
                    startedInForeground = cursor.getInt(4) != 0,
                    bytes = cursor.getLong(5),
                    items = cursor.getLong(6),
                    uploadMillis = cursor.getLong(7),
                    outcome = runCatching { SyncRunOutcome.valueOf(cursor.getString(8)) }
                        .getOrDefault(SyncRunOutcome.RUNNING),
                    stopReason = if (cursor.isNull(9)) null else cursor.getInt(9),
                    detail = if (cursor.isNull(10)) null else cursor.getString(10),
                    foregroundService = if (cursor.isNull(11)) null else cursor.getInt(11) != 0,
                )
            }
        }
        runs
    }

    suspend fun countUnassignedPendingJobs(): Int = withContext(Dispatchers.IO) {
        readableDatabase.rawQuery(
            "SELECT COUNT(*) FROM upload_jobs WHERE account_key IS NULL AND state IN ('QUEUED', 'UPLOADING')",
            null
        ).use { cursor -> if (cursor.moveToFirst()) cursor.getInt(0) else 0 }
    }

    private fun cursorToJob(cursor: android.database.Cursor): LocalUploadJob {
        return LocalUploadJob(
            id = cursor.getLong(0),
            localUri = cursor.getString(1),
            filename = cursor.getString(2),
            byteSize = cursor.getLong(3),
            sha256 = cursor.getString(4),
            capturedAt = cursor.getString(5),
            source = cursor.getString(12)?.let { sourceId ->
                com.iris.app.data.model.UploadSource(
                    id = sourceId,
                    name = cursor.getString(13).orEmpty(),
                    relativePath = cursor.getString(14).orEmpty(),
                    volume = cursor.getString(15).orEmpty(),
                    mediaStoreId = cursor.getString(16).orEmpty(),
                    generation = cursor.getLong(17),
                    mediaKind = cursor.getString(18).orEmpty()
                )
            },
            uploadId = cursor.getString(6),
            nextByteOffset = cursor.getLong(7),
            chunkSize = cursor.getInt(8),
            state = try {
                UploadJobState.valueOf(cursor.getString(9))
            } catch (e: Exception) {
                UploadJobState.QUEUED
            },
            errorMessage = cursor.getString(10),
            updatedAt = cursor.getLong(11)
        )
    }

    companion object {
        const val DATABASE_NAME = "iris_sync.db"
        const val DATABASE_VERSION = 6
        const val SYNC_RUN_HISTORY_LIMIT = 50
        private const val JOB_COLUMNS =
            "SELECT id, local_uri, filename, byte_size, sha256, captured_at, upload_id, next_byte_offset, chunk_size, state, error_message, updated_at, source_id, source_name, source_relative_path, source_volume, source_media_store_id, source_generation, source_media_kind FROM upload_jobs"

        private fun createUploadJobsTable(db: SQLiteDatabase) {
            db.execSQL(
                """
                CREATE TABLE upload_jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_key TEXT,
                    local_uri TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    byte_size INTEGER NOT NULL,
                    sha256 TEXT NOT NULL,
                    captured_at TEXT NOT NULL,
                    source_id TEXT,
                    source_name TEXT,
                    source_relative_path TEXT,
                    source_volume TEXT,
                    source_media_store_id TEXT,
                    source_generation INTEGER NOT NULL DEFAULT 0,
                    source_media_kind TEXT,
                    upload_id TEXT,
                    next_byte_offset INTEGER NOT NULL DEFAULT 0,
                    chunk_size INTEGER NOT NULL DEFAULT 33554432,
                    state TEXT NOT NULL DEFAULT 'QUEUED',
                    error_message TEXT,
                    updated_at INTEGER NOT NULL
                )
                """.trimIndent()
            )
            db.execSQL("CREATE UNIQUE INDEX idx_upload_jobs_account_uri ON upload_jobs(account_key, local_uri)")
            db.execSQL("CREATE INDEX idx_upload_jobs_account_id ON upload_jobs(account_key, id)")
            db.execSQL("CREATE INDEX idx_upload_jobs_account_state_id ON upload_jobs(account_key, state, id)")
        }

        private fun dropUploadJobIndexes(db: SQLiteDatabase) {
            // Index names remain attached to the renamed legacy table. Remove
            // them before creating the replacement table's indexes.
            db.execSQL("DROP INDEX IF EXISTS idx_upload_jobs_account_uri")
            db.execSQL("DROP INDEX IF EXISTS idx_upload_jobs_account_id")
            db.execSQL("DROP INDEX IF EXISTS idx_upload_jobs_account_state_id")
        }

        private fun createSyncRunsTable(db: SQLiteDatabase) {
            db.execSQL(
                """
                CREATE TABLE sync_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_key TEXT NOT NULL,
                    started_at INTEGER NOT NULL,
                    ended_at INTEGER,
                    run_trigger TEXT NOT NULL,
                    started_in_foreground INTEGER NOT NULL,
                    bytes INTEGER NOT NULL DEFAULT 0,
                    items INTEGER NOT NULL DEFAULT 0,
                    upload_millis INTEGER NOT NULL DEFAULT 0,
                    outcome TEXT NOT NULL,
                    stop_reason INTEGER,
                    detail TEXT,
                    foreground_service INTEGER
                )
                """.trimIndent()
            )
            db.execSQL("CREATE INDEX idx_sync_runs_account_id ON sync_runs(account_key, id)")
        }

        private fun createSyncCursorsTable(db: SQLiteDatabase) {
            db.execSQL(
                """
                CREATE TABLE sync_cursors (
                    account_key TEXT PRIMARY KEY NOT NULL,
                    last_cursor INTEGER NOT NULL DEFAULT 0,
                    updated_at INTEGER NOT NULL
                )
                """.trimIndent()
            )
        }
    }
}
