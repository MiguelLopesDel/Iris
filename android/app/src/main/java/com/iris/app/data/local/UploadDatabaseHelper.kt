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
        createScanStateTable(db)
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
        if (oldVersion < 7) {
            // Steps 3 and 4 rebuild upload_jobs with the current columns, so an
            // upgrade from before them already has these.
            addColumnIfMissing(db, "upload_jobs", "source_date_modified", "INTEGER")
            addColumnIfMissing(db, "upload_jobs", "previous_sha256", "TEXT")
            addColumnIfMissing(db, "upload_jobs", "verified_at", "INTEGER")
            createScanStateTable(db)
        }
        if (oldVersion < 8) {
            addColumnIfMissing(db, "upload_jobs", "source_size", "INTEGER")
            // Uploads the server refused because the bytes did not match the
            // declared hash were failed for good. They were sent at MediaStore's
            // SIZE, which can trail a file rewritten without MediaStore noticing;
            // uploads now measure the real file, so give them another chance.
            db.execSQL(
                "UPDATE upload_jobs SET state = 'QUEUED', upload_id = NULL, next_byte_offset = 0, " +
                    "error_message = NULL WHERE state = 'FAILED' AND error_message LIKE '%Hash do arquivo%'"
            )
        }
        if (oldVersion < 9) {
            addColumnIfMissing(db, "scan_state", "server_instance_id", "TEXT")
        }
    }

    /** What the queue knows about [localUri] for this account, or null when it is not queued. */
    suspend fun knownMedia(accountKey: String, localUri: String): com.iris.app.data.sync.KnownMedia? =
        withContext(Dispatchers.IO) {
            require(accountKey.isNotBlank()) { "An account key is required to inspect upload jobs" }
            readableDatabase.rawQuery(
                "SELECT id, sha256, COALESCE(source_size, byte_size), source_date_modified, source_generation, verified_at " +
                    "FROM upload_jobs WHERE account_key = ? AND local_uri = ? LIMIT 1",
                arrayOf(accountKey, localUri)
            ).use { cursor ->
                if (!cursor.moveToFirst()) return@use null
                com.iris.app.data.sync.KnownMedia(
                    jobId = cursor.getLong(0),
                    sha256 = cursor.getString(1),
                    fingerprint = com.iris.app.data.sync.MediaFingerprint(
                        size = cursor.getLong(2),
                        dateModifiedSeconds = if (cursor.isNull(3)) null else cursor.getLong(3),
                        generation = cursor.getLong(4),
                    ),
                    verifiedAt = if (cursor.isNull(5)) null else cursor.getLong(5),
                )
            }
        }

    /**
     * Records that the file behind job [id] still has the hash it was queued
     * with: renamed, moved or touched, but the same bytes. Its upload state is
     * untouched.
     */
    suspend fun recordVerifiedFingerprint(
        accountKey: String,
        id: Long,
        fingerprint: com.iris.app.data.sync.MediaFingerprint,
        filename: String,
        source: com.iris.app.data.model.UploadSource?,
        verifiedAt: Long?,
    ) = withContext(Dispatchers.IO) {
        writableDatabase.update(
            "upload_jobs",
            ContentValues().apply {
                put("source_size", fingerprint.size)
                put("source_date_modified", fingerprint.dateModifiedSeconds)
                put("source_generation", fingerprint.generation)
                put("filename", filename)
                putSource(source)
                if (verifiedAt != null) put("verified_at", verifiedAt)
            },
            "id = ? AND account_key = ?",
            arrayOf(id.toString(), accountKey)
        )
    }

    /**
     * The file behind job [id] now holds other bytes (edited in place, or the
     * id was reused after MediaStore was rebuilt): queue the new content,
     * keeping the old hash in previous_sha256, under a new id (see
     * [moveToNewestId]). A job being sent right
     * now is left alone; the next scan finds the change again. Returns whether
     * the row was replaced.
     */
    suspend fun replaceWithNewVersion(
        accountKey: String,
        id: Long,
        sha256: String,
        byteSize: Long,
        fingerprint: com.iris.app.data.sync.MediaFingerprint,
        filename: String,
        capturedAt: String,
        source: com.iris.app.data.model.UploadSource?,
        verifiedAt: Long,
    ): Boolean = runInWriteTransaction { db ->
        db.execSQL(
            "UPDATE upload_jobs SET previous_sha256 = sha256 " +
                "WHERE id = ? AND account_key = ? AND state != 'UPLOADING' AND sha256 != ?",
            arrayOf<Any>(id, accountKey, sha256)
        )
        db.update(
            "upload_jobs",
            ContentValues().apply {
                put("sha256", sha256)
                put("byte_size", byteSize)
                put("source_size", fingerprint.size)
                put("source_date_modified", fingerprint.dateModifiedSeconds)
                put("source_generation", fingerprint.generation)
                put("filename", filename)
                put("captured_at", capturedAt)
                putSource(source)
                putNull("upload_id")
                put("next_byte_offset", 0L)
                put("state", UploadJobState.QUEUED.name)
                putNull("error_message")
                put("verified_at", verifiedAt)
                put("updated_at", System.currentTimeMillis())
            },
            "id = ? AND account_key = ? AND state != 'UPLOADING'",
            arrayOf(id.toString(), accountKey)
        ).let { replaced ->
            if (replaced > 0) moveToNewestId(db, id)
            replaced > 0
        }
    }

    /**
     * Gives the row a new id above every existing one. Upload passes claim
     * in id order above the last id they took, so a version requeued behind
     * that cursor would wait for the next pass, which may never come: the
     * pass itself reports success. A new id puts it ahead of any running
     * pass, without letting a pass revisit what it already tried.
     */
    private fun moveToNewestId(db: SQLiteDatabase, id: Long) {
        val sequence = db.rawQuery("SELECT seq FROM sqlite_sequence WHERE name = 'upload_jobs'", null)
            .use { if (it.moveToFirst()) it.getLong(0) else 0L }
        val highest = db.rawQuery("SELECT COALESCE(MAX(id), 0) FROM upload_jobs", null)
            .use { if (it.moveToFirst()) it.getLong(0) else 0L }
        val newId = maxOf(sequence, highest) + 1
        db.execSQL("UPDATE upload_jobs SET id = ? WHERE id = ?", arrayOf<Any>(newId, id))
        // Keep AUTOINCREMENT from ever handing this id out again.
        db.execSQL("UPDATE sqlite_sequence SET seq = ? WHERE name = 'upload_jobs'", arrayOf<Any>(newId))
    }

    /**
     * The bytes behind job [id] are not the ones it declared (its file was
     * rewritten since it was hashed): record what they are now and start its
     * upload over. Not a new version of the media, so previous_sha256 stays.
     */
    suspend fun setContent(accountKey: String, id: Long, sha256: String, byteSize: Long) = withContext(Dispatchers.IO) {
        writableDatabase.update(
            "upload_jobs",
            ContentValues().apply {
                put("sha256", sha256)
                put("byte_size", byteSize)
                putNull("upload_id")
                put("next_byte_offset", 0L)
                put("verified_at", System.currentTimeMillis())
                put("updated_at", System.currentTimeMillis())
            },
            "id = ? AND account_key = ?",
            arrayOf(id.toString(), accountKey)
        )
    }

    /** This account's scan bookkeeping: MediaStore versions and full verification times. */
    suspend fun scanState(accountKey: String): ScanState = withContext(Dispatchers.IO) {
        readableDatabase.rawQuery(
            "SELECT media_store_versions, full_verification_started_at, last_full_verification_at, server_instance_id " +
                "FROM scan_state WHERE account_key = ?",
            arrayOf(accountKey)
        ).use { cursor ->
            if (!cursor.moveToFirst()) return@use ScanState()
            ScanState(
                mediaStoreVersions = decodeVersions(cursor.getString(0).orEmpty()),
                fullVerificationStartedAt = if (cursor.isNull(1)) null else cursor.getLong(1),
                lastFullVerificationAt = if (cursor.isNull(2)) null else cursor.getLong(2),
                serverInstanceId = cursor.getString(3),
            )
        }
    }

    suspend fun saveScanState(accountKey: String, state: ScanState) = withContext(Dispatchers.IO) {
        writableDatabase.insertWithOnConflict(
            "scan_state",
            null,
            ContentValues().apply {
                put("account_key", accountKey)
                put("media_store_versions", encodeVersions(state.mediaStoreVersions))
                put("full_verification_started_at", state.fullVerificationStartedAt)
                put("last_full_verification_at", state.lastFullVerificationAt)
                put("server_instance_id", state.serverInstanceId)
            },
            SQLiteDatabase.CONFLICT_REPLACE
        )
    }

    data class ScanState(
        val mediaStoreVersions: Map<String, String> = emptyMap(),
        val fullVerificationStartedAt: Long? = null,
        val lastFullVerificationAt: Long? = null,
        /** The server installation this account's queue states describe. */
        val serverInstanceId: String? = null,
    )

    /**
     * The account's queue described another server installation (reinstalled,
     * or a fresh one at the same address with the same user id): what it marked
     * uploaded or already present says nothing about this server. Every item
     * goes back to the queue with its hash kept, so nothing is read again; the
     * server answers "duplicate" for what it already has. The change-feed
     * cursor belonged to the old installation too.
     */
    suspend fun requeueForNewServer(accountKey: String): Int = runInWriteTransaction { db ->
        val requeued = db.update(
            "upload_jobs",
            ContentValues().apply {
                put("state", UploadJobState.QUEUED.name)
                putNull("upload_id")
                put("next_byte_offset", 0L)
                putNull("error_message")
                put("updated_at", System.currentTimeMillis())
            },
            "account_key = ? AND NOT (state = 'QUEUED' AND upload_id IS NULL)",
            arrayOf(accountKey)
        )
        db.delete("sync_cursors", "account_key = ?", arrayOf(accountKey))
        requeued
    }

    suspend fun insertOrIgnoreJob(
        accountKey: String,
        localUri: String,
        filename: String,
        byteSize: Long,
        sha256: String,
        capturedAt: String,
        source: com.iris.app.data.model.UploadSource? = null,
        dateModifiedSeconds: Long? = null,
        verifiedAt: Long? = null,
        sourceSize: Long? = null,
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
                put("source_date_modified", dateModifiedSeconds)
                put("source_size", sourceSize)
                put("verified_at", verifiedAt)
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

    /**
     * Claims the oldest pending job with an id above [afterId].
     *
     * A pass walks the queue in id order and passes the last id it claimed, so
     * jobs it is already sending or set aside are behind it. This keeps the
     * query at two parameters: excluding them by id needed one parameter each,
     * and a long pass with many isolated failures could exceed SQLite's limit
     * on bound variables (999 on older versions).
     */
    suspend fun claimNextPendingJob(
        accountKey: String,
        afterId: Long = 0L
    ): LocalUploadJob? = withContext(Dispatchers.IO) {
        require(accountKey.isNotBlank()) { "An account key is required to claim upload jobs" }
        runInWriteTransaction { db ->
            val cursor = db.rawQuery(
                """
                SELECT id, local_uri, filename, byte_size, sha256, captured_at, upload_id, next_byte_offset, chunk_size, state, error_message, updated_at,
                       source_id, source_name, source_relative_path, source_volume, source_media_store_id, source_generation, source_media_kind
                FROM upload_jobs
                WHERE account_key = ? AND state IN ('QUEUED', 'UPLOADING') AND id > ?
                ORDER BY id ASC
                LIMIT 1
                """.trimIndent(),
                arrayOf(accountKey, afterId.toString())
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
            updatedAt = cursor.getLong(11),
            // Present only in queries built from JOB_COLUMNS.
            sourceDateModified = if (cursor.columnCount > 19 && !cursor.isNull(19)) cursor.getLong(19) else null,
            previousSha256 = if (cursor.columnCount > 20) cursor.getString(20) else null,
            verifiedAt = if (cursor.columnCount > 21 && !cursor.isNull(21)) cursor.getLong(21) else null,
            sourceSize = if (cursor.columnCount > 22 && !cursor.isNull(22)) cursor.getLong(22) else null,
        )
    }

    private fun ContentValues.putSource(source: com.iris.app.data.model.UploadSource?) {
        if (source == null) return
        put("source_id", source.id)
        put("source_name", source.name)
        put("source_relative_path", source.relativePath)
        put("source_volume", source.volume)
        put("source_media_store_id", source.mediaStoreId)
        put("source_media_kind", source.mediaKind)
    }

    companion object {
        const val DATABASE_NAME = "iris_sync.db"
        const val DATABASE_VERSION = 9
        const val SYNC_RUN_HISTORY_LIMIT = 50
        private const val JOB_COLUMNS =
            "SELECT id, local_uri, filename, byte_size, sha256, captured_at, upload_id, next_byte_offset, chunk_size, state, error_message, updated_at, source_id, source_name, source_relative_path, source_volume, source_media_store_id, source_generation, source_media_kind, source_date_modified, previous_sha256, verified_at, source_size FROM upload_jobs"

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
                    source_date_modified INTEGER,
                    previous_sha256 TEXT,
                    verified_at INTEGER,
                    source_size INTEGER,
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

        private fun createScanStateTable(db: SQLiteDatabase) {
            db.execSQL(
                """
                CREATE TABLE IF NOT EXISTS scan_state (
                    account_key TEXT PRIMARY KEY NOT NULL,
                    media_store_versions TEXT NOT NULL DEFAULT '',
                    full_verification_started_at INTEGER,
                    last_full_verification_at INTEGER,
                    server_instance_id TEXT
                )
                """.trimIndent()
            )
        }

        private fun addColumnIfMissing(db: SQLiteDatabase, table: String, column: String, type: String) {
            val exists = db.rawQuery("PRAGMA table_info($table)", null).use { cursor ->
                val nameIndex = cursor.getColumnIndexOrThrow("name")
                generateSequence { if (cursor.moveToNext()) cursor.getString(nameIndex) else null }.any { it == column }
            }
            if (!exists) db.execSQL("ALTER TABLE $table ADD COLUMN $column $type")
        }

        // Volume names and MediaStore versions contain neither '=' nor '\n'.
        internal fun encodeVersions(versions: Map<String, String>): String =
            versions.entries.sortedBy { it.key }.joinToString("\n") { "${it.key}=${it.value}" }

        internal fun decodeVersions(text: String): Map<String, String> =
            text.lineSequence().filter { '=' in it }.associate { it.substringBefore('=') to it.substringAfter('=') }

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
