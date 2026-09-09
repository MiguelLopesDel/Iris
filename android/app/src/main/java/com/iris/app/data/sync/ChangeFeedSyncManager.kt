package com.iris.app.data.sync

import com.iris.app.data.local.UploadDatabaseHelper
import com.iris.app.data.remote.IrisApiService
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

class ChangeFeedSyncManager(
    private val dbHelper: UploadDatabaseHelper,
    private val apiServiceProvider: () -> IrisApiService
) {

    suspend fun syncChanges(): Int = withContext(Dispatchers.IO) {
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

            // Apply changes locally
            for (change in response.changes) {
                totalChanges++
                val uploadId = change.entityId
                val payloadState = change.payload?.get("state")?.toString()?.replace("\"", "")
                val errorSummary = change.payload?.get("error")?.toString()?.replace("\"", "")
                when (payloadState) {
                    "processing" -> dbHelper.updateJobStateByUploadId(uploadId, com.iris.app.data.model.UploadJobState.PROCESSING)
                    "ready" -> dbHelper.updateJobStateByUploadId(uploadId, com.iris.app.data.model.UploadJobState.READY)
                    "failed_processing" -> dbHelper.updateJobStateByUploadId(uploadId, com.iris.app.data.model.UploadJobState.FAILED_PROCESSING, errorSummary)
                }
            }

            // Persist next_cursor ONLY after applying all returned changes locally
            dbHelper.saveSyncCursor(response.nextCursor)
            cursor = response.nextCursor

            if (!response.hasMore) {
                break
            }
        }
        totalChanges
    }
}
