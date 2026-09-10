package com.iris.app.data.repository

import com.iris.app.data.local.DeviceCredentialsStore
import com.iris.app.data.local.UploadDatabaseHelper
import com.iris.app.data.model.DeviceLoginResponse
import com.iris.app.data.model.IrisCollection
import com.iris.app.data.model.IrisConcept
import com.iris.app.data.model.LocalUploadJob
import com.iris.app.data.model.MediaRecord
import com.iris.app.data.model.Person
import com.iris.app.data.model.PersonMediaResponse
import com.iris.app.data.model.RecordMetadataResponse
import com.iris.app.data.model.RecordsResponse
import com.iris.app.data.model.SearchResponse
import com.iris.app.data.model.ServerInfo
import com.iris.app.data.remote.IrisApiClient
import com.iris.app.data.sync.ChangeFeedSyncManager
import com.iris.app.data.sync.MediaStoreScanner
import com.iris.app.data.sync.SyncUploadManager

import kotlin.coroutines.cancellation.CancellationException

inline fun <T, R> T.runCatchingCancellable(block: T.() -> R): Result<R> {
    return try {
        Result.success(block())
    } catch (e: CancellationException) {
        throw e
    } catch (e: Throwable) {
        Result.failure(e)
    }
}

class IrisRepository(
    val apiClient: IrisApiClient,
    val credentialsStore: DeviceCredentialsStore,
    val dbHelper: UploadDatabaseHelper,
    val uploadManager: SyncUploadManager,
    val mediaScanner: MediaStoreScanner,
    val changeFeedSync: ChangeFeedSyncManager
) {
    suspend fun deviceLogin(
        username: String,
        password: String,
        deviceName: String
    ): Result<DeviceLoginResponse> = runCatchingCancellable {
        val response = apiClient.apiService.deviceLogin(
            username = username,
            password = password,
            deviceName = deviceName,
            platform = "android"
        )
        credentialsStore.saveSession(
            deviceId = response.deviceId,
            accessToken = response.accessToken,
            refreshToken = response.refreshToken,
            expiresInSeconds = response.expiresIn,
            username = username,
            serverOrigin = IrisApiClient.getOrigin(apiClient.baseUrl)
        )
        response
    }

    fun logoutDevice() {
        credentialsStore.clearCredentials()
    }

    suspend fun getUploadQueue(): List<LocalUploadJob> {
        return dbHelper.getAllJobs()
    }

    suspend fun triggerSync(): Result<Int> = runCatchingCancellable {
        val discovered = mediaScanner.scanAndEnqueueNewMedia()
        uploadManager.processQueue()
        changeFeedSync.syncChanges()
        discovered
    }

    suspend fun checkServerHealth(): Result<com.iris.app.data.model.HealthResponse> = runCatchingCancellable {
        apiClient.apiService.getHealth()
    }

    suspend fun getServerInfo(): Result<ServerInfo> = runCatchingCancellable {
        apiClient.apiService.getInfo()
    }

    suspend fun getRecords(
        page: Int = 1,
        perPage: Int = 24,
        sortBy: String = "importacao",
        sortAsc: Int = 0,
        mediaType: String = "all",
        collectionIds: String = "",
        conceptIds: String = ""
    ): Result<RecordsResponse> = runCatchingCancellable {
        val safePage = maxOf(1, page)
        val safePerPage = perPage.coerceIn(12, 500)
        apiClient.apiService.getRecords(
            page = safePage,
            perPage = safePerPage,
            sortBy = sortBy,
            sortAsc = sortAsc,
            mediaType = mediaType,
            collectionIds = collectionIds,
            conceptIds = conceptIds
        )
    }

    suspend fun getRecordDetail(idx: Int): Result<MediaRecord> = runCatchingCancellable {
        apiClient.apiService.getRecordDetail(idx)
    }

    suspend fun getTimeline(mediaType: String = "all"): Result<com.iris.app.data.model.TimelineResponse> =
        runCatchingCancellable { apiClient.apiService.getTimeline(mediaType) }

    suspend fun renameRecord(idx: Int, name: String): Result<Unit> = runCatchingCancellable {
        apiClient.apiService.renameRecord(idx, name)
        Unit
    }

    suspend fun getRecordMetadata(idx: Int): Result<RecordMetadataResponse> = runCatchingCancellable {
        apiClient.apiService.getRecordMetadata(idx)
    }

    suspend fun searchText(
        query: String,
        topK: Int = 50,
        threshold: Float = 0.15f,
        balance: Float = 0.5f,
        textBonus: Float = 1.0f,
        lexicalWeight: Float = 0.25f,
        translate: Boolean = true,
        mediaType: String = "all",
        collectionIds: String = "",
        conceptIds: String = ""
    ): Result<SearchResponse> = runCatchingCancellable {
        apiClient.apiService.searchText(
            query = query,
            topK = topK,
            threshold = threshold,
            balance = balance,
            textBonus = textBonus,
            lexicalWeight = lexicalWeight,
            translate = translate,
            mediaType = mediaType,
            collectionIds = collectionIds,
            conceptIds = conceptIds
        )
    }

    suspend fun searchFilename(
        query: String,
        topK: Int = 50,
        mediaType: String = "all",
        collectionIds: String = "",
        conceptIds: String = ""
    ): Result<SearchResponse> = runCatchingCancellable {
        apiClient.apiService.searchFilename(
            query = query,
            topK = topK,
            mediaType = mediaType,
            collectionIds = collectionIds,
            conceptIds = conceptIds
        )
    }

    suspend fun searchSimilar(idx: Int, topK: Int = 30): Result<SearchResponse> = runCatchingCancellable {
        apiClient.apiService.searchSimilar(idx = idx, topK = topK)
    }

    suspend fun searchRandom(count: Int = 24): Result<SearchResponse> = runCatchingCancellable {
        apiClient.apiService.searchRandom(count = count)
    }

    suspend fun getPersons(): Result<List<Person>> = runCatchingCancellable {
        apiClient.apiService.getPersons().persons
    }

    suspend fun getPersonMedia(personId: Int): Result<PersonMediaResponse> = runCatchingCancellable {
        apiClient.apiService.getPersonMedia(personId)
    }

    suspend fun getCollections(): Result<List<IrisCollection>> = runCatchingCancellable {
        apiClient.apiService.getCollections().collections
    }

    suspend fun getCollectionMembers(collectionId: Int): Result<List<MediaRecord>> = runCatchingCancellable {
        val response = apiClient.apiService.getCollectionMembers(collectionId)
        response.records.ifEmpty { response.members }
    }

    suspend fun getConcepts(): Result<List<IrisConcept>> = runCatchingCancellable {
        apiClient.apiService.getConcepts().concepts
    }
}
