package com.iris.app.data.catalog

import android.content.ContentValues
import android.content.Context
import android.database.sqlite.SQLiteDatabase
import android.database.sqlite.SQLiteOpenHelper
import com.iris.app.data.model.MediaRecord
import com.iris.app.data.remote.IrisApiClient
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext

/**
 * The catalog mirror on device.
 *
 * Only the columns the gallery orders and filters by are modelled; the record
 * itself is stored verbatim as the JSON the server sent. That keeps the mirror
 * from needing a migration every time the server grows a field — `thumb_hash`
 * was added mid-flight and needed no schema change here — and guarantees what
 * the UI reads back is exactly what the server said, not a lossy re-encoding.
 */
class SqliteCatalogStore(
    context: Context,
    private val json: kotlinx.serialization.json.Json = IrisApiClient.RESPONSE_JSON,
) : SQLiteOpenHelper(context, DATABASE_NAME, null, DATABASE_VERSION), CatalogStore {

    private val writeMutex = Mutex()

    override fun onConfigure(db: SQLiteDatabase) {
        super.onConfigure(db)
        db.enableWriteAheadLogging()
        try {
            db.execSQL("PRAGMA busy_timeout = 5000")
        } catch (_: Exception) {
        }
    }

    override fun onCreate(db: SQLiteDatabase) {
        db.execSQL(
            """
            CREATE TABLE catalog_media (
                db_id INTEGER PRIMARY KEY,
                idx INTEGER NOT NULL,
                media_type TEXT NOT NULL DEFAULT 'image',
                file_mtime REAL NOT NULL DEFAULT 0,
                payload TEXT NOT NULL
            )
            """.trimIndent()
        )
        // The gallery's only ordering: newest capture first, like Google Photos.
        db.execSQL("CREATE INDEX idx_catalog_recent ON catalog_media(file_mtime DESC)")
        db.execSQL("CREATE INDEX idx_catalog_type ON catalog_media(media_type, file_mtime DESC)")
    }

    override fun onUpgrade(db: SQLiteDatabase, oldVersion: Int, newVersion: Int) {
        // The mirror is a cache: it is always rebuildable from the server, so a
        // schema change drops it rather than carrying migration logic forever.
        db.execSQL("DROP TABLE IF EXISTS catalog_media")
        onCreate(db)
    }

    override suspend fun upsertBatch(records: List<MediaRecord>) {
        if (records.isEmpty()) return
        withContext(Dispatchers.IO) {
            writeMutex.withLock {
                val db = writableDatabase
                db.beginTransactionNonExclusive()
                try {
                    for (record in records) {
                        val dbId = record.dbId ?: continue
                        val values = ContentValues().apply {
                            put("db_id", dbId)
                            put("idx", record.index)
                            put("media_type", record.mediaType)
                            put("file_mtime", record.fileMtime ?: 0.0)
                            put("payload", json.encodeToString(MediaRecord.serializer(), record))
                        }
                        db.insertWithOnConflict(
                            "catalog_media", null, values, SQLiteDatabase.CONFLICT_REPLACE
                        )
                    }
                    db.setTransactionSuccessful()
                } finally {
                    db.endTransaction()
                }
            }
        }
    }

    override suspend fun page(offset: Int, limit: Int, mediaType: String): List<MediaRecord> =
        withContext(Dispatchers.IO) {
            val filtered = mediaType != "all"
            val sql = buildString {
                append("SELECT payload FROM catalog_media ")
                if (filtered) append("WHERE media_type = ? ")
                append("ORDER BY file_mtime DESC, db_id DESC LIMIT ? OFFSET ?")
            }
            val args = if (filtered) {
                arrayOf(mediaType, limit.toString(), offset.toString())
            } else {
                arrayOf(limit.toString(), offset.toString())
            }
            readableDatabase.rawQuery(sql, args).use { cursor ->
                buildList {
                    while (cursor.moveToNext()) {
                        runCatching {
                            json.decodeFromString(MediaRecord.serializer(), cursor.getString(0))
                        }.getOrNull()?.let(::add)
                    }
                }
            }
        }

    override suspend fun count(mediaType: String): Int = withContext(Dispatchers.IO) {
        val filtered = mediaType != "all"
        val sql = "SELECT COUNT(*) FROM catalog_media" + if (filtered) " WHERE media_type = ?" else ""
        val args = if (filtered) arrayOf(mediaType) else emptyArray()
        readableDatabase.rawQuery(sql, args).use { cursor ->
            if (cursor.moveToFirst()) cursor.getInt(0) else 0
        }
    }

    override suspend fun clear() = withContext(Dispatchers.IO) {
        writeMutex.withLock {
            writableDatabase.execSQL("DELETE FROM catalog_media")
        }
    }

    companion object {
        const val DATABASE_NAME = "iris_catalog.db"
        const val DATABASE_VERSION = 1
    }
}
