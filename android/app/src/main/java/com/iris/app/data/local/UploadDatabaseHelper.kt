package com.iris.app.data.local

import android.content.ContentValues
import android.content.Context
import android.database.sqlite.SQLiteDatabase
import android.database.sqlite.SQLiteOpenHelper
import com.iris.app.data.model.LocalUploadJob
import com.iris.app.data.model.UploadJobState
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

class UploadDatabaseHelper(context: Context) : SQLiteOpenHelper(
    context,
    DATABASE_NAME,
    null,
    DATABASE_VERSION
) {

    override fun onCreate(db: SQLiteDatabase) {
        db.execSQL(
            """
            CREATE TABLE upload_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                local_uri TEXT NOT NULL UNIQUE,
                filename TEXT NOT NULL,
                byte_size INTEGER NOT NULL,
                sha256 TEXT NOT NULL,
                captured_at TEXT NOT NULL,
                upload_id TEXT,
                next_byte_offset INTEGER NOT NULL DEFAULT 0,
                chunk_size INTEGER NOT NULL DEFAULT 33554432,
                state TEXT NOT NULL DEFAULT 'QUEUED',
                error_message TEXT,
                updated_at INTEGER NOT NULL
            )
            """.trimIndent()
        )

        db.execSQL(
            """
            CREATE TABLE sync_cursor (
                id INTEGER PRIMARY KEY,
                last_cursor INTEGER NOT NULL DEFAULT 0,
                updated_at INTEGER NOT NULL
            )
            """.trimIndent()
        )

        db.execSQL("INSERT OR IGNORE INTO sync_cursor (id, last_cursor, updated_at) VALUES (1, 0, ${System.currentTimeMillis()})")
    }

    override fun onUpgrade(db: SQLiteDatabase, oldVersion: Int, newVersion: Int) {
        db.execSQL("DROP TABLE IF EXISTS upload_jobs")
        db.execSQL("DROP TABLE IF EXISTS sync_cursor")
        onCreate(db)
    }

    suspend fun insertOrIgnoreJob(
        localUri: String,
        filename: String,
        byteSize: Long,
        sha256: String,
        capturedAt: String
    ): Long = withContext(Dispatchers.IO) {
        writableDatabase.let { db ->
            val values = ContentValues().apply {
                put("local_uri", localUri)
                put("filename", filename)
                put("byte_size", byteSize)
                put("sha256", sha256)
                put("captured_at", capturedAt)
                put("state", UploadJobState.QUEUED.name)
                put("updated_at", System.currentTimeMillis())
            }
            db.insertWithOnConflict("upload_jobs", null, values, SQLiteDatabase.CONFLICT_IGNORE)
        }
    }

    suspend fun isUriEnqueued(localUri: String): Boolean = withContext(Dispatchers.IO) {
        readableDatabase.let { db ->
            val cursor = db.rawQuery(
                "SELECT 1 FROM upload_jobs WHERE local_uri = ? LIMIT 1",
                arrayOf(localUri)
            )
            cursor.use { it.moveToFirst() }
        }
    }

    suspend fun updateUploadStarted(id: Long, uploadId: String, offset: Long, chunkSize: Int) = withContext(Dispatchers.IO) {
        writableDatabase.let { db ->
            val values = ContentValues().apply {
                put("upload_id", uploadId)
                put("next_byte_offset", offset)
                put("chunk_size", chunkSize)
                put("state", UploadJobState.UPLOADING.name)
                put("updated_at", System.currentTimeMillis())
            }
            db.update("upload_jobs", values, "id = ?", arrayOf(id.toString()))
        }
    }

    suspend fun updateOffsetTransactionally(id: Long, newOffset: Long) = withContext(Dispatchers.IO) {
        writableDatabase.let { db ->
            db.beginTransaction()
            try {
                val values = ContentValues().apply {
                    put("next_byte_offset", newOffset)
                    put("state", UploadJobState.UPLOADING.name)
                    put("updated_at", System.currentTimeMillis())
                }
                db.update("upload_jobs", values, "id = ?", arrayOf(id.toString()))
                db.setTransactionSuccessful()
            } finally {
                db.endTransaction()
            }
        }
    }

    suspend fun updateJobState(id: Long, state: UploadJobState, errorMessage: String? = null) = withContext(Dispatchers.IO) {
        writableDatabase.let { db ->
            val values = ContentValues().apply {
                put("state", state.name)
                put("error_message", errorMessage)
                put("updated_at", System.currentTimeMillis())
            }
            db.update("upload_jobs", values, "id = ?", arrayOf(id.toString()))
        }
    }

    suspend fun updateJobStateByUploadId(uploadId: String, state: UploadJobState, errorMessage: String? = null) = withContext(Dispatchers.IO) {
        writableDatabase.let { db ->
            val values = ContentValues().apply {
                put("state", state.name)
                put("error_message", errorMessage)
                put("updated_at", System.currentTimeMillis())
            }
            db.update("upload_jobs", values, "upload_id = ?", arrayOf(uploadId))
        }
    }

    suspend fun getNextPendingJob(): LocalUploadJob? = withContext(Dispatchers.IO) {
        readableDatabase.let { db ->
            val cursor = db.rawQuery(
                """
                SELECT id, local_uri, filename, byte_size, sha256, captured_at, upload_id, next_byte_offset, chunk_size, state, error_message, updated_at
                FROM upload_jobs
                WHERE state IN ('QUEUED', 'UPLOADING')
                ORDER BY id ASC
                LIMIT 1
                """.trimIndent(),
                null
            )
            cursor.use {
                if (it.moveToFirst()) {
                    cursorToJob(it)
                } else null
            }
        }
    }

    suspend fun getAllJobs(): List<LocalUploadJob> = withContext(Dispatchers.IO) {
        readableDatabase.let { db ->
            val cursor = db.rawQuery(
                "SELECT id, local_uri, filename, byte_size, sha256, captured_at, upload_id, next_byte_offset, chunk_size, state, error_message, updated_at FROM upload_jobs ORDER BY id DESC",
                null
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

    suspend fun getLastSyncCursor(): Long = withContext(Dispatchers.IO) {
        readableDatabase.let { db ->
            val cursor = db.rawQuery("SELECT last_cursor FROM sync_cursor WHERE id = 1", null)
            cursor.use {
                if (it.moveToFirst()) it.getLong(0) else 0L
            }
        }
    }

    suspend fun saveSyncCursor(newCursor: Long) = withContext(Dispatchers.IO) {
        writableDatabase.let { db ->
            val values = ContentValues().apply {
                put("last_cursor", newCursor)
                put("updated_at", System.currentTimeMillis())
            }
            db.update("sync_cursor", values, "id = 1", null)
        }
    }

    private fun cursorToJob(cursor: android.database.Cursor): LocalUploadJob {
        return LocalUploadJob(
            id = cursor.getLong(0),
            localUri = cursor.getString(1),
            filename = cursor.getString(2),
            byteSize = cursor.getLong(3),
            sha256 = cursor.getString(4),
            capturedAt = cursor.getString(5),
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
        const val DATABASE_VERSION = 1
    }
}
