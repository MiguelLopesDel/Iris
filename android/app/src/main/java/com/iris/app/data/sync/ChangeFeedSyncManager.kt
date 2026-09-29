package com.iris.app.data.sync

import com.iris.app.data.local.UploadDatabaseHelper
import com.iris.app.data.remote.IrisApiService
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.withContext

import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonPrimitive

import android.content.ContentValues
import com.iris.app.data.model.UploadJobState
import kotlinx.coroutines.sync.Mutex

class ChangeFeedSyncManager(
    private val dbHelper: UploadDatabaseHelper,
    private val apiServiceProvider: (String) -> IrisApiService
) {

    private val syncMutex = Mutex()

    suspend fun syncChanges(
        accountKey: String,
        sessionIdentity: String,
        isSessionCurrent: () -> Boolean = { true }
    ): Int = withContext(Dispatchers.IO) {
        require(accountKey.isNotBlank()) { "An account key is required to sync changes" }
        require(sessionIdentity.isNotBlank()) { "A session identity is required to sync changes" }
        if (!syncMutex.tryLock()) {
            return@withContext 0
        }
        try {
            ensureSession(isSessionCurrent)
            val apiService = apiServiceProvider(sessionIdentity)
            var cursor = dbHelper.getLastSyncCursor(accountKey)
            var totalChanges = 0

            while (true) {
                ensureSession(isSessionCurrent)
                val response = try {
                    apiService.getChanges(cursor = cursor, limit = 200)
                } catch (cancelled: CancellationException) {
                    throw cancelled
                } catch (error: Exception) {
                    // The caller must distinguish "nothing changed" from a
                    // failed poll. MediaSyncWorker retries the latter while
                    // keeping the last committed cursor intact.
                    throw error
                }
                ensureSession(isSessionCurrent)

                if (response.changes.isEmpty()) {
                    break
                }

                // Prevent infinite loop if server next_cursor stagnates with hasMore
                if (response.nextCursor <= cursor && response.hasMore) {
                    break
                }

                // Single atomic transaction for entire batch + cursor update
                ensureSession(isSessionCurrent)
                dbHelper.runInWriteTransaction { db ->
                    for (change in response.changes) {
                        totalChanges++
                        try {
                            if (change.entityType.isNotEmpty() && change.entityType != "media") {
                                continue
                            }
                            val uploadId = change.entityId
                            val payloadState = change.payload?.get("state")?.jsonPrimitive?.contentOrNull
                            val errorSummary = change.payload?.get("error")?.jsonPrimitive?.contentOrNull

                            val newState = when (payloadState) {
                                "processing" -> UploadJobState.PROCESSING
                                "ready" -> UploadJobState.READY
                                "failed_processing" -> UploadJobState.FAILED_PROCESSING
                                else -> null
                            }
                            if (newState != null) {
                                val values = ContentValues().apply {
                                    put("state", newState.name)
                                    put("error_message", errorSummary)
                                    put("updated_at", System.currentTimeMillis())
                                }
                                db.update(
                                    "upload_jobs",
                                    values,
                                    "upload_id = ? AND account_key = ?",
                                    arrayOf(uploadId, accountKey)
                                )
                            }
                        } catch (_: Exception) {
                            // Poison pill isolation: a single corrupt event must not block cursor progression
                        }
                    }

                    // Save next cursor within the same atomic transaction
                    val cursorValues = ContentValues().apply {
                        put("account_key", accountKey)
                        put("last_cursor", response.nextCursor)
                        put("updated_at", System.currentTimeMillis())
                    }
                    db.insertWithOnConflict("sync_cursors", null, cursorValues, android.database.sqlite.SQLiteDatabase.CONFLICT_REPLACE)
                }

                cursor = response.nextCursor

                if (!response.hasMore) {
                    break
                }
            }
            totalChanges
        } finally {
            syncMutex.unlock()
        }
    }

    private suspend fun ensureSession(isSessionCurrent: () -> Boolean) {
        currentCoroutineContext().ensureActive()
        if (!isSessionCurrent()) {
            throw CancellationException("Device session changed during change-feed sync")
        }
    }
}
