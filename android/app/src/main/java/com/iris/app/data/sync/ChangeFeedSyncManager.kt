package com.iris.app.data.sync

import com.iris.app.data.local.UploadDatabaseHelper
import com.iris.app.data.remote.IrisApiService
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonPrimitive

import android.content.ContentValues
import com.iris.app.data.model.UploadJobState
import kotlinx.coroutines.sync.Mutex

class ChangeFeedSyncManager(
    private val dbHelper: UploadDatabaseHelper,
    private val apiServiceProvider: () -> IrisApiService
) {

    private val syncMutex = Mutex()

    suspend fun syncChanges(): Int = withContext(Dispatchers.IO) {
        if (!syncMutex.tryLock()) {
            return@withContext 0
        }
        try {
            val apiService = apiServiceProvider()
            var cursor = dbHelper.getLastSyncCursor()
            var totalChanges = 0

            while (true) {
                val response = try {
                    apiService.getChanges(cursor = cursor, limit = 200)
                } catch (e: Exception) {
                    break
                }

                if (response.changes.isEmpty()) {
                    break
                }

                // Prevent infinite loop if server next_cursor stagnates with hasMore
                if (response.nextCursor <= cursor && response.hasMore) {
                    break
                }

                // Single atomic transaction for entire batch + cursor update
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
                                db.update("upload_jobs", values, "upload_id = ?", arrayOf(uploadId))
                            }
                        } catch (_: Exception) {
                            // Poison pill isolation: a single corrupt event must not block cursor progression
                        }
                    }

                    // Save next cursor within the same atomic transaction
                    val cursorValues = ContentValues().apply {
                        put("last_cursor", response.nextCursor)
                        put("updated_at", System.currentTimeMillis())
                    }
                    db.update("sync_cursor", cursorValues, "id = 1", null)
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
}
