package com.iris.app.data.repository

import com.iris.app.data.local.DeviceCredentialsStore
import com.iris.app.data.local.UploadDatabaseHelper
import com.iris.app.data.model.DeviceLoginResponse
import com.iris.app.data.model.IrisCollection
import com.iris.app.data.model.IrisConcept
import com.iris.app.data.model.LocalUploadJob
import com.iris.app.data.model.UploadJobState
import com.iris.app.data.model.MediaRecord
import com.iris.app.data.model.MediaScanPolicy
import com.iris.app.data.model.Person
import com.iris.app.data.model.PersonMediaResponse
import com.iris.app.data.model.RecordMetadataResponse
import com.iris.app.data.model.RecordsResponse
import com.iris.app.data.model.SearchResponse
import com.iris.app.data.model.ServerInfo
import com.iris.app.data.model.AddSpaceItemRequest
import com.iris.app.data.model.AddSpaceItemResponse
import com.iris.app.data.model.CreateSpaceRequest
import com.iris.app.data.model.SaveSpaceItemResponse
import com.iris.app.data.model.SpaceAlbum
import com.iris.app.data.model.SpaceItemsResponse
import com.iris.app.data.model.SpaceSearchResponse
import com.iris.app.data.model.SpaceMember
import com.iris.app.data.model.SpaceSummary
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import retrofit2.HttpException
import com.iris.app.data.remote.IrisApiClient
import com.iris.app.data.sync.ChangeFeedSyncManager
import com.iris.app.data.sync.MediaStoreScanner
import com.iris.app.data.sync.SyncQueueCoordinator
import com.iris.app.data.sync.AccountSyncSession
import com.iris.app.data.sync.SyncUploadManager
import com.iris.app.performance.Metric
import com.iris.app.performance.PerformanceMonitor
import java.util.concurrent.atomic.AtomicBoolean

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
    val changeFeedSync: ChangeFeedSyncManager,
    private val performanceMonitor: PerformanceMonitor? = null,
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
            serverOrigin = IrisApiClient.getOrigin(apiClient.baseUrl),
            userId = response.user?.id
        )
        response
    }

    fun logoutDevice() {
        credentialsStore.clearCredentials()
    }

    suspend fun getUploadQueue(): List<LocalUploadJob> {
        val accountKey = credentialsStore.accountIdentity.value ?: return emptyList()
        return dbHelper.getAllJobs(accountKey)
    }

    /** Newest jobs only: the screen shows a window, never the whole queue. */
    suspend fun getRecentUploadJobs(limit: Int): List<LocalUploadJob> {
        val accountKey = credentialsStore.accountIdentity.value ?: return emptyList()
        return dbHelper.getRecentJobs(accountKey, limit)
    }

    suspend fun getUploadQueueCounts(): Map<UploadJobState, Int> {
        val accountKey = credentialsStore.accountIdentity.value ?: return emptyMap()
        return dbHelper.countsByState(accountKey)
    }

    suspend fun getUnassignedPendingUploadCount(): Int =
        if (credentialsStore.accountIdentity.value == null) 0 else dbHelper.countUnassignedPendingJobs()

    suspend fun triggerSync(policy: MediaScanPolicy = MediaScanPolicy()): Result<Int> = runCatchingCancellable {
        val syncStartedAtNanos = System.nanoTime()
        val firstUploadMetricRecorded = AtomicBoolean(false)
        val onFirstUploadJobClaimed = {
            if (firstUploadMetricRecorded.compareAndSet(false, true)) {
                performanceMonitor?.record(
                    Metric.SyncFirstUploadJobStart,
                    (System.nanoTime() - syncStartedAtNanos) / 1_000_000.0
                )
            }
        }
        val sessionIdentity = credentialsStore.sessionIdentity.value
            ?: error("Faça login antes de sincronizar")
        val accountKey = credentialsStore.accountIdentity.value
            ?: error("A conta não pôde ser identificada; entre novamente antes de sincronizar")
        val syncSession = AccountSyncSession(sessionIdentity, accountKey)
        val isSessionCurrent = {
            syncSession.matches(
                credentialsStore.sessionIdentity.value,
                credentialsStore.accountIdentity.value
            )
        }
        val syncResult = SyncQueueCoordinator.scanAndDrain(
            scanAndEnqueue = { onNewJobEnqueued ->
                mediaScanner.scanAndEnqueueNewMedia(
                    accountKey = accountKey,
                    policy = policy,
                    isSessionCurrent = isSessionCurrent,
                    onNewJobEnqueued = onNewJobEnqueued,
                )
            },
            drainQueue = { workSignal ->
                uploadManager.processQueue(
                    accountKey = accountKey,
                    sessionIdentity = sessionIdentity,
                    workSignal = workSignal,
                    isSessionCurrent = isSessionCurrent,
                    onFirstUploadJobClaimed = onFirstUploadJobClaimed,
                )
            },
        )
        check(syncResult.queueCompleted) {
            "O envio ficou incompleto e será retomado na próxima sincronização"
        }
        changeFeedSync.syncChanges(accountKey, sessionIdentity, isSessionCurrent)
        syncResult.scanResult
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

    suspend fun getCollectionMembersPage(
        collectionId: Int,
        page: Int = 1,
        perPage: Int = 24
    ): Result<RecordsResponse> = getRecords(
        page = page,
        perPage = perPage,
        sortBy = "data",
        collectionIds = collectionId.toString()
    )

    suspend fun getConcepts(): Result<List<IrisConcept>> = runCatchingCancellable {
        apiClient.apiService.getConcepts().concepts
    }

    // ── Shared spaces ────────────────────────────────────────────────────
    // Failures carry the server's own sentence ("O espaço precisa de pelo
    // menos um gestor..."), which says more than an HTTP status code.

    suspend fun getSpaces(): Result<List<SpaceSummary>> = serverCall {
        apiClient.apiService.getSpaces().spaces
    }

    suspend fun createSpace(name: String): Result<SpaceSummary> = serverCall {
        apiClient.apiService.createSpace(CreateSpaceRequest(name.trim())).space
    }

    suspend fun getSpace(spaceId: Int): Result<SpaceSummary> = serverCall {
        apiClient.apiService.getSpace(spaceId).space
    }

    suspend fun getSpaceItems(spaceId: Int, before: Int? = null): Result<SpaceItemsResponse> =
        serverCall { apiClient.apiService.getSpaceItems(spaceId, before = before) }

    suspend fun addToSpace(spaceId: Int, recordDbId: Int): Result<AddSpaceItemResponse> =
        serverCall { apiClient.apiService.addSpaceItem(spaceId, AddSpaceItemRequest(recordDbId)) }

    suspend fun saveSpaceItem(spaceId: Int, itemId: Int): Result<SaveSpaceItemResponse> =
        serverCall { apiClient.apiService.saveSpaceItem(spaceId, itemId) }

    suspend fun removeSpaceItem(spaceId: Int, itemId: Int): Result<Unit> = serverCall {
        val response = apiClient.apiService.removeSpaceItem(spaceId, itemId)
        if (!response.isSuccessful) throw HttpException(response)
    }

    suspend fun searchSpace(spaceId: Int, query: String): Result<SpaceSearchResponse> =
        serverCall { apiClient.apiService.searchSpace(spaceId, query.trim()) }

    suspend fun getSpaceAlbums(spaceId: Int): Result<List<SpaceAlbum>> = serverCall {
        apiClient.apiService.getSpaceAlbums(spaceId).albums
    }

    suspend fun getSpaceAlbumItems(spaceId: Int, albumId: Int, before: Int? = null): Result<SpaceItemsResponse> =
        serverCall { apiClient.apiService.getSpaceAlbumItems(spaceId, albumId, before = before) }

    suspend fun getSpaceMembers(spaceId: Int): Result<List<SpaceMember>> = serverCall {
        apiClient.apiService.getSpaceMembers(spaceId).members
    }

    /** Leave a space: remove your own membership. */
    suspend fun leaveSpace(spaceId: Int): Result<Unit> = serverCall {
        val me = apiClient.apiService.getSpaceMembers(spaceId).members.first { it.isYou }
        val response = apiClient.apiService.removeSpaceMember(spaceId, me.userId)
        if (!response.isSuccessful) throw HttpException(response)
    }

    private suspend fun <R> serverCall(block: suspend () -> R): Result<R> = try {
        Result.success(block())
    } catch (e: CancellationException) {
        throw e
    } catch (e: HttpException) {
        Result.failure(IllegalStateException(serverDetail(e) ?: "O servidor respondeu ${e.code()}", e))
    } catch (e: Throwable) {
        Result.failure(e)
    }

    private fun serverDetail(e: HttpException): String? = try {
        val body = e.response()?.errorBody()?.string().orEmpty()
        IrisApiClient.RESPONSE_JSON.parseToJsonElement(body)
            .jsonObject["detail"]?.jsonPrimitive?.contentOrNull
    } catch (_: Exception) {
        null
    }
}
