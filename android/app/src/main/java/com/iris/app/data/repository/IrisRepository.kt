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
    ): Result<DeviceLoginResponse> = runCatching {
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
            username = username
        )
        response
    }

    fun logoutDevice() {
        credentialsStore.clearCredentials()
    }

    suspend fun getUploadQueue(): List<LocalUploadJob> {
        return dbHelper.getAllJobs()
    }

    suspend fun triggerSync(): Result<Int> = runCatching {
        val discovered = mediaScanner.scanAndEnqueueNewMedia()
        uploadManager.processQueue()
        changeFeedSync.syncChanges()
        discovered
    }

    suspend fun checkServerHealth(): Result<com.iris.app.data.model.HealthResponse> = runCatching {
        apiClient.apiService.getHealth()
    }

    suspend fun getServerInfo(): Result<ServerInfo> = runCatching {
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
    ): Result<RecordsResponse> = runCatching {
        apiClient.apiService.getRecords(
            page = page,
            perPage = perPage,
            sortBy = sortBy,
            sortAsc = sortAsc,
            mediaType = mediaType,
            collectionIds = collectionIds,
            conceptIds = conceptIds
        )
    }

    suspend fun getRecordDetail(idx: Int): Result<MediaRecord> = runCatching {
        apiClient.apiService.getRecordDetail(idx)
    }

    suspend fun getRecordMetadata(idx: Int): Result<RecordMetadataResponse> = runCatching {
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
    ): Result<SearchResponse> = runCatching {
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
    ): Result<SearchResponse> = runCatching {
        apiClient.apiService.searchFilename(
            query = query,
            topK = topK,
            mediaType = mediaType,
            collectionIds = collectionIds,
            conceptIds = conceptIds
        )
    }

    suspend fun searchSimilar(idx: Int, topK: Int = 30): Result<SearchResponse> = runCatching {
        apiClient.apiService.searchSimilar(idx = idx, topK = topK)
    }

    suspend fun searchRandom(count: Int = 24): Result<SearchResponse> = runCatching {
        apiClient.apiService.searchRandom(count = count)
    }

    suspend fun getPersons(): Result<List<Person>> = runCatching {
        apiClient.apiService.getPersons().persons
    }

    suspend fun getPersonMedia(personId: Int): Result<PersonMediaResponse> = runCatching {
        apiClient.apiService.getPersonMedia(personId)
    }

    suspend fun getCollections(): Result<List<IrisCollection>> = runCatching {
        apiClient.apiService.getCollections().collections
    }

    suspend fun getCollectionMembers(collectionId: Int): Result<List<MediaRecord>> = runCatching {
        apiClient.apiService.getCollectionMembers(collectionId).members
    }

    suspend fun getConcepts(): Result<List<IrisConcept>> = runCatching {
        apiClient.apiService.getConcepts().concepts
    }
}
