package com.iris.app.data.remote

import com.iris.app.data.model.ChangesResponse
import com.iris.app.data.model.IngestLimits
import com.iris.app.data.model.IngestResponse
import com.iris.app.data.model.CollectionMembersResponse
import com.iris.app.data.model.CollectionsResponse
import com.iris.app.data.model.ConceptsResponse
import com.iris.app.data.model.DeviceLoginResponse
import com.iris.app.data.model.DeviceRefreshResponse
import com.iris.app.data.model.MediaRecord
import com.iris.app.data.model.PersonMediaResponse
import com.iris.app.data.model.PersonsResponse
import com.iris.app.data.model.RecordMetadataResponse
import com.iris.app.data.model.RecordsResponse
import com.iris.app.data.model.SearchResponse
import com.iris.app.data.model.ServerInfo
import com.iris.app.data.model.SpeedTestResponse
import com.iris.app.data.model.UploadChunkResponse
import com.iris.app.data.model.UploadCompleteBatchRequest
import com.iris.app.data.model.UploadCompleteBatchResponse
import com.iris.app.data.model.UploadCompleteResponse
import com.iris.app.data.model.UploadInitRequest
import com.iris.app.data.model.UploadInitResponse
import com.iris.app.data.model.UploadInitBatchRequest
import com.iris.app.data.model.UploadInitBatchResponse
import com.iris.app.data.model.UploadStatusResponse
import com.iris.app.data.model.AddSpaceItemRequest
import com.iris.app.data.model.AddSpaceItemResponse
import com.iris.app.data.model.CreateSpaceRequest
import com.iris.app.data.model.SaveSpaceItemResponse
import com.iris.app.data.model.SpaceAlbumsResponse
import com.iris.app.data.model.SpaceItemsResponse
import com.iris.app.data.model.SpaceSearchResponse
import com.iris.app.data.model.SpaceMembersResponse
import com.iris.app.data.model.SpaceResponse
import com.iris.app.data.model.SpacesResponse
import okhttp3.RequestBody
import retrofit2.Response
import retrofit2.http.Body
import retrofit2.http.DELETE
import retrofit2.http.Field
import retrofit2.http.FormUrlEncoded
import retrofit2.http.GET
import retrofit2.http.Header
import retrofit2.http.POST
import retrofit2.http.PUT
import retrofit2.http.Path
import retrofit2.http.Query

interface IrisApiService {

    // ── Device Authentication ───────────────────────────────────────────────
    @FormUrlEncoded
    @POST("api/auth/devices/login")
    suspend fun deviceLogin(
        @Field("username") username: String,
        @Field("password") password: String,
        @Field("device_name") deviceName: String,
        @Field("platform") platform: String = "android"
    ): DeviceLoginResponse

    @FormUrlEncoded
    @POST("api/auth/devices/refresh")
    suspend fun deviceRefresh(
        @Field("device_id") deviceId: String,
        @Field("refresh_token") refreshToken: String
    ): DeviceRefreshResponse

    // ── Sync Upload Protocol (Resumable & Chunked) ──────────────────────────
    @POST("api/sync/uploads")
    suspend fun initUpload(
        @Body request: UploadInitRequest
    ): UploadInitResponse

    @POST("api/sync/uploads/batch")
    suspend fun initUploadBatch(
        @Body request: UploadInitBatchRequest
    ): UploadInitBatchResponse

    @GET("api/sync/uploads/{upload_id}")
    suspend fun getUploadStatus(
        @Path("upload_id") uploadId: String
    ): UploadStatusResponse

    /** Sends synthetic bytes the server never keeps; [mode] is "discard" or "disk". */
    @POST("api/sync/speedtest")
    suspend fun speedTest(
        @Query("mode") mode: String,
        @Body body: RequestBody
    ): SpeedTestResponse

    @PUT("api/sync/uploads/{upload_id}")
    suspend fun uploadChunk(
        @Path("upload_id") uploadId: String,
        @Query("offset") offset: Long,
        @Body body: RequestBody
    ): Response<UploadChunkResponse>

    @POST("api/sync/uploads/{upload_id}/complete")
    suspend fun completeUpload(
        @Path("upload_id") uploadId: String
    ): UploadCompleteResponse

    @POST("api/sync/uploads/complete-batch")
    suspend fun completeUploadBatch(
        @Body request: UploadCompleteBatchRequest
    ): UploadCompleteBatchResponse

    /** What a batch may hold now; 404 from a server without batch ingest. */
    @GET("api/sync/ingest/limits")
    suspend fun getIngestLimits(): Response<IngestLimits>

    /**
     * Sends reserved uploads whole, back to back, and finishes them; [manifest]
     * lists them in body order as `upload_id:size`, separated by commas.
     */
    @POST("api/sync/ingest")
    suspend fun ingest(
        @Header("X-Iris-Ingest") manifest: String,
        @Body body: RequestBody
    ): Response<IngestResponse>

    // ── Change Feed Sync ────────────────────────────────────────────────────
    @GET("api/sync/changes")
    suspend fun getChanges(
        @Query("cursor") cursor: Long = 0L,
        @Query("limit") limit: Int = 200
    ): ChangesResponse

    // ── General System & Media Endpoints ────────────────────────────────────
    @GET("healthz")
    suspend fun getHealth(): com.iris.app.data.model.HealthResponse

    @GET("api/info")
    suspend fun getInfo(): ServerInfo

    @GET("api/records")
    suspend fun getRecords(
        @Query("page") page: Int = 1,
        @Query("per_page") perPage: Int = 24,
        @Query("sort_by") sortBy: String = "importacao",
        @Query("sort_asc") sortAsc: Int = 0,
        @Query("media_type") mediaType: String = "all",
        @Query("collection_ids") collectionIds: String = "",
        @Query("concept_ids") conceptIds: String = ""
    ): RecordsResponse

    @GET("api/records/{idx}")
    suspend fun getRecordDetail(
        @Path("idx") idx: Int
    ): MediaRecord

    @GET("api/records/timeline")
    suspend fun getTimeline(
        @Query("media_type") mediaType: String = "all"
    ): com.iris.app.data.model.TimelineResponse

    @FormUrlEncoded
    @POST("api/records/{idx}/rename")
    suspend fun renameRecord(
        @Path("idx") idx: Int,
        @Field("name") name: String
    ): Map<String, String>

    @GET("api/records/{idx}/metadata")
    suspend fun getRecordMetadata(
        @Path("idx") idx: Int
    ): RecordMetadataResponse

    @GET("api/search")
    suspend fun searchText(
        @Query("q") query: String,
        @Query("top_k") topK: Int = 50,
        @Query("threshold") threshold: Float = 0.15f,
        @Query("balance") balance: Float = 0.5f,
        @Query("text_bonus") textBonus: Float = 1.0f,
        @Query("lexical_weight") lexicalWeight: Float = 0.25f,
        @Query("translate") translate: Boolean = true,
        @Query("media_type") mediaType: String = "all",
        @Query("collection_ids") collectionIds: String = "",
        @Query("concept_ids") conceptIds: String = ""
    ): SearchResponse

    @GET("api/search/filename")
    suspend fun searchFilename(
        @Query("q") query: String,
        @Query("top_k") topK: Int = 50,
        @Query("media_type") mediaType: String = "all",
        @Query("collection_ids") collectionIds: String = "",
        @Query("concept_ids") conceptIds: String = ""
    ): SearchResponse

    @GET("api/search/similar/{idx}")
    suspend fun searchSimilar(
        @Path("idx") idx: Int,
        @Query("top_k") topK: Int = 30
    ): SearchResponse

    @GET("api/search/random")
    suspend fun searchRandom(
        @Query("k") count: Int = 24
    ): SearchResponse

    @GET("api/persons")
    suspend fun getPersons(): PersonsResponse

    @GET("api/persons/{person_id}/media")
    suspend fun getPersonMedia(
        @Path("person_id") personId: Int
    ): PersonMediaResponse

    @GET("api/collections")
    suspend fun getCollections(): CollectionsResponse

    @GET("api/collections/{col_id}/members")
    suspend fun getCollectionMembers(
        @Path("col_id") collectionId: Int
    ): CollectionMembersResponse

    @GET("api/concepts")
    suspend fun getConcepts(): ConceptsResponse

    // ── Shared spaces ────────────────────────────────────────────────────

    @GET("api/spaces")
    suspend fun getSpaces(): SpacesResponse

    @POST("api/spaces")
    suspend fun createSpace(@Body request: CreateSpaceRequest): SpaceResponse

    @GET("api/spaces/{space_id}")
    suspend fun getSpace(@Path("space_id") spaceId: Int): SpaceResponse

    @GET("api/spaces/{space_id}/items")
    suspend fun getSpaceItems(
        @Path("space_id") spaceId: Int,
        @Query("limit") limit: Int = 60,
        @Query("before") before: Int? = null
    ): SpaceItemsResponse

    @POST("api/spaces/{space_id}/items")
    suspend fun addSpaceItem(
        @Path("space_id") spaceId: Int,
        @Body request: AddSpaceItemRequest
    ): AddSpaceItemResponse

    @POST("api/spaces/{space_id}/items/{item_id}/save")
    suspend fun saveSpaceItem(
        @Path("space_id") spaceId: Int,
        @Path("item_id") itemId: Int
    ): SaveSpaceItemResponse

    @DELETE("api/spaces/{space_id}/items/{item_id}")
    suspend fun removeSpaceItem(
        @Path("space_id") spaceId: Int,
        @Path("item_id") itemId: Int
    ): Response<Unit>

    @GET("api/spaces/{space_id}/search")
    suspend fun searchSpace(
        @Path("space_id") spaceId: Int,
        @Query("q") query: String,
        @Query("limit") limit: Int = 60
    ): SpaceSearchResponse

    @GET("api/spaces/{space_id}/albums")
    suspend fun getSpaceAlbums(@Path("space_id") spaceId: Int): SpaceAlbumsResponse

    @GET("api/spaces/{space_id}/albums/{album_id}/items")
    suspend fun getSpaceAlbumItems(
        @Path("space_id") spaceId: Int,
        @Path("album_id") albumId: Int,
        @Query("limit") limit: Int = 60,
        @Query("before") before: Int? = null
    ): SpaceItemsResponse

    @GET("api/spaces/{space_id}/members")
    suspend fun getSpaceMembers(@Path("space_id") spaceId: Int): SpaceMembersResponse

    @DELETE("api/spaces/{space_id}/members/{user_id}")
    suspend fun removeSpaceMember(
        @Path("space_id") spaceId: Int,
        @Path("user_id") userId: Int
    ): Response<Unit>
}
