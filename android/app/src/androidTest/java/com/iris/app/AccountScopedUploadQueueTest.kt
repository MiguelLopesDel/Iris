package com.iris.app

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import android.database.sqlite.SQLiteDatabase
import android.content.ContentValues
import android.net.Uri
import android.os.Build
import android.provider.MediaStore
import android.util.Log
import com.iris.app.data.local.DeviceAuthStore
import com.iris.app.data.local.UploadDatabaseHelper
import com.iris.app.data.model.HealthResponse
import com.iris.app.data.model.MediaScanPolicy
import com.iris.app.data.model.UploadJobState
import com.iris.app.data.remote.IrisApiClient
import com.iris.app.data.remote.IrisApiService
import com.iris.app.data.sync.MediaStoreScanner
import com.iris.app.data.sync.ResumableUploadTransfer
import com.iris.app.data.sync.SyncUploadManager
import com.iris.app.data.sync.SyncQueueCoordinator
import com.iris.app.performance.Metric
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.delay
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import okhttp3.RequestBody
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import okio.Buffer
import okio.BufferedSink
import okio.ForwardingSink
import okio.buffer
import org.json.JSONArray
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File
import java.io.FileInputStream
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.atomic.AtomicLong
import java.util.concurrent.locks.LockSupport

/** A queued upload from one account must never be sent with another account's token. */
@RunWith(AndroidJUnit4::class)
class AccountScopedUploadQueueTest {

    private lateinit var server: MockWebServer
    private lateinit var app: IrisApplication
    private lateinit var dbHelper: UploadDatabaseHelper
    private lateinit var testDatabaseName: String
    private val requests = CopyOnWriteArrayList<RecordedRequest>()
    private val delayInitRequests = AtomicBoolean(false)
    private val holdInitRequests = AtomicBoolean(false)
    private val heldInitRequestTarget = AtomicInteger(0)
    private var allInitRequestsHeld = CompletableDeferred<Unit>()
    private var releaseHeldInitRequests = CountDownLatch(1)
    private val activeInitRequests = AtomicInteger(0)
    private val maxActiveInitRequests = AtomicInteger(0)
    private val delayChunkRequests = AtomicBoolean(false)
    private val activeChunkRequests = AtomicInteger(0)
    private val maxActiveChunkRequests = AtomicInteger(0)
    private val delayCompleteRequests = AtomicBoolean(false)
    private val simulatedResponseDelayMillis = AtomicLong(200L)
    private val activeCompleteRequests = AtomicInteger(0)
    private val maxActiveCompleteRequests = AtomicInteger(0)
    private val initSequence = AtomicInteger(0)
    private val batchUploadIds = ConcurrentHashMap<String, String>()
    private var firstInitRequestObserved = CompletableDeferred<Unit>()
    private var serverChunkSizeBytes = 32768L
    private val uploadIdFilenames = ConcurrentHashMap<String, String>()
    private val rejectChunksForFilenames = ConcurrentHashMap.newKeySet<String>()
    private val loseUploadOnceForFilenames = ConcurrentHashMap.newKeySet<String>()
    private val unavailableOnceForFilenames = ConcurrentHashMap.newKeySet<String>()
    private val hashMismatchOnceForFilenames = ConcurrentHashMap.newKeySet<String>()

    private fun initializedMediaCount(recorded: List<RecordedRequest>): Int = recorded.sumOf { request ->
        when (request.path) {
            "/api/sync/uploads" -> if (request.method == "POST") 1 else 0
            "/api/sync/uploads/batch" -> if (request.method == "POST") {
                JSONObject(request.body.clone().readUtf8()).getJSONArray("uploads").length()
            } else 0
            else -> 0
        }
    }

    private fun initRpcCount(recorded: List<RecordedRequest>): Int = recorded.count {
        it.method == "POST" && it.path in setOf("/api/sync/uploads", "/api/sync/uploads/batch")
    }

    private fun completedMediaCount(recorded: List<RecordedRequest>): Int = recorded.sumOf { request ->
        when {
            request.method != "POST" -> 0
            request.path?.endsWith("/complete") == true -> 1
            request.path == "/api/sync/uploads/complete-batch" ->
                JSONObject(request.body.clone().readUtf8()).getJSONArray("uploads").length()
            else -> 0
        }
    }

    private fun completionRpcCount(recorded: List<RecordedRequest>): Int = recorded.count {
        it.method == "POST" && (
            it.path?.endsWith("/complete") == true ||
                it.path == "/api/sync/uploads/complete-batch"
            )
    }

    private fun initBatchSizes(recorded: List<RecordedRequest>): List<Int> = recorded.mapNotNull { request ->
        when (request.path) {
            "/api/sync/uploads" -> if (request.method == "POST") 1 else null
            "/api/sync/uploads/batch" -> if (request.method == "POST") {
                JSONObject(request.body.clone().readUtf8()).getJSONArray("uploads").length()
            } else null
            else -> null
        }
    }

    @Before
    fun setUp() {
        server = MockWebServer()
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                requests += request
                return when {
                request.path == "/api/sync/uploads/stale-upload/complete" ->
                    MockResponse().setResponseCode(404)
                request.path == "/api/sync/uploads/complete-batch" -> {
                    if (delayCompleteRequests.get()) {
                        val active = activeCompleteRequests.incrementAndGet()
                        maxActiveCompleteRequests.updateAndGet { current -> maxOf(current, active) }
                        try {
                            Thread.sleep(simulatedResponseDelayMillis.get())
                        } finally {
                            activeCompleteRequests.decrementAndGet()
                        }
                    }
                    val uploads = JSONObject(request.body.clone().readUtf8()).getJSONArray("uploads")
                    val results = JSONArray()
                    for (index in 0 until uploads.length()) {
                        val uploadId = uploads.getJSONObject(index).getString("upload_id")
                        val mismatch = uploadIdFilenames[uploadId]?.let(hashMismatchOnceForFilenames::remove) == true
                        results.put(
                            JSONObject()
                                .put("upload_id", uploadId)
                                .put("state", if (uploadId == "stale-upload" || mismatch) null else "ready")
                                .apply {
                                    if (uploadId == "stale-upload") put("error_code", 404)
                                    if (mismatch) {
                                        put("error_code", 422)
                                        put("error_message", "Hash do arquivo não confere")
                                    }
                                },
                        )
                    }
                    MockResponse().setHeader("Content-Type", "application/json")
                        .setBody(JSONObject().put("uploads", results).toString())
                }
                request.path?.startsWith("/api/sync/uploads/") == true && request.path?.endsWith("/complete") == true ->
                    {
                        if (delayCompleteRequests.get()) {
                            val active = activeCompleteRequests.incrementAndGet()
                            maxActiveCompleteRequests.updateAndGet { current -> maxOf(current, active) }
                            try {
                                Thread.sleep(simulatedResponseDelayMillis.get())
                            } finally {
                                activeCompleteRequests.decrementAndGet()
                            }
                        }
                        val uploadId = request.path.orEmpty().substringBefore("?")
                            .substringBeforeLast("/complete").substringAfterLast("/")
                        MockResponse().setHeader("Content-Type", "application/json")
                            .setBody("""{"upload_id":"$uploadId","state":"ready"}""")
                    }
                    request.path == "/api/sync/uploads/batch" || request.path == "/api/sync/uploads" -> {
                        firstInitRequestObserved.complete(Unit)
                        if (delayInitRequests.get() || holdInitRequests.get()) {
                            val active = activeInitRequests.incrementAndGet()
                            maxActiveInitRequests.updateAndGet { current -> maxOf(current, active) }
                            try {
                                if (holdInitRequests.get()) {
                                    if (active >= heldInitRequestTarget.get()) allInitRequestsHeld.complete(Unit)
                                    releaseHeldInitRequests.await(5, TimeUnit.SECONDS)
                                } else {
                                    Thread.sleep(simulatedResponseDelayMillis.get())
                                }
                            } finally {
                                activeInitRequests.decrementAndGet()
                            }
                        }
                        if (request.path == "/api/sync/uploads") {
                            MockResponse().setHeader("Content-Type", "application/json")
                                .setBody(
                                    """{"upload_id":"account-b-upload-${initSequence.incrementAndGet()}","offset":0,"chunk_size":$serverChunkSizeBytes}"""
                                )
                        } else {
                            val uploads = JSONObject(request.body.clone().readUtf8()).getJSONArray("uploads")
                            val results = JSONArray()
                            for (index in 0 until uploads.length()) {
                                val item = uploads.getJSONObject(index)
                                val clientUploadId = item.getString("client_upload_id")
                                val uploadId = batchUploadIds.computeIfAbsent(clientUploadId) {
                                    "account-b-upload-${initSequence.incrementAndGet()}"
                                }
                                uploadIdFilenames[uploadId] = item.optString("filename")
                                results.put(
                                    JSONObject()
                                        .put("client_upload_id", clientUploadId)
                                        .put("upload_id", uploadId)
                                        .put("offset", 0)
                                        .put("chunk_size", serverChunkSizeBytes)
                                        .put("state", "uploading")
                                )
                            }
                            MockResponse().setHeader("Content-Type", "application/json")
                                .setBody(JSONObject().put("uploads", results).toString())
                        }
                    }
                    request.method == "PUT" -> {
                        if (delayChunkRequests.get()) {
                            val active = activeChunkRequests.incrementAndGet()
                            maxActiveChunkRequests.updateAndGet { current -> maxOf(current, active) }
                            try {
                                Thread.sleep(simulatedResponseDelayMillis.get())
                            } finally {
                                activeChunkRequests.decrementAndGet()
                            }
                        }
                        val requestPath = request.path.orEmpty()
                        val uploadId = requestPath.substringBefore("?").substringAfterLast("/")
                        val offset = requestPath.substringAfter("?offset=", "0").toLongOrNull() ?: 0L
                        val filename = uploadIdFilenames[uploadId]
                        if (filename in rejectChunksForFilenames) {
                            return MockResponse().setResponseCode(400).setBody("unsupported media")
                        }
                        // A 404 means the server lost the upload session: a transient failure.
                        if (filename != null && loseUploadOnceForFilenames.remove(filename)) {
                            return MockResponse().setResponseCode(404)
                        }
                        if (filename != null && unavailableOnceForFilenames.remove(filename)) {
                            return MockResponse().setResponseCode(503)
                        }
                        MockResponse().setHeader("Content-Type", "application/json")
                            .setBody(
                                """{"upload_id":"$uploadId","offset":${offset + request.bodySize}}"""
                            )
                    }
                    request.path?.startsWith("/api/sync/changes") == true ->
                        MockResponse().setHeader("Content-Type", "application/json")
                            .setBody("""{"changes":[],"next_cursor":0,"has_more":false}""")
                    request.path == "/healthz" ->
                        MockResponse().setHeader("Content-Type", "application/json")
                            .setBody("""{"status":"ok","mode":"multiuser"}""")
                    else -> MockResponse().setResponseCode(404)
                }
            }
        }
        server.start()

        val context = InstrumentationRegistry.getInstrumentation().targetContext
        app = context.applicationContext as IrisApplication
        testDatabaseName = "iris_sync_account_isolation_${System.nanoTime()}.db"
        dbHelper = UploadDatabaseHelper(context, testDatabaseName)

        runBlocking {
            app.settingsRepository.updateServerUrl(server.url("/").toString())
        }
        app.apiClient.updateBaseUrl(server.url("/").toString())
        app.allowCleartext(server)
        app.credentialsStore.clearCredentials()
    }

    @After
    fun tearDown() {
        app.credentialsStore.clearCredentials()
        app.allowCleartext(server, allowed = false)
        dbHelper.close()
        app.deleteDatabase(testDatabaseName)
        server.shutdown()
    }

    @Test
    fun queued_upload_from_account_a_stays_paused_after_switching_to_b() = runBlocking {
        app.credentialsStore.saveSession(
            deviceId = "account-a-device",
            accessToken = "account-a-token",
            refreshToken = "account-a-refresh",
            expiresInSeconds = 3600,
            username = "account-a",
            serverOrigin = IrisApiClient.getOrigin(server.url("/").toString()),
            userId = 11
        )
        val accountAKey = app.credentialsStore.accountIdentity.value!!
        val rowId = dbHelper.insertOrIgnoreJob(
            accountKey = accountAKey,
            localUri = "content://media/external/images/media/100",
            filename = "account-a-photo.jpg",
            byteSize = 0,
            sha256 = "0".repeat(64),
            capturedAt = "2026-09-24T00:00:00Z"
        )
        assertTrue("The fixture upload should be queued", rowId > 0)
        val localUri = "content://media/external/images/media/100"
        assertTrue("Account A should see its own queue row", dbHelper.isUriEnqueued(accountAKey, localUri))
        assertFalse("Account B must not inherit Account A's queue row", dbHelper.isUriEnqueued("server|user:12", localUri))
        assertEquals(
            "The same account must not enqueue the same local media twice",
            -1L,
            dbHelper.insertOrIgnoreJob(
                accountKey = accountAKey,
                localUri = localUri,
                filename = "account-a-photo.jpg",
                byteSize = 0,
                sha256 = "0".repeat(64),
                capturedAt = "2026-09-24T00:00:00Z"
            )
        )

        app.credentialsStore.saveSession(
            deviceId = "account-b-device",
            accessToken = "account-b-token",
            refreshToken = "account-b-refresh",
            expiresInSeconds = 3600,
            username = "account-b",
            serverOrigin = IrisApiClient.getOrigin(server.url("/").toString()),
            userId = 12
        )
        val accountBKey = app.credentialsStore.accountIdentity.value!!
        val accountBSession = app.credentialsStore.sessionIdentity.value
        val manager = SyncUploadManager(app.contentResolver, dbHelper) { session ->
            app.apiClient.apiServiceForSession(session)
        }

        assertTrue(manager.processQueue(accountBKey, accountBSession!!) {
            app.credentialsStore.sessionIdentity.value == accountBSession
        })

        val uploadRequests = requests.filter { it.path?.startsWith("/api/sync/uploads") == true }
        assertEquals("Account A's pending media must not be uploaded as account B", 0, uploadRequests.size)
        assertEquals("The untransferred job must stay queued", "QUEUED", dbHelper.getAllJobs(accountAKey).single().state.name)
        assertTrue("Account B must not see Account A's queue", dbHelper.getAllJobs(accountBKey).isEmpty())
    }

    @Test
    fun same_local_media_can_have_separate_jobs_for_two_accounts() = runBlocking {
        val localUri = "content://media/external/images/media/shared-device-item"
        val accountAKey = "server|user:11"
        val accountBKey = "server|user:12"

        assertTrue(dbHelper.insertOrIgnoreJob(accountAKey, localUri, "photo.jpg", 20L, "a".repeat(64), "2026-09-24T00:00:00Z") > 0L)
        assertTrue(dbHelper.insertOrIgnoreJob(accountBKey, localUri, "photo.jpg", 20L, "a".repeat(64), "2026-09-24T00:00:00Z") > 0L)

        assertEquals(1, dbHelper.getAllJobs(accountAKey).size)
        assertEquals(1, dbHelper.getAllJobs(accountBKey).size)
    }

    @Test
    fun queue_uploads_distinct_media_concurrently_with_a_bounded_worker_pool() = runBlocking {
        val origin = IrisApiClient.getOrigin(server.url("/").toString())
        app.credentialsStore.saveSession(
            deviceId = "parallel-device",
            accessToken = "parallel-token",
            refreshToken = "parallel-refresh",
            expiresInSeconds = 3600,
            username = "parallel-user",
            serverOrigin = origin,
            userId = 31
        )
        val accountKey = app.credentialsStore.accountIdentity.value!!
        val sessionIdentity = app.credentialsStore.sessionIdentity.value!!
        val localFiles = mutableListOf<File>()
        repeat(12) { index ->
            val file = File(app.cacheDir, "iris-parallel-$index-${System.nanoTime()}.bin")
            file.writeBytes(ByteArray(64 * 1024) { byteIndex -> (byteIndex % 251).toByte() })
            localFiles += file
            assertTrue(
                dbHelper.insertOrIgnoreJob(
                    accountKey = accountKey,
                    localUri = Uri.fromFile(file).toString(),
                    filename = file.name,
                    byteSize = file.length(),
                    sha256 = index.toString().padStart(64, 'a'),
                    capturedAt = "2026-09-24T00:00:00Z"
                ) > 0L
            )
        }

        delayInitRequests.set(true)
        delayChunkRequests.set(true)
        val firstUploadJobClaims = AtomicInteger(0)
        val manager = SyncUploadManager(app.contentResolver, dbHelper) { session ->
            app.apiClient.apiServiceForSession(session)
        }
        try {
            assertTrue(
                manager.processQueue(
                    accountKey,
                    sessionIdentity,
                    isSessionCurrent = { app.credentialsStore.sessionIdentity.value == sessionIdentity },
                    onFirstUploadJobClaimed = { firstUploadJobClaims.incrementAndGet() },
                )
            )
        } finally {
            delayInitRequests.set(false)
            delayChunkRequests.set(false)
            localFiles.forEach(File::delete)
        }

        assertTrue(
            "Concurrent small-file reservations should be combined into fewer RPCs",
            initRpcCount(requests.toList()) < 12
        )
        assertEquals("First-claim timing should be emitted once per queue drain", 1, firstUploadJobClaims.get())
        assertTrue(
            "Upload concurrency must remain bounded to protect the phone and server",
            maxActiveInitRequests.get() <= SyncUploadManager.MAX_CONCURRENT_UPLOADS
        )
        assertTrue(
            "File chunks should overlap request latency across workers",
            maxActiveChunkRequests.get() > 1
        )
        assertTrue(
            "Chunk concurrency must remain bounded to protect the phone and server",
            maxActiveChunkRequests.get() <= SyncUploadManager.MAX_CONCURRENT_UPLOADS
        )
        assertEquals(12, initializedMediaCount(requests.toList()))
        assertEquals(24, requests.count { it.method == "PUT" })
        assertEquals(12 * 64 * 1024L, requests.filter { it.method == "PUT" }.sumOf { it.bodySize })
        assertTrue(dbHelper.getAllJobs(accountKey).all { it.state.name == "READY" })
    }

    @Test
    fun interactive_health_request_keeps_independent_capacity_during_full_upload_pool() = runBlocking {
        val origin = IrisApiClient.getOrigin(server.url("/").toString())
        app.credentialsStore.saveSession(
            deviceId = "dispatcher-isolation-device",
            accessToken = "dispatcher-isolation-token",
            refreshToken = "dispatcher-isolation-refresh",
            expiresInSeconds = 3600,
            username = "dispatcher-isolation-user",
            serverOrigin = origin,
            userId = 47,
        )
        val accountKey = app.credentialsStore.accountIdentity.value!!
        val sessionIdentity = app.credentialsStore.sessionIdentity.value!!
        val uploadCount = SyncUploadManager.MAX_SUPPORTED_UPLOADS
        repeat(uploadCount) { index ->
            assertTrue(
                dbHelper.insertOrIgnoreJob(
                    accountKey = accountKey,
                    localUri = "content://media/external/images/media/dispatcher-$index",
                    filename = "dispatcher-$index.jpg",
                    byteSize = 0,
                    sha256 = "dispatcher-$index".padEnd(64, 'd'),
                    capturedAt = "2026-09-24T00:00:00Z",
                ) > 0L,
            )
        }

        // All workers can join one reservation batch, so there may be only a
        // single active HTTP init request even though the entire worker pool
        // is blocked waiting for its response.
        heldInitRequestTarget.set(1)
        holdInitRequests.set(true)
        val manager = SyncUploadManager(
            contentResolver = app.contentResolver,
            dbHelper = dbHelper,
            maxConcurrentUploads = uploadCount,
            apiServiceProvider = { session -> app.apiClient.apiServiceForSession(session) },
        )
        val uploads = async(Dispatchers.IO) {
            manager.processQueue(accountKey, sessionIdentity)
        }
        try {
            withTimeout(5_000) { allInitRequestsHeld.await() }
            assertEquals("The batched upload dispatcher should have its init request held", 1, activeInitRequests.get())

            // This models a browsing/status call through IrisRepository's API facade.
            // It must remain independent of the session-bound upload dispatcher.
            val interactiveStartedAt = System.nanoTime()
            val interactiveHealth = app.apiClient.apiService.getHealth()
            val interactiveElapsedMs = (System.nanoTime() - interactiveStartedAt) / 1_000_000.0
            assertEquals("ok", interactiveHealth.status)
            assertTrue(
                "Interactive health was delayed while upload init requests were held: ${formatMs(interactiveElapsedMs)} ms",
                interactiveElapsedMs < 1_000.0,
            )
            assertEquals("Upload requests must remain held during the interactive call", 1, activeInitRequests.get())
            assertTrue("The active upload init batch should remain in flight", requests.any { it.path == "/api/sync/uploads/batch" })
        } finally {
            releaseHeldInitRequests.countDown()
            holdInitRequests.set(false)
        }

        assertTrue("All queued uploads should complete after the held init calls are released", uploads.await())
        assertEquals(uploadCount, dbHelper.getAllJobs(accountKey).size)
        assertTrue(dbHelper.getAllJobs(accountKey).all { it.state.name == "READY" })
    }

    @Test
    fun media_store_scan_bench_reports_first_hashing_pass_and_unchanged_rescan() = runBlocking<Unit> {
        val accountKey = "media-store-bench-${System.nanoTime()}"
        val resolver = app.contentResolver
        val mediaUris = mutableListOf<Uri>()
        val fixtureSizes = listOf(1L, 1L, 36L, 36L).map { it * 1024L * 1024L }
        val fixtureKinds = listOf("image", "image", "video", "video")
        val fixtureRoot = "Pictures/IrisSyncBenchmark-${System.nanoTime()}/"
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        var adoptedMediaPermissions = false

        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                instrumentation.uiAutomation.adoptShellPermissionIdentity(
                    android.Manifest.permission.READ_MEDIA_IMAGES,
                    android.Manifest.permission.READ_MEDIA_VIDEO,
                    android.Manifest.permission.READ_MEDIA_VISUAL_USER_SELECTED,
                )
                adoptedMediaPermissions = true
            } else {
                runShellCommand(instrumentation, "pm grant ${app.packageName} ${android.Manifest.permission.READ_EXTERNAL_STORAGE}")
                runShellCommand(instrumentation, "pm grant ${app.packageName} ${android.Manifest.permission.WRITE_EXTERNAL_STORAGE}")
            }

            fixtureSizes.forEachIndexed { index, size ->
                val kind = fixtureKinds[index]
                val isVideo = kind == "video"
                val collection = if (isVideo) {
                    MediaStore.Video.Media.EXTERNAL_CONTENT_URI
                } else {
                    MediaStore.Images.Media.EXTERNAL_CONTENT_URI
                }
                val values = ContentValues().apply {
                    put(MediaStore.MediaColumns.DISPLAY_NAME, "iris-sync-bench-${System.nanoTime()}-$index.${if (isVideo) "mp4" else "jpg"}")
                    put(MediaStore.MediaColumns.MIME_TYPE, if (isVideo) "video/mp4" else "image/jpeg")
                    put(MediaStore.MediaColumns.RELATIVE_PATH, fixtureRoot)
                    put(MediaStore.MediaColumns.IS_PENDING, 1)
                }
                val uri = resolver.insert(collection, values)
                    ?: error("MediaStore refused benchmark fixture $index")
                mediaUris += uri
                resolver.openOutputStream(uri, "w")!!.use { output ->
                    val block = ByteArray(64 * 1024) { byteIndex -> ((byteIndex + index) % 251).toByte() }
                    var remaining = size
                    while (remaining > 0) {
                        val next = minOf(remaining, block.size.toLong()).toInt()
                        output.write(block, 0, next)
                        remaining -= next
                    }
                }
                val published = ContentValues().apply { put(MediaStore.MediaColumns.IS_PENDING, 0) }
                assertEquals(1, resolver.update(uri, published, null, null))
            }

            val scanner = MediaStoreScanner(
                contentResolver = resolver,
                uploadManager = SyncUploadManager(
                    contentResolver = resolver,
                    dbHelper = dbHelper,
                    apiServiceProvider = { error("MediaStore scan benchmark must not start uploads") },
                ),
            )
            val selectedSources = scanner.discoverSources()
                .filter { it.relativePath.trimEnd('/') == fixtureRoot.trimEnd('/') }
            assertEquals("The MediaStore fixture should expose image and video sources", setOf("image", "video"), selectedSources.map { it.mediaKind }.toSet())
            val policy = MediaScanPolicy(
                mode = "selected",
                selectedSourceIds = selectedSources.map { it.id }.toSet(),
                includeImages = true,
                includeVideos = true,
            )

            val firstStartedAt = System.nanoTime()
            val firstNewJobs = scanner.scanAndEnqueueNewMedia(accountKey, policy)
            val firstElapsedMs = (System.nanoTime() - firstStartedAt) / 1_000_000.0
            val secondStartedAt = System.nanoTime()
            val secondNewJobs = scanner.scanAndEnqueueNewMedia(accountKey, policy)
            val secondElapsedMs = (System.nanoTime() - secondStartedAt) / 1_000_000.0
            val totalBytes = fixtureSizes.sum()
            val effectiveHashScanMBps = totalBytes / (firstElapsedMs / 1_000.0) / 1_000_000.0

            assertEquals("The first scan should enqueue every benchmark media item", fixtureSizes.size, firstNewJobs)
            assertEquals("An unchanged rescan should not enqueue the same media again", 0, secondNewJobs)
            assertEquals(fixtureSizes.size, dbHelper.getAllJobs(accountKey).size)
            Log.i(
                "IrisUploadBench",
                "IRIS_MEDIASTORE_SCAN_BENCH items=${fixtureSizes.size} bytes=$totalBytes " +
                    "first_scan_ms=${formatMs(firstElapsedMs)} effective_hash_scan_MBps=${formatMs(effectiveHashScanMBps)} " +
                    "unchanged_rescan_ms=${formatMs(secondElapsedMs)} new_jobs=$firstNewJobs/$secondNewJobs",
            )
        } finally {
            try {
                mediaUris.forEach { uri -> resolver.delete(uri, null, null) }
            } finally {
                if (adoptedMediaPermissions) {
                    instrumentation.uiAutomation.dropShellPermissionIdentity()
                }
            }
        }
    }

    @Test
    fun upload_throughput_separates_media_scan_wait_from_active_put_time() = runBlocking<Unit> {
        val origin = IrisApiClient.getOrigin(server.url("/").toString())
        app.credentialsStore.saveSession(
            deviceId = "active-throughput-device",
            accessToken = "active-throughput-token",
            refreshToken = "active-throughput-refresh",
            expiresInSeconds = 3600,
            username = "active-throughput-user",
            serverOrigin = origin,
            userId = 82,
        )
        val accountKey = app.credentialsStore.accountIdentity.value!!
        val sessionIdentity = app.credentialsStore.sessionIdentity.value!!
        val media = File(app.cacheDir, "active-throughput-${System.nanoTime()}.jpg")
        writeUniqueBenchmarkFile(media, 1024 * 1024, seed = 82L)
        val monitor = app.performanceMonitor
        val manager = SyncUploadManager(
            contentResolver = app.contentResolver,
            dbHelper = dbHelper,
            performanceMonitor = monitor,
            apiServiceProvider = { session -> app.apiClient.apiServiceForSession(session) },
        )

        monitor.start()
        try {
            val result = SyncQueueCoordinator.scanAndDrain(
                scanAndEnqueue = { signalNewWork ->
                    // A deterministic cold-scan gap: the real drainer starts before
                    // discovery and must wait for this durable queue insertion.
                    delay(350)
                    assertTrue(
                        manager.enqueueMedia(
                            accountKey = accountKey,
                            uri = Uri.fromFile(media),
                            filename = media.name,
                            size = media.length(),
                            capturedAtIso = "2026-09-25T00:00:00Z",
                        ) > 0L,
                    )
                    signalNewWork()
                    1
                },
                drainQueue = { workSignal ->
                    manager.processQueue(
                        accountKey = accountKey,
                        sessionIdentity = sessionIdentity,
                        workSignal = workSignal,
                    )
                },
            )
            assertEquals(1, result.scanResult)
            assertTrue(result.queueCompleted)
            assertEquals(UploadJobState.READY, dbHelper.getAllJobs(accountKey).single().state)
        } finally {
            monitor.stop()
            media.delete()
        }

        val upload = monitor.report.value.upload!!
        assertEquals(1024L * 1024L, upload.bytes)
        assertEquals(1L, upload.confirmedItems)
        assertTrue(upload.itemsPerSecond > 0.0)
        assertTrue("Queue wall time should include the simulated scan wait", upload.elapsedMillis >= 350.0)
        assertTrue("Active PUT time must exclude the simulated scan wait", upload.activeElapsedMillis < upload.elapsedMillis - 250.0)
        assertTrue("Active PUT throughput should exceed wall queue throughput", upload.activeMibPerSecond > upload.mibPerSecond)
    }

    @Test
    fun upload_control_plane_benchmark_measures_small_item_round_trips() = runBlocking<Unit> {
        val origin = IrisApiClient.getOrigin(server.url("/").toString())
        app.credentialsStore.saveSession(
            deviceId = "control-benchmark-device",
            accessToken = "control-benchmark-token",
            refreshToken = "control-benchmark-refresh",
            expiresInSeconds = 3600,
            username = "control-benchmark-user",
            serverOrigin = origin,
            userId = 42
        )
        val accountKey = app.credentialsStore.accountIdentity.value!!
        val sessionIdentity = app.credentialsStore.sessionIdentity.value!!
        val fileCount = 32
        repeat(fileCount) { index ->
            val hash = "control-${index.toString().padStart(2, '0')}".padEnd(64, 'x')
            assertTrue(
                dbHelper.insertOrIgnoreJob(
                    accountKey = accountKey,
                    localUri = "content://media/external/images/media/control-$index",
                    filename = "control-$index.jpg",
                    byteSize = 0,
                    sha256 = hash,
                    capturedAt = "2026-09-24T00:00:00Z"
                ) > 0L
            )
        }

        // A deterministic response delay makes the effect of per-file control
        // round trips visible without pretending to model a real network path.
        delayInitRequests.set(true)
        delayCompleteRequests.set(true)
        val manager = SyncUploadManager(app.contentResolver, dbHelper) { session ->
            app.apiClient.apiServiceForSession(session)
        }
        val startedAt = System.nanoTime()
        try {
            val completed = manager.processQueue(accountKey, sessionIdentity)
            assertTrue(
                "Upload queue failed: ${dbHelper.getAllJobs(accountKey).map { it.state to it.errorMessage }}; " +
                    "requests=${requests.map { it.path }}",
                completed,
            )
        } finally {
            delayInitRequests.set(false)
            delayCompleteRequests.set(false)
        }
        val elapsedMillis = (System.nanoTime() - startedAt) / 1_000_000.0
        val uploadRequests = requests.filter { it.path?.startsWith("/api/sync/uploads") == true }
        val initCount = initializedMediaCount(uploadRequests)
        val initBatches = initRpcCount(uploadRequests)
        val completeCount = completedMediaCount(uploadRequests)

        assertEquals(fileCount, initCount)
        assertEquals(fileCount, completeCount)
        assertEquals("Zero-byte fixtures isolate control calls", 0, uploadRequests.count { it.method == "PUT" })
        assertTrue("Init calls should be reduced by batching", initBatches < fileCount)
        assertTrue(
            "Init concurrency must remain bounded",
            maxActiveInitRequests.get() <= SyncUploadManager.MAX_CONCURRENT_UPLOADS,
        )
        assertTrue("Completions should be coalesced into fewer control calls", completionRpcCount(uploadRequests) < fileCount)
        assertTrue(
            "Completion concurrency must remain bounded",
            maxActiveCompleteRequests.get() <= SyncUploadManager.MAX_CONCURRENT_UPLOADS,
        )
        assertTrue(dbHelper.getAllJobs(accountKey).all { it.state.name == "READY" })

        Log.i(
            "IrisUploadBench",
            "IRIS_UPLOAD_CONTROL_BENCH files=$fileCount bytes=0 upload_requests=${uploadRequests.size} " +
                "init_items=$initCount init_batches=$initBatches complete=$completeCount " +
                "response_delay_ms=200 elapsed_ms=${formatMs(elapsedMillis)} " +
                "max_active_init=${maxActiveInitRequests.get()} max_active_complete=${maxActiveCompleteRequests.get()}"
        )
    }

    @Test
    fun upload_worker_sweep_measures_controlled_rtt_across_supported_pool_sizes() = runBlocking<Unit> {
        val origin = IrisApiClient.getOrigin(server.url("/").toString())
        app.credentialsStore.saveSession(
            deviceId = "worker-sweep-device",
            accessToken = "worker-sweep-token",
            refreshToken = "worker-sweep-refresh",
            expiresInSeconds = 3600,
            username = "worker-sweep-user",
            serverOrigin = origin,
            userId = 45
        )
        val accountKey = app.credentialsStore.accountIdentity.value!!
        val sessionIdentity = app.credentialsStore.sessionIdentity.value!!
        val fileCount = 32
        val bytesPerFile = 1024 * 1024
        val totalBytes = fileCount * bytesPerFile
        serverChunkSizeBytes = bytesPerFile.toLong()
        simulatedResponseDelayMillis.set(35L)
        delayInitRequests.set(true)
        delayChunkRequests.set(true)
        // Leave complete responses fast so the per-hash completion lock does
        // not make stripe collisions look like a transfer-worker bottleneck.
        delayCompleteRequests.set(false)
        val results = mutableMapOf<Int, MutableList<Double>>()

        try {
            // Two passes in opposite order reduce the chance that emulator
            // warm-up or thermal drift is mistaken for a worker-count effect.
            val sweep = listOf(1, 2, 4, 6, 8, 12, 16, 16, 12, 8, 6, 4, 2, 1)
            for ((trialIndex, workerCount) in sweep.withIndex()) {
                val files = (0 until fileCount).map { index ->
                    File(app.cacheDir, "bench-worker-$workerCount-$trialIndex-$index-${System.nanoTime()}.bin").also {
                        writeBenchmarkFile(it, bytesPerFile, seed = workerCount * 10_000 + trialIndex * 100 + index)
                    }
                }
                val manager = SyncUploadManager(
                    contentResolver = app.contentResolver,
                    dbHelper = dbHelper,
                    performanceMonitor = app.performanceMonitor,
                    maxConcurrentUploads = workerCount,
                    apiServiceProvider = { session -> app.apiClient.apiServiceForSession(session) },
                )

                try {
                    app.performanceMonitor.start()
                    val enqueueStartedAt = System.nanoTime()
                    files.forEach { file ->
                        assertTrue(
                            "Every worker-sweep fixture should be durably queued",
                            manager.enqueueMedia(
                                accountKey = accountKey,
                                uri = Uri.fromFile(file),
                                filename = file.name,
                                size = file.length(),
                                capturedAtIso = "2026-09-24T00:00:00Z",
                            ) > 0L,
                        )
                    }
                    val enqueueElapsedMillis = (System.nanoTime() - enqueueStartedAt) / 1_000_000.0
                    app.performanceMonitor.stop()
                    val hashMetric = app.performanceMonitor.report.value.metrics
                        .firstOrNull { it.name == "sync.media_hash.total" }
                    val startedAt = System.nanoTime()
                    app.performanceMonitor.start()
                    try {
                        assertTrue(manager.processQueue(accountKey, sessionIdentity))
                    } finally {
                        app.performanceMonitor.stop()
                    }
                    val elapsedMillis = (System.nanoTime() - startedAt) / 1_000_000.0
                    val trialRequests = requests.toList()
                    val uploadRequests = trialRequests.filter { it.path?.startsWith("/api/sync/uploads") == true }
                    val putRequests = uploadRequests.filter { it.method == "PUT" }
                    assertEquals(fileCount, initializedMediaCount(uploadRequests))
                    assertEquals(fileCount, putRequests.size)
                    assertEquals(fileCount, completedMediaCount(uploadRequests))
                    assertEquals(totalBytes.toLong(), putRequests.sumOf { it.bodySize })
                    assertTrue(
                        "The worker-count trial must not exceed its configured request concurrency",
                        maxOf(maxActiveInitRequests.get(), maxActiveChunkRequests.get(), maxActiveCompleteRequests.get()) <= workerCount,
                    )
                    assertTrue(
                        "Batched initialization remains serialized and bounded",
                        maxActiveInitRequests.get() <= 1,
                    )
                    if (workerCount > 1) {
                        assertTrue("Multiple workers should reduce init RPC count", initRpcCount(uploadRequests) < fileCount)
                    }
                    val completedJobs = dbHelper.getAllJobs(accountKey)
                        .filter { it.filename.startsWith("bench-worker-$workerCount-$trialIndex-") }
                    assertEquals(fileCount, completedJobs.size)
                    assertTrue(completedJobs.all { it.state.name == "READY" })
                    val mbps = totalBytes / (elapsedMillis / 1_000.0) / 1_000_000.0
                    results.getOrPut(workerCount) { mutableListOf() }.add(mbps)
                    val phaseMetrics = app.performanceMonitor.report.value.metrics.associateBy { it.name }
                    val phaseSummary = listOf(
                        "sync.upload_init.total",
                        "sync.upload_init_batch.total",
                        "sync.upload_chunk.total",
                        "sync.upload_chunk.body",
                        "sync.upload_chunk.ack_wait",
                        "sync.upload_complete.total",
                        "sync.upload_complete_batch.total",
                    ).joinToString(",") { metricName ->
                        val metric = phaseMetrics[metricName]
                        if (metric == null) "$metricName=none" else {
                            "$metricName(n=${metric.count},p50=${formatMs(metric.medianMs)},p90=${formatMs(metric.p90Ms)})"
                        }
                    }

                    Log.i(
                        "IrisUploadBench",
                        "IRIS_UPLOAD_WORKER_SWEEP workers=$workerCount files=$fileCount bytes=$totalBytes " +
                        "requests=${uploadRequests.size}(init_items=$fileCount,init_batches=${initRpcCount(uploadRequests)}," +
                            "put=${putRequests.size},complete=$fileCount) " +
                        "enqueue_ms=${formatMs(enqueueElapsedMillis)} " +
                        "hash_metric=${hashMetric?.let { "n=${it.count},p50=${formatMs(it.medianMs)},p90=${formatMs(it.p90Ms)}" } ?: "none"} " +
                        "response_delay_ms=${simulatedResponseDelayMillis.get()} elapsed_ms=${formatMs(elapsedMillis)} " +
                            "MBps=${formatMs(mbps)} max_active_init=${maxActiveInitRequests.get()} " +
                            "max_active_put=${maxActiveChunkRequests.get()} phase_metrics=[$phaseSummary]",
                    )

                    repeat(trialRequests.size) {
                        assertTrue("MockWebServer should record every trial request", server.takeRequest(1, TimeUnit.SECONDS) != null)
                    }
                    requests.clear()
                    maxActiveInitRequests.set(0)
                    maxActiveChunkRequests.set(0)
                    maxActiveCompleteRequests.set(0)
                } finally {
                    files.forEach(File::delete)
                }
            }
        } finally {
            delayInitRequests.set(false)
            delayChunkRequests.set(false)
            delayCompleteRequests.set(false)
            app.performanceMonitor.stop()
            simulatedResponseDelayMillis.set(200L)
        }

        val summary = results.toSortedMap().entries.joinToString(" ") { (workers, rates) ->
            val median = rates.sorted()[rates.size / 2]
            "$workers-worker median=${formatMs(median)}MBps trials=${rates.map(::formatMs)}"
        }
        Log.i("IrisUploadBench", "IRIS_UPLOAD_WORKER_SWEEP_SUMMARY response_delay_ms=35 $summary")
    }

    @Test
    fun upload_init_coalesce_window_sweep_measures_throughput_and_first_request_delay() = runBlocking<Unit> {
        val origin = IrisApiClient.getOrigin(server.url("/").toString())
        app.credentialsStore.saveSession(
            deviceId = "coalesce-window-device",
            accessToken = "coalesce-window-token",
            refreshToken = "coalesce-window-refresh",
            expiresInSeconds = 3600,
            username = "coalesce-window-user",
            serverOrigin = origin,
            userId = 47,
        )
        val accountKey = app.credentialsStore.accountIdentity.value!!
        val sessionIdentity = app.credentialsStore.sessionIdentity.value!!
        val fileCount = 32
        val bytesPerFile = 1024 * 1024
        val totalBytes = fileCount * bytesPerFile
        val windowsMillis = listOf(0L, 2L, 4L, 8L, 16L)
        val sweepWindowsMillis = windowsMillis + windowsMillis.reversed()
        serverChunkSizeBytes = bytesPerFile.toLong()
        simulatedResponseDelayMillis.set(35L)
        delayInitRequests.set(true)
        delayChunkRequests.set(true)
        delayCompleteRequests.set(false)
        val results = mutableMapOf<Long, MutableList<Triple<Double, Double, String>>>()

        try {
            for ((trialIndex, windowMillis) in sweepWindowsMillis.withIndex()) {
                val files = (0 until fileCount).map { index ->
                    File(app.cacheDir, "bench-coalesce-$windowMillis-$index-${System.nanoTime()}.bin").also {
                        writeBenchmarkFile(it, bytesPerFile, seed = 470_000 + trialIndex * 100 + index)
                    }
                }
                val requestStart = requests.size
                firstInitRequestObserved = CompletableDeferred()
                val manager = SyncUploadManager(
                    contentResolver = app.contentResolver,
                    dbHelper = dbHelper,
                    performanceMonitor = app.performanceMonitor,
                    maxConcurrentUploads = SyncUploadManager.MAX_CONCURRENT_UPLOADS,
                    uploadInitCoalesceWindowMillis = windowMillis,
                    apiServiceProvider = { session -> app.apiClient.apiServiceForSession(session) },
                )
                try {
                    files.forEach { file ->
                        assertTrue(
                            "Every coalesce-window fixture should be durably queued",
                            manager.enqueueMedia(
                                accountKey = accountKey,
                                uri = Uri.fromFile(file),
                                filename = file.name,
                                size = file.length(),
                                capturedAtIso = "2026-09-24T00:00:00Z",
                            ) > 0L,
                        )
                    }

                    val startedAt = System.nanoTime()
                    val work = async(Dispatchers.IO) { manager.processQueue(accountKey, sessionIdentity) }
                    firstInitRequestObserved.await()
                    val firstInitElapsedMillis = (System.nanoTime() - startedAt) / 1_000_000.0
                    assertTrue("The coalesce-window queue should complete", work.await())
                    val elapsedMillis = (System.nanoTime() - startedAt) / 1_000_000.0
                    val uploadRequests = requests.drop(requestStart)
                        .filter { it.path?.startsWith("/api/sync/uploads") == true }
                    val batchSizes = initBatchSizes(uploadRequests)
                    val putRequests = uploadRequests.filter { it.method == "PUT" }
                    assertEquals(fileCount, initializedMediaCount(uploadRequests))
                    assertEquals(fileCount, putRequests.size)
                    assertEquals(totalBytes.toLong(), putRequests.sumOf { it.bodySize })
                    assertEquals(fileCount, completedMediaCount(uploadRequests))
                    assertTrue("All jobs from the coalesce-window trial should complete", dbHelper.getAllJobs(accountKey)
                        .filter { it.filename.startsWith("bench-coalesce-$windowMillis-") }
                        .all { it.state.name == "READY" })
                    val mbps = totalBytes / (elapsedMillis / 1_000.0) / 1_000_000.0
                    results.getOrPut(windowMillis) { mutableListOf() } += Triple(
                        firstInitElapsedMillis,
                        mbps,
                        batchSizes.joinToString(","),
                    )
                    repeat(uploadRequests.size) {
                        assertTrue(
                            "MockWebServer should release each recorded payload before the next trial",
                            server.takeRequest(1, TimeUnit.SECONDS) != null,
                        )
                    }
                } finally {
                    files.forEach(File::delete)
                }
                requests.subList(requestStart, requests.size).clear()
                maxActiveInitRequests.set(0)
                maxActiveChunkRequests.set(0)
                maxActiveCompleteRequests.set(0)
            }
        } finally {
            delayInitRequests.set(false)
            delayChunkRequests.set(false)
            delayCompleteRequests.set(false)
            simulatedResponseDelayMillis.set(200L)
        }

        val summary = windowsMillis.joinToString(" | ") { windowMillis ->
            val trials = results.getValue(windowMillis)
            val medianFirstInit = trials.map { it.first }.sorted()[trials.size / 2]
            val medianMbps = trials.map { it.second }.sorted()[trials.size / 2]
            "window=${windowMillis}ms first_init_median_ms=${formatMs(medianFirstInit)} " +
                "MBps_median=${formatMs(medianMbps)} trials=${trials.map { formatMs(it.second) }} " +
                "batch_sizes=${trials.joinToString("/") { it.third }}"
        }
        Log.i("IrisUploadBench", "IRIS_UPLOAD_COALESCE_WINDOW_SWEEP files=$fileCount bytes=$totalBytes $summary")
    }

    @Test
    fun production_default_upload_workers_meet_mobile_uplink_target_under_controlled_rtt() = runBlocking {
        val origin = IrisApiClient.getOrigin(server.url("/").toString())
        app.credentialsStore.saveSession(
            deviceId = "default-throughput-device",
            accessToken = "default-throughput-token",
            refreshToken = "default-throughput-refresh",
            expiresInSeconds = 3600,
            username = "default-throughput-user",
            serverOrigin = origin,
            userId = 46,
        )
        val accountKey = app.credentialsStore.accountIdentity.value!!
        val sessionIdentity = app.credentialsStore.sessionIdentity.value!!
        val fileCount = 32
        val bytesPerFile = 1024 * 1024
        val totalBytes = fileCount * bytesPerFile
        val trialRates = mutableListOf<Double>()
        val files = mutableListOf<File>()
        serverChunkSizeBytes = bytesPerFile.toLong()
        simulatedResponseDelayMillis.set(35L)
        delayInitRequests.set(true)
        delayChunkRequests.set(true)
        delayCompleteRequests.set(false)

        try {
            repeat(3) { trialIndex ->
                val trialFiles = (0 until fileCount).map { index ->
                    File(app.cacheDir, "default-upload-bench-$trialIndex-$index-${System.nanoTime()}.bin").also {
                        writeBenchmarkFile(it, bytesPerFile, seed = 460_000 + trialIndex * 100 + index)
                    }
                }
                files += trialFiles
                val manager = SyncUploadManager(
                    contentResolver = app.contentResolver,
                    dbHelper = dbHelper,
                    performanceMonitor = app.performanceMonitor,
                    apiServiceProvider = { session -> app.apiClient.apiServiceForSession(session) },
                )
                trialFiles.forEach { file ->
                    assertTrue(
                        "Every default-throughput fixture should be durably queued",
                        manager.enqueueMedia(
                            accountKey = accountKey,
                            uri = Uri.fromFile(file),
                            filename = file.name,
                            size = file.length(),
                            capturedAtIso = "2026-09-24T00:00:00Z",
                        ) > 0L,
                    )
                }

                val requestStart = requests.size
                val startedAt = System.nanoTime()
                assertTrue(manager.processQueue(accountKey, sessionIdentity))
                val elapsedMillis = (System.nanoTime() - startedAt) / 1_000_000.0
                val uploadRequests = requests.drop(requestStart)
                    .filter { it.path?.startsWith("/api/sync/uploads") == true }
                val putRequests = uploadRequests.filter { it.method == "PUT" }
                assertEquals(fileCount, initializedMediaCount(uploadRequests))
                assertEquals(fileCount, putRequests.size)
                assertEquals(fileCount, completedMediaCount(uploadRequests))
                assertEquals(totalBytes.toLong(), putRequests.sumOf { it.bodySize })
                val completedJobs = dbHelper.getAllJobs(accountKey)
                    .filter { it.filename.startsWith("default-upload-bench-$trialIndex-") }
                assertEquals(fileCount, completedJobs.size)
                assertTrue(completedJobs.all { it.state.name == "READY" })

                val mbps = totalBytes / (elapsedMillis / 1_000.0) / 1_000_000.0
                trialRates += mbps
                assertTrue("The batcher should reduce initialization round trips", initRpcCount(uploadRequests) < fileCount)
                assertEquals("Initialization batch requests must be serialized", 1, maxActiveInitRequests.get())
                Log.i(
                    "IrisUploadBench",
                    "IRIS_UPLOAD_DEFAULT_ACCEPTANCE trial=${trialIndex + 1} files=$fileCount bytes=$totalBytes " +
                        "elapsed_ms=${formatMs(elapsedMillis)} MBps=${formatMs(mbps)} " +
                        "max_active_init=${maxActiveInitRequests.get()} max_active_put=${maxActiveChunkRequests.get()}",
                )
                repeat(uploadRequests.size) {
                    assertTrue("MockWebServer should release each recorded payload", server.takeRequest(1, TimeUnit.SECONDS) != null)
                }
                requests.subList(requestStart, requests.size).clear()
                maxActiveInitRequests.set(0)
                maxActiveChunkRequests.set(0)
                maxActiveCompleteRequests.set(0)
            }

            val medianMbps = trialRates.sorted()[trialRates.size / 2]
            assertTrue(
                "The production-default upload pool must reach a median of at least 45 MB/s " +
                    "on the controlled 35 ms RTT model; trials=${trialRates.map(::formatMs)} MB/s",
                medianMbps >= 45.0,
            )
        } finally {
            delayInitRequests.set(false)
            delayChunkRequests.set(false)
            delayCompleteRequests.set(false)
            simulatedResponseDelayMillis.set(200L)
            files.forEach(File::delete)
        }
    }

    @Test
    fun upload_goodput_respects_a_shared_50mbps_payload_budget_for_large_and_small_files() = runBlocking {
        val origin = IrisApiClient.getOrigin(server.url("/").toString())
        app.credentialsStore.saveSession(
            deviceId = "shaped-throughput-device",
            accessToken = "shaped-throughput-token",
            refreshToken = "shaped-throughput-refresh",
            expiresInSeconds = 3600,
            username = "shaped-throughput-user",
            serverOrigin = origin,
            userId = 48,
        )
        val accountKey = app.credentialsStore.accountIdentity.value!!
        val sessionIdentity = app.credentialsStore.sessionIdentity.value!!
        val totalBytes = 32 * 1024 * 1024
        val layouts = listOf(
            Triple("one_32m", listOf(totalBytes), 8L),
            Triple("thirty_two_1m", List(32) { 1024 * 1024 }, 8L),
            Triple("one_twenty_eight_256k", List(128) { 256 * 1024 }, 8L),
            Triple("five_twelve_64k_window8", List(512) { 64 * 1024 }, 8L),
            // Diagnostic-only sweep: same data volume, longer coalesce windows.
            Triple("five_twelve_64k_window32", List(512) { 64 * 1024 }, 32L),
            Triple("five_twelve_64k_window64", List(512) { 64 * 1024 }, 64L),
        )
        val measuredRates = linkedMapOf<String, Double>()
        val allFiles = mutableListOf<File>()
        serverChunkSizeBytes = totalBytes.toLong()
        delayInitRequests.set(false)
        delayChunkRequests.set(false)
        delayCompleteRequests.set(false)

        try {
            for ((layout, fileSizes, initWindowMillis) in layouts) {
                val limiter = SharedPayloadRateLimiter(bytesPerSecond = 50_000_000L)
                val mediaFiles = fileSizes.mapIndexed { index, size ->
                    File(app.cacheDir, "shaped-$layout-$index-${System.nanoTime()}.bin").also { file ->
                        writeBenchmarkFile(file, size, seed = layout.hashCode() + index)
                    }
                }
                allFiles += mediaFiles
                val requestStart = requests.size
                val manager = SyncUploadManager(
                    contentResolver = app.contentResolver,
                    dbHelper = dbHelper,
                    performanceMonitor = app.performanceMonitor,
                    uploadInitCoalesceWindowMillis = initWindowMillis,
                    apiServiceProvider = { session ->
                        val api = app.apiClient.apiServiceForSession(session)
                        object : IrisApiService by api {
                            override suspend fun uploadChunk(
                                uploadId: String,
                                offset: Long,
                                body: okhttp3.RequestBody,
                            ) = api.uploadChunk(
                                uploadId = uploadId,
                                offset = offset,
                                body = RateLimitedRequestBody(body, limiter),
                            )
                        }
                    },
                )

                mediaFiles.forEach { file ->
                    assertTrue(
                        "Every $layout fixture should be durably queued",
                        manager.enqueueMedia(
                            accountKey = accountKey,
                            uri = Uri.fromFile(file),
                            filename = file.name,
                            size = file.length(),
                            capturedAtIso = "2026-09-25T00:00:00Z",
                        ) > 0L,
                    )
                }

                app.performanceMonitor.start()
                val startedAt = System.nanoTime()
                assertTrue("Every $layout upload should complete", manager.processQueue(accountKey, sessionIdentity))
                val elapsedMillis = (System.nanoTime() - startedAt) / 1_000_000.0
                app.performanceMonitor.stop()

                val uploadRequests = requests.drop(requestStart)
                    .filter { it.path?.startsWith("/api/sync/uploads") == true }
                val putRequests = uploadRequests.filter { it.method == "PUT" }
                assertEquals(fileSizes.size, putRequests.size)
                assertEquals(totalBytes.toLong(), putRequests.sumOf { it.bodySize })
                if (fileSizes.size > 1) {
                    assertTrue(
                        "$layout should finalize items in fewer control-plane calls",
                        completionRpcCount(uploadRequests) < fileSizes.size,
                    )
                }
                val completedJobs = dbHelper.getAllJobs(accountKey)
                    .filter { it.filename.startsWith("shaped-$layout-") }
                assertEquals(fileSizes.size, completedJobs.size)
                assertTrue(completedJobs.all { it.state == UploadJobState.READY })

                val rateMbps = totalBytes / (elapsedMillis / 1_000.0) / 1_000_000.0
                measuredRates[layout] = rateMbps
                val phaseMetrics = app.performanceMonitor.report.value.metrics
                    .filter { it.name in setOf(
                        "sync.upload_init_batch.total",
                        "sync.upload_chunk.total",
                        "sync.upload_chunk.body",
                        "sync.upload_chunk.ack_wait",
                        "sync.upload_complete.total",
                        "sync.upload_complete_batch.total",
                    ) }
                    .joinToString(",") { metric ->
                        "${metric.name}(n=${metric.count},p50=${formatMs(metric.medianMs)},p90=${formatMs(metric.p90Ms)})"
                    }
                Log.i(
                    "IrisUploadBench",
                    "IRIS_UPLOAD_SHAPED layout=$layout files=${fileSizes.size} bytes=$totalBytes " +
                        "elapsed_ms=${formatMs(elapsedMillis)} MBps=${formatMs(rateMbps)} " +
                        "payload_cap_MBps=50 active_calls=${app.performanceMonitor.report.value.upload?.activeMibPerSecond?.let { formatMs(it * 1.048576) }} " +
                        "phases=[$phaseMetrics]",
                )
                repeat(uploadRequests.size) {
                    assertTrue("MockWebServer should release each shaped upload", server.takeRequest(1, TimeUnit.SECONDS) != null)
                }
                requests.subList(requestStart, requests.size).clear()
            }

            measuredRates.forEach { (layout, rate) ->
                assertTrue("$layout exceeded the shared 50 MB/s payload budget: $rate MB/s", rate <= 55.0)
            }
        } finally {
            app.performanceMonitor.stop()
            allFiles.forEach(File::delete)
        }
    }

    @Test
    fun isolated_real_server_upload_benchmark_measures_end_to_end_photo_video_payload_path() = runBlocking<Unit> {
        val args = InstrumentationRegistry.getArguments()
        val baseUrl = args.getString("irisBenchBaseUrl")?.takeIf(String::isNotBlank)
        val username = args.getString("irisBenchUsername")?.takeIf(String::isNotBlank)
        val password = args.getString("irisBenchPassword")?.takeIf(String::isNotBlank)
        org.junit.Assume.assumeTrue("Only run when an isolated local Iris server is supplied", baseUrl != null)
        org.junit.Assume.assumeTrue("An isolated benchmark account is required", username != null && password != null)
        val verifiedBaseUrl = baseUrl!!
        val benchmarkUrl = Uri.parse(verifiedBaseUrl)
        check(benchmarkUrl.scheme == "http" && benchmarkUrl.host in setOf("127.0.0.1", "localhost", "10.0.2.2")) {
            "Refusing to send benchmark media anywhere except HTTP loopback or the Android emulator host bridge"
        }

        val realMonitor = com.iris.app.performance.PerformanceMonitor()
        val origin = IrisApiClient.getOrigin(verifiedBaseUrl)
        val credentials = object : DeviceAuthStore {
            private var accessToken: String? = null
            private var refreshToken: String? = null
            private var deviceId: String? = null
            private var sessionIdentity: String? = null
            private var serverOrigin: String? = null

            override fun getAccessToken() = accessToken
            override fun getRefreshToken() = refreshToken
            override fun getDeviceId() = deviceId
            override fun getSessionIdentity() = sessionIdentity
            override fun getServerOrigin() = serverOrigin

            fun saveSession(access: String, refresh: String, id: String, identity: String) {
                accessToken = access
                refreshToken = refresh
                deviceId = id
                sessionIdentity = identity
                serverOrigin = origin
            }

            override fun replaceTokensAtomically(accessToken: String, refreshToken: String, expiresInSeconds: Long) {
                this.accessToken = accessToken
                this.refreshToken = refreshToken
            }

            override fun clearCredentials() {
                accessToken = null
                refreshToken = null
                deviceId = null
                sessionIdentity = null
                serverOrigin = null
            }
        }
        val apiClient = IrisApiClient(baseUrl, credentials, realMonitor)
        val login = apiClient.apiService.deviceLogin(
            username = username!!,
            password = password!!,
            deviceName = "isolated Android throughput benchmark",
        )
        val identity = "${origin}|user:${login.user?.id ?: error("Server login response lacks user id")}|${login.deviceId}"
        credentials.saveSession(login.accessToken, login.refreshToken, login.deviceId, identity)
        val api = apiClient.apiServiceForSession(identity)
        // The same path without the sync pipeline: the ceiling the runs below are compared with.
        val ceiling = com.iris.app.data.sync.ServerSpeedTest().run(api)
        Log.i(
            "IrisUploadBench",
            "IRIS_SPEEDTEST " + ceiling.joinToString(" ") { result ->
                "${result.phase}=${formatMs(result.bytesPerSecond / 1_000_000.0)}MBps"
            },
        )
        val fileCount = 16
        val photoBytes = 1024 * 1024
        val videoBytes = 36 * 1024 * 1024
        val totalBytes = (fileCount / 4) * videoBytes + (fileCount - fileCount / 4) * photoBytes
        val samples = mutableMapOf<Int, Double>()

        for (workerCount in listOf(4, 8, 12, 16)) {
            val mediaItems = (0 until fileCount).map { index ->
                val isVideo = index % 4 == 0
                val extension = if (isVideo) "mp4" else "jpg"
                val filename = "real-server-$workerCount-$index-${System.nanoTime()}.$extension"
                createMediaStoreBenchmarkItem(
                    resolver = app.contentResolver,
                    filename = filename,
                    isVideo = isVideo,
                    sizeBytes = if (isVideo) videoBytes else photoBytes,
                    seed = System.nanoTime() xor filename.hashCode().toLong(),
                )
            }
            val manager = SyncUploadManager(
                contentResolver = app.contentResolver,
                dbHelper = dbHelper,
                performanceMonitor = realMonitor,
                maxConcurrentUploads = workerCount,
                apiServiceProvider = { api },
            )
            try {
                val enqueueStarted = System.nanoTime()
                mediaItems.forEach { item ->
                    assertTrue(
                        "Real-server benchmark media should be durably queued",
                        manager.enqueueMedia(
                            accountKey = identity,
                            uri = item.uri,
                            filename = item.filename,
                            size = item.sizeBytes,
                            capturedAtIso = "2026-09-24T00:00:00Z",
                        ) > 0L,
                    )
                }
                val enqueueMillis = (System.nanoTime() - enqueueStarted) / 1_000_000.0
                realMonitor.start()
                val transferStartedAtEpochMs = System.currentTimeMillis()
                val transferStarted = System.nanoTime()
                assertTrue(manager.processQueue(identity, identity))
                val transferMillis = (System.nanoTime() - transferStarted) / 1_000_000.0
                val transferEndedAtEpochMs = System.currentTimeMillis()
                realMonitor.stop()

                val completed = dbHelper.getAllJobs(identity)
                    .filter { it.filename.startsWith("real-server-$workerCount-") }
                assertEquals(fileCount, completed.size)
                assertTrue("All real-server uploads should complete", completed.all { it.state.name == "READY" })
                val mbps = totalBytes / (transferMillis / 1_000.0) / 1_000_000.0
                samples[workerCount] = mbps
                val activeUpload = realMonitor.report.value.upload
                val phaseSummary = realMonitor.report.value.metrics
                    .filter { it.name in setOf(
                        "sync.upload_init.total",
                        "sync.upload_init_batch.total",
                        "sync.upload_chunk.total",
                        "sync.upload_chunk.body",
                        "sync.upload_chunk.ack_wait",
                        "sync.upload_complete.total",
                        "sync.upload_complete_batch.total",
                    ) }
                    .joinToString(",") { metric ->
                        "${metric.name}(n=${metric.count},p50=${formatMs(metric.medianMs)},p90=${formatMs(metric.p90Ms)})"
                    }
                Log.i(
                    "IrisUploadBench",
                    "IRIS_UPLOAD_REAL_SERVER workers=$workerCount photos=${fileCount * 3 / 4} videos=${fileCount / 4} " +
                        "bytes=$totalBytes enqueue_hash_ms=${formatMs(enqueueMillis)} " +
                        "transfer_start_epoch_ms=$transferStartedAtEpochMs transfer_end_epoch_ms=$transferEndedAtEpochMs " +
                        "transfer_ms=${formatMs(transferMillis)} MBps=${formatMs(mbps)} " +
                        "active_MBps=${formatMs((activeUpload?.activeMibPerSecond ?: 0.0) * 1.048576)} " +
                        "phases=[$phaseSummary] " +
                        "server=${origin} ai_expected=off",
                )
            } finally {
                realMonitor.stop()
                mediaItems.forEach { item -> app.contentResolver.delete(item.uri, null, null) }
            }
        }

        Log.i(
            "IrisUploadBench",
            "IRIS_UPLOAD_REAL_SERVER_SUMMARY " + samples.toSortedMap().entries.joinToString(" ") { (workers, mbps) ->
                "$workers-worker=${formatMs(mbps)}MBps"
            },
        )
    }

    @Test
    fun deleted_local_media_is_failed_and_does_not_stall_the_queue() = runBlocking {
        val origin = IrisApiClient.getOrigin(server.url("/").toString())
        app.credentialsStore.saveSession(
            deviceId = "deleted-media-device",
            accessToken = "deleted-media-token",
            refreshToken = "deleted-media-refresh",
            expiresInSeconds = 3600,
            username = "deleted-media-user",
            serverOrigin = origin,
            userId = 41
        )
        val accountKey = app.credentialsStore.accountIdentity.value!!
        val sessionIdentity = app.credentialsStore.sessionIdentity.value!!
        val manager = SyncUploadManager(app.contentResolver, dbHelper) { session ->
            app.apiClient.apiServiceForSession(session)
        }
        val stamp = System.nanoTime()
        val deleted = createMediaStoreBenchmarkItem(app.contentResolver, "deleted-$stamp.jpg", false, 96 * 1024, stamp)
        val kept = createMediaStoreBenchmarkItem(app.contentResolver, "kept-$stamp.jpg", false, 96 * 1024, stamp + 1)
        try {
            // The deleted item is queued first, so it is the first one claimed.
            listOf(deleted, kept).forEach { item ->
                assertTrue(manager.enqueueMedia(accountKey, item.uri, item.filename, item.sizeBytes, "2026-09-30T00:00:00Z") > 0L)
            }
            app.contentResolver.delete(deleted.uri, null, null)

            assertTrue("A deleted photo must not stall the queue", manager.processQueue(accountKey, sessionIdentity))

            val jobs = dbHelper.getAllJobs(accountKey).associateBy { it.filename }
            assertEquals("FAILED", jobs.getValue(deleted.filename).state.name)
            assertEquals(ResumableUploadTransfer.SOURCE_MISSING_MESSAGE, jobs.getValue(deleted.filename).errorMessage)
            assertEquals("READY", jobs.getValue(kept.filename).state.name)
            // Nothing is left to retry on the next run either.
            assertTrue(manager.processQueue(accountKey, sessionIdentity))
        } finally {
            app.contentResolver.delete(kept.uri, null, null)
        }
    }

    @Test
    fun rejected_item_is_failed_and_does_not_stall_the_queue() = runBlocking {
        val (accountKey, sessionIdentity, manager) = signedInManager("rejected")
        val stamp = System.nanoTime()
        val rejected = createMediaStoreBenchmarkItem(app.contentResolver, "rejected-$stamp.jpg", false, 96 * 1024, stamp)
        val kept = createMediaStoreBenchmarkItem(app.contentResolver, "kept-$stamp.jpg", false, 96 * 1024, stamp + 1)
        rejectChunksForFilenames += rejected.filename
        try {
            listOf(rejected, kept).forEach { item ->
                assertTrue(manager.enqueueMedia(accountKey, item.uri, item.filename, item.sizeBytes, "2026-10-02T00:00:00Z") > 0L)
            }

            assertTrue("A rejected item must not stall the queue", manager.processQueue(accountKey, sessionIdentity))

            val jobs = dbHelper.getAllJobs(accountKey).associateBy { it.filename }
            assertEquals("FAILED", jobs.getValue(rejected.filename).state.name)
            assertEquals("READY", jobs.getValue(kept.filename).state.name)
        } finally {
            listOf(rejected, kept).forEach { app.contentResolver.delete(it.uri, null, null) }
        }
    }

    @Test
    fun one_transient_failure_defers_that_item_and_the_rest_keep_uploading() = runBlocking {
        val (accountKey, sessionIdentity, manager) = signedInManager("transient")
        val stamp = System.nanoTime()
        val flaky = createMediaStoreBenchmarkItem(app.contentResolver, "flaky-$stamp.jpg", false, 96 * 1024, stamp)
        val others = (1..4).map { index ->
            createMediaStoreBenchmarkItem(app.contentResolver, "steady-$index-$stamp.jpg", false, 96 * 1024, stamp + index)
        }
        loseUploadOnceForFilenames += flaky.filename
        try {
            (listOf(flaky) + others).forEach { item ->
                assertTrue(manager.enqueueMedia(accountKey, item.uri, item.filename, item.sizeBytes, "2026-10-02T00:00:00Z") > 0L)
            }

            // The pass reports the deferred item so WorkManager retries it...
            assertFalse(manager.processQueue(accountKey, sessionIdentity))
            var jobs = dbHelper.getAllJobs(accountKey).associateBy { it.filename }
            // ...but it did not stop the others.
            others.forEach { assertEquals("READY", jobs.getValue(it.filename).state.name) }
            assertEquals("QUEUED", jobs.getValue(flaky.filename).state.name)

            assertTrue(manager.processQueue(accountKey, sessionIdentity))
            jobs = dbHelper.getAllJobs(accountKey).associateBy { it.filename }
            assertEquals("READY", jobs.getValue(flaky.filename).state.name)
        } finally {
            (listOf(flaky) + others).forEach { app.contentResolver.delete(it.uri, null, null) }
        }
    }

    @Test
    fun server_unavailable_while_sending_a_chunk_is_retried_not_failed() = runBlocking {
        val (accountKey, sessionIdentity, manager) = signedInManager("unavailable")
        val stamp = System.nanoTime()
        val item = createMediaStoreBenchmarkItem(app.contentResolver, "unavailable-$stamp.jpg", false, 96 * 1024, stamp)
        unavailableOnceForFilenames += item.filename
        try {
            assertTrue(manager.enqueueMedia(accountKey, item.uri, item.filename, item.sizeBytes, "2026-10-02T00:00:00Z") > 0L)

            assertFalse(manager.processQueue(accountKey, sessionIdentity))
            assertEquals("QUEUED", dbHelper.getAllJobs(accountKey).single { it.filename == item.filename }.state.name)

            assertTrue(manager.processQueue(accountKey, sessionIdentity))
            assertEquals("READY", dbHelper.getAllJobs(accountKey).single { it.filename == item.filename }.state.name)
        } finally {
            app.contentResolver.delete(item.uri, null, null)
        }
    }

    @Test
    fun a_pass_can_set_aside_more_items_than_sqlite_bound_variables() = runBlocking {
        // Older SQLite versions allow 999 bound variables per statement. Setting
        // items aside must not grow the claim query with the number of failures.
        val accountKey = "server|user:77"
        val count = 1_200
        repeat(count) { index ->
            assertTrue(dbHelper.insertOrIgnoreJob(
                accountKey = accountKey,
                localUri = "content://media/external/images/media/${10_000 + index}",
                filename = "deferred-$index.jpg",
                byteSize = 20L,
                sha256 = index.toString().padStart(64, '0'),
                capturedAt = "2026-10-02T00:00:00Z",
            ) > 0L)
        }

        var lastClaimedId = 0L
        var claimed = 0
        while (true) {
            val job = dbHelper.claimNextPendingJob(accountKey, lastClaimedId) ?: break
            assertTrue("Claims advance in id order", job.id > lastClaimedId)
            lastClaimedId = job.id
            // Every item fails transiently and goes back to the queue.
            dbHelper.updateJobState(accountKey, job.id, UploadJobState.QUEUED)
            claimed++
        }

        assertEquals(count, claimed)
        // The next pass starts over and finds them all again.
        assertNotNull(dbHelper.claimNextPendingJob(accountKey))
    }

    @Test
    fun an_upload_declares_the_real_file_size_when_media_store_trails_it() = runBlocking {
        val (accountKey, sessionIdentity, manager) = signedInManager("stale-size")
        val stamp = System.nanoTime()
        val item = createMediaStoreBenchmarkItem(app.contentResolver, "stale-size-$stamp.jpg", false, 96 * 1024, stamp)
        try {
            assertTrue(manager.enqueueMedia(accountKey, item.uri, item.filename, item.sizeBytes, "2026-10-02T00:00:00Z") > 0L)
            // What a file rewritten without MediaStore noticing looks like to the queue:
            // the size recorded at scan time is smaller than the file now.
            val job = dbHelper.getAllJobs(accountKey).single { it.filename == item.filename }
            dbHelper.writableDatabase.execSQL("UPDATE upload_jobs SET byte_size = ? WHERE id = ?", arrayOf<Any>(item.sizeBytes - 170, job.id))
            requests.clear()

            assertTrue(manager.processQueue(accountKey, sessionIdentity))

            val declared = requests.filter { it.path == "/api/sync/uploads/batch" }
                .flatMap { request ->
                    val uploads = JSONObject(request.body.clone().readUtf8()).getJSONArray("uploads")
                    (0 until uploads.length()).map { uploads.getJSONObject(it) }
                }
                .single { it.getString("filename") == item.filename }
            assertEquals(item.sizeBytes, declared.getLong("size"))
            val sent = requests.filter { it.method == "PUT" }.sumOf { it.bodySize }
            assertEquals(item.sizeBytes, sent)
            assertEquals("READY", dbHelper.getAllJobs(accountKey).single { it.filename == item.filename }.state.name)
        } finally {
            app.contentResolver.delete(item.uri, null, null)
        }
    }

    @Test
    fun a_hash_mismatch_for_a_rewritten_file_is_hashed_again_and_resent() = runBlocking {
        val (accountKey, sessionIdentity, manager) = signedInManager("mismatch")
        val stamp = System.nanoTime()
        val item = createMediaStoreBenchmarkItem(app.contentResolver, "mismatch-$stamp.jpg", false, 96 * 1024, stamp)
        try {
            assertTrue(manager.enqueueMedia(accountKey, item.uri, item.filename, item.sizeBytes, "2026-10-02T00:00:00Z") > 0L)
            val originalHash = dbHelper.getAllJobs(accountKey).single { it.filename == item.filename }.sha256
            // The file is rewritten after it was hashed; the server sees other bytes.
            app.contentResolver.openOutputStream(item.uri, "wt")!!.use { it.write(ByteArray(96 * 1024) { 7 }) }
            hashMismatchOnceForFilenames += item.filename

            assertFalse(manager.processQueue(accountKey, sessionIdentity))
            val retried = dbHelper.getAllJobs(accountKey).single { it.filename == item.filename }
            assertEquals("QUEUED", retried.state.name)
            assertNotEquals(originalHash, retried.sha256)

            assertTrue(manager.processQueue(accountKey, sessionIdentity))
            assertEquals("READY", dbHelper.getAllJobs(accountKey).single { it.filename == item.filename }.state.name)
        } finally {
            app.contentResolver.delete(item.uri, null, null)
        }
    }

    @Test
    fun granting_location_access_after_hashing_hashes_again_before_sending() = runBlocking {
        // MediaStore returns a photo without its location unless the app holds
        // ACCESS_MEDIA_LOCATION when it reads, through any URI. A photo hashed
        // before the permission was granted then reads as other bytes of the
        // same size, and every photo with a location was refused once.
        var locationGranted = false
        val origin = IrisApiClient.getOrigin(server.url("/").toString())
        app.credentialsStore.saveSession(
            deviceId = "location-device", accessToken = "location-token",
            refreshToken = "location-refresh", expiresInSeconds = 3600,
            username = "location-user", serverOrigin = origin, userId = 43,
        )
        val manager = SyncUploadManager(app.contentResolver, dbHelper, canReadOriginals = { locationGranted }) { session ->
            app.apiClient.apiServiceForSession(session)
        }
        val accountKey = app.credentialsStore.accountIdentity.value!!
        val sessionIdentity = app.credentialsStore.sessionIdentity.value!!
        val stamp = System.nanoTime()
        val item = createMediaStoreBenchmarkItem(app.contentResolver, "location-$stamp.jpg", false, 96 * 1024, stamp)
        try {
            assertTrue(manager.enqueueMedia(accountKey, item.uri, item.filename, item.sizeBytes, "2026-10-02T00:00:00Z") > 0L)
            // Granted after the scan: the same URI now returns the unredacted
            // photo, same size, different bytes.
            locationGranted = true
            app.contentResolver.openOutputStream(item.uri, "wt")!!.use { it.write(ByteArray(item.sizeBytes.toInt()) { 9 }) }
            requests.clear()

            assertTrue(manager.processQueue(accountKey, sessionIdentity))

            val declared = requests.filter { it.path == "/api/sync/uploads/batch" }
                .flatMap { request ->
                    val uploads = JSONObject(request.body.clone().readUtf8()).getJSONArray("uploads")
                    (0 until uploads.length()).map { uploads.getJSONObject(it) }
                }
                .single { it.getString("filename") == item.filename }
            val uploadId = dbHelper.getAllJobs(accountKey).single { it.filename == item.filename }.uploadId!!
            // Every chunk of this upload, in order; the file is sent once, not
            // refused and sent again.
            val sent = requests.filter { it.method == "PUT" && it.path!!.contains(uploadId) }
            val bytes = sent.fold(ByteArray(0)) { all, request -> all + request.body.clone().readByteArray() }
            assertEquals("The photo is sent once, not refused and sent again", item.sizeBytes, bytes.size.toLong())
            val digest = java.security.MessageDigest.getInstance("SHA-256").digest(bytes)
                .joinToString("") { "%02x".format(it) }
            assertEquals("The declared hash must describe the bytes sent", declared.getString("sha256"), digest)
            assertEquals("READY", dbHelper.getAllJobs(accountKey).single { it.filename == item.filename }.state.name)
        } finally {
            app.contentResolver.delete(item.uri, null, null)
        }
    }

    @Test
    fun a_hash_mismatch_with_an_unchanged_file_fails_instead_of_looping() = runBlocking {
        val (accountKey, sessionIdentity, manager) = signedInManager("mismatch-same")
        val stamp = System.nanoTime()
        val item = createMediaStoreBenchmarkItem(app.contentResolver, "mismatch-same-$stamp.jpg", false, 96 * 1024, stamp)
        try {
            assertTrue(manager.enqueueMedia(accountKey, item.uri, item.filename, item.sizeBytes, "2026-10-02T00:00:00Z") > 0L)
            hashMismatchOnceForFilenames += item.filename

            assertTrue(manager.processQueue(accountKey, sessionIdentity))
            assertEquals("FAILED", dbHelper.getAllJobs(accountKey).single { it.filename == item.filename }.state.name)
        } finally {
            app.contentResolver.delete(item.uri, null, null)
        }
    }

    private fun signedInManager(name: String): Triple<String, String, SyncUploadManager> {
        val origin = IrisApiClient.getOrigin(server.url("/").toString())
        app.credentialsStore.saveSession(
            deviceId = "$name-device",
            accessToken = "$name-token",
            refreshToken = "$name-refresh",
            expiresInSeconds = 3600,
            username = "$name-user",
            serverOrigin = origin,
            userId = 43
        )
        val manager = SyncUploadManager(app.contentResolver, dbHelper) { session ->
            app.apiClient.apiServiceForSession(session)
        }
        return Triple(
            app.credentialsStore.accountIdentity.value!!,
            app.credentialsStore.sessionIdentity.value!!,
            manager,
        )
    }

    @Test
    fun completion_batches_distinct_hashes_and_serializes_duplicate_hashes() = runBlocking {
        val origin = IrisApiClient.getOrigin(server.url("/").toString())
        app.credentialsStore.saveSession(
            deviceId = "completion-device",
            accessToken = "completion-token",
            refreshToken = "completion-refresh",
            expiresInSeconds = 3600,
            username = "completion-user",
            serverOrigin = origin,
            userId = 32
        )
        val accountKey = app.credentialsStore.accountIdentity.value!!
        val sessionIdentity = app.credentialsStore.sessionIdentity.value!!
        val manager = SyncUploadManager(app.contentResolver, dbHelper) { session ->
            app.apiClient.apiServiceForSession(session)
        }

        suspend fun enqueueJobs(hashes: List<String>, prefix: String) {
            hashes.forEachIndexed { index, hash ->
                assertTrue(
                    "Each completion fixture should be queued",
                    dbHelper.insertOrIgnoreJob(
                        accountKey = accountKey,
                        localUri = "content://media/external/images/media/$prefix-$index",
                        filename = "$prefix-$index.jpg",
                        byteSize = 0,
                        sha256 = hash,
                        capturedAt = "2026-09-24T00:00:00Z"
                    ) > 0L
                )
            }
        }

        delayCompleteRequests.set(true)
        try {
            enqueueJobs((0 until 8).map { it.toString().padStart(64, 'a') }, "distinct")
            assertTrue(
                manager.processQueue(accountKey, sessionIdentity) {
                    app.credentialsStore.sessionIdentity.value == sessionIdentity
                }
            )
            assertTrue(
            "The complete-batch calls should include every distinct upload",
                completedMediaCount(requests.toList()) >= 8
            )
            assertTrue("Batching should reduce completion RPCs", completionRpcCount(requests.toList()) < 8)
            assertTrue(dbHelper.getAllJobs(accountKey).all { it.state.name == "READY" })

            maxActiveCompleteRequests.set(0)
            initSequence.set(0)
            val completionRpcsBeforeDuplicates = completionRpcCount(requests.toList())
            enqueueJobs(List(4) { "f".repeat(64) }, "duplicates")
            assertTrue(
                manager.processQueue(accountKey, sessionIdentity) {
                    app.credentialsStore.sessionIdentity.value == sessionIdentity
                }
            )
            assertTrue("Same-hash jobs should all finalize successfully", dbHelper.getAllJobs(accountKey).all { it.state.name == "READY" })
            assertEquals(
                "Same-hash uploads must retain serialized finalization",
                1,
                maxActiveCompleteRequests.get(),
            )
            assertEquals(
                "Same-hash finalization remains one-at-a-time, while distinct hashes are batched",
                4,
                completionRpcCount(requests.toList()) - completionRpcsBeforeDuplicates,
            )
        } finally {
            delayCompleteRequests.set(false)
        }
    }

    @Test
    fun upload_throughput_benchmark_reports_hash_and_transfer_cost_by_file_count() = runBlocking {
        val origin = IrisApiClient.getOrigin(server.url("/").toString())
        app.credentialsStore.saveSession(
            deviceId = "throughput-device",
            accessToken = "throughput-token",
            refreshToken = "throughput-refresh",
            expiresInSeconds = 3600,
            username = "throughput-user",
            serverOrigin = origin,
            userId = 41
        )
        val accountKey = app.credentialsStore.accountIdentity.value!!
        val sessionIdentity = app.credentialsStore.sessionIdentity.value!!
        val performance = app.performanceMonitor
        val manager = SyncUploadManager(app.contentResolver, dbHelper, performance) { session ->
            app.apiClient.apiServiceForSession(session)
        }
        val totalBytes = 32 * 1024 * 1024
        serverChunkSizeBytes = 32L * 1024 * 1024

        // Equal payload sizes isolate per-item control-plane overhead from byte
        // throughput. These files are local synthetic test fixtures only.
        val trials = listOf(
            "one_large" to (1 to totalBytes),
            "eight_medium" to (8 to totalBytes / 8),
            "thirty_two_small" to (32 to totalBytes / 32),
        )

        for ((label, shape) in trials) {
            val (fileCount, bytesPerFile) = shape
            val files = (0 until fileCount).map { index ->
                File(app.cacheDir, "bench-$label-$index-${System.nanoTime()}.bin").also {
                    // Make same-size fixtures have distinct hashes. Reusing
                    // identical bytes serializes finalization through the
                    // duplicate-content lock and biases the many-small-files
                    // result toward a case unlike an ordinary photo library.
                    writeBenchmarkFile(it, bytesPerFile, seed = label.hashCode() + index)
                }
            }
            performance.start()
            val startedAt = System.nanoTime()
            val firstClaimAt = AtomicLong(0L)
            var hashAndEnqueueMillis = 0.0
            var managerRunMillis = 0.0

            try {
                val result = SyncQueueCoordinator.scanAndDrain(
                    scanAndEnqueue = { signalQueue ->
                        files.forEach { file ->
                            val enqueueStartedAt = System.nanoTime()
                            val jobId = manager.enqueueMedia(
                                accountKey = accountKey,
                                uri = Uri.fromFile(file),
                                filename = file.name,
                                size = file.length(),
                                capturedAtIso = "2026-09-24T00:00:00Z",
                            )
                            hashAndEnqueueMillis += (System.nanoTime() - enqueueStartedAt) / 1_000_000.0
                            assertTrue("Benchmark fixture should be queued", jobId > 0L)
                            signalQueue()
                        }
                        files.size
                    },
                    drainQueue = { workSignal ->
                        val drainStartedAt = System.nanoTime()
                        val completed = manager.processQueue(
                            accountKey = accountKey,
                            sessionIdentity = sessionIdentity,
                            isSessionCurrent = {
                                app.credentialsStore.sessionIdentity.value == sessionIdentity
                            },
                            workSignal = workSignal,
                            onFirstUploadJobClaimed = {
                                val claimedAt = System.nanoTime()
                                if (firstClaimAt.compareAndSet(0L, claimedAt)) {
                                    performance.record(
                                        Metric.SyncFirstUploadJobStart,
                                        (claimedAt - startedAt) / 1_000_000.0,
                                    )
                                }
                            },
                        )
                        managerRunMillis += (System.nanoTime() - drainStartedAt) / 1_000_000.0
                        completed
                    },
                )
                val endToEndMillis = (System.nanoTime() - startedAt) / 1_000_000.0
                assertEquals(fileCount, result.scanResult)
                val jobsAfterDrain = dbHelper.getAllJobs(accountKey)
                    .filter { it.filename.startsWith("bench-$label-") }
                assertTrue(
                    "All benchmark uploads should finish: ${jobsAfterDrain
                        .map { it.filename to (it.state to it.errorMessage) }}; " +
                        "requests=${requests.map { it.path }}",
                    result.queueCompleted,
                )

                val trialRequests = requests.toList()
                val uploadRequests = trialRequests.filter {
                    it.path?.startsWith("/api/sync/uploads") == true
                }
                val initRequests = initializedMediaCount(uploadRequests)
                val initBatches = initRpcCount(uploadRequests)
                val putRequests = uploadRequests.filter { it.method == "PUT" }
                val completeRequests = completedMediaCount(uploadRequests)
                val transferredBytes = putRequests.sumOf { it.bodySize }
                assertEquals("Each media item should receive one upload reservation", fileCount, initRequests)
                assertEquals("Each fixture fits in one chunk", fileCount, putRequests.size)
                assertEquals("Each media item should finalize independently", fileCount, completeRequests)
                assertEquals(totalBytes.toLong(), transferredBytes)
                val completedJobs = dbHelper.getAllJobs(accountKey)
                    .filter { it.filename.startsWith("bench-$label-") }
                assertEquals(fileCount, completedJobs.size)
                assertTrue("All synthetic uploads should reach READY", completedJobs.all { it.state.name == "READY" })

                val firstClaimMillis = firstClaimAt.get().takeIf { it > 0L }
                    ?.let { (it - startedAt) / 1_000_000.0 } ?: 0.0
                val managerMbps = transferredBytes / (managerRunMillis / 1_000.0) / 1_000_000.0
                val pipelineMbps = transferredBytes / (endToEndMillis / 1_000.0) / 1_000_000.0
                performance.stop()
                val uploadSummary = performance.report.value.upload!!
                assertEquals("Metrics should count server-confirmed uploads", fileCount.toLong(), uploadSummary.confirmedItems)
                val metricByName = performance.report.value.metrics.associateBy { it.name }
                val phaseMetrics = listOf(
                    "sync.media_hash.total",
                    "sync.upload.first_job_start",
                    "sync.upload_queue.total",
                    "sync.upload_init.total",
                    "sync.upload_init_batch.total",
                    "sync.upload_chunk.total",
                    "sync.upload_chunk.body",
                    "sync.upload_chunk.ack_wait",
                    "sync.upload_complete.total",
                    "sync.upload_complete_batch.total",
                ).joinToString(",") { metricName ->
                    val metric = metricByName[metricName]
                    if (metric == null) {
                        "$metricName=none"
                    } else {
                        "$metricName(n=${metric.count},p50=${formatMs(metric.medianMs)},p90=${formatMs(metric.p90Ms)})"
                    }
                }
                assertEquals(
                    "Each PUT should report a media-body duration",
                    putRequests.size,
                    metricByName["sync.upload_chunk.body"]?.count,
                )
                assertEquals(
                    "The init-batch metric counts reservation RPCs",
                    initBatches,
                    metricByName["sync.upload_init_batch.total"]?.count,
                )
                assertEquals(
                    "Each PUT should report server/round-trip acknowledgement wait",
                    putRequests.size,
                    metricByName["sync.upload_chunk.ack_wait"]?.count,
                )
                Log.i(
                    "IrisUploadBench",
                    "IRIS_UPLOAD_BENCH layout=$label files=$fileCount bytes=$transferredBytes " +
                        "upload_requests=${uploadRequests.size}(init_items=$initRequests,init_batches=$initBatches," +
                            "put=${putRequests.size},complete=$completeRequests) " +
                        "all_requests=${trialRequests.size} hash_enqueue_ms=${formatMs(hashAndEnqueueMillis)} " +
                        "first_claim_ms=${formatMs(firstClaimMillis)} manager_ms=${formatMs(managerRunMillis)} " +
                        "end_to_end_ms=${formatMs(endToEndMillis)} manager_MBps=${formatMs(managerMbps)} " +
                        "pipeline_MBps=${formatMs(pipelineMbps)} confirmed_items_per_sec=${formatMs(uploadSummary.itemsPerSecond)} " +
                        "phases=[$phaseMetrics]"
                )

                // Drain MockWebServer's own request queue before the next equal-
                // sized trial so its recorded bodies do not accumulate in memory.
                repeat(trialRequests.size) {
                    assertTrue(
                        "MockWebServer should have recorded each request",
                        server.takeRequest(1, TimeUnit.SECONDS) != null,
                    )
                }
                requests.clear()
            } finally {
                performance.stop()
                files.forEach(File::delete)
            }
        }
    }

    @Test
    fun upload_manager_throughput_benchmark_with_prequeued_small_files() = runBlocking<Unit> {
        val origin = IrisApiClient.getOrigin(server.url("/").toString())
        app.credentialsStore.saveSession(
            deviceId = "prequeued-benchmark-device",
            accessToken = "prequeued-benchmark-token",
            refreshToken = "prequeued-benchmark-refresh",
            expiresInSeconds = 3600,
            username = "prequeued-benchmark-user",
            serverOrigin = origin,
            userId = 43
        )
        val accountKey = app.credentialsStore.accountIdentity.value!!
        val sessionIdentity = app.credentialsStore.sessionIdentity.value!!
        val fileCount = 32
        val bytesPerFile = 1 * 1024 * 1024
        val files = (0 until fileCount).map { index ->
            File(app.cacheDir, "bench-prequeued-$index-${System.nanoTime()}.bin").also {
                writeBenchmarkFile(it, bytesPerFile, seed = 500 + index)
            }
        }
        serverChunkSizeBytes = bytesPerFile.toLong()
        val manager = SyncUploadManager(app.contentResolver, dbHelper, app.performanceMonitor) { session ->
            app.apiClient.apiServiceForSession(session)
        }
        files.forEach { file ->
            assertTrue(
                "All media is hashed and durably queued before draining starts",
                manager.enqueueMedia(
                    accountKey = accountKey,
                    uri = Uri.fromFile(file),
                    filename = file.name,
                    size = file.length(),
                    capturedAtIso = "2026-09-24T00:00:00Z"
                ) > 0L
            )
        }
        app.performanceMonitor.start()
        val startedAt = System.nanoTime()
        try {
            val completed = manager.processQueue(accountKey, sessionIdentity)
            assertTrue(
                "Prequeued uploads failed: ${dbHelper.getAllJobs(accountKey).map { it.filename to (it.state to it.errorMessage) }}; " +
                    "requests=${requests.map { it.path }}",
                completed,
            )
        } finally {
            app.performanceMonitor.stop()
            files.forEach(File::delete)
        }
        val elapsedMillis = (System.nanoTime() - startedAt) / 1_000_000.0
        val uploadRequests = requests.filter { it.path?.startsWith("/api/sync/uploads") == true }
        val putRequests = uploadRequests.filter { it.method == "PUT" }
        assertEquals(fileCount, initializedMediaCount(uploadRequests))
        assertEquals(fileCount, putRequests.size)
        assertEquals(fileCount, completedMediaCount(uploadRequests))
        assertEquals((fileCount * bytesPerFile).toLong(), putRequests.sumOf { it.bodySize })
        assertTrue(dbHelper.getAllJobs(accountKey).all { it.state.name == "READY" })

        Log.i(
            "IrisUploadBench",
            "IRIS_UPLOAD_PREQUEUED_BENCH files=$fileCount bytes=${putRequests.sumOf { it.bodySize }} " +
                "upload_requests=${uploadRequests.size} elapsed_ms=${formatMs(elapsedMillis)} " +
                "manager_MBps=${formatMs(fileCount * bytesPerFile / (elapsedMillis / 1_000.0) / 1_000_000.0)}"
        )
    }

    @Test
    fun scan_keeps_upload_workers_available_as_small_batches_are_discovered() = runBlocking<Unit> {
        val origin = IrisApiClient.getOrigin(server.url("/").toString())
        app.credentialsStore.saveSession(
            deviceId = "streaming-queue-device",
            accessToken = "streaming-queue-token",
            refreshToken = "streaming-queue-refresh",
            expiresInSeconds = 3600,
            username = "streaming-queue-user",
            serverOrigin = origin,
            userId = 44
        )
        val accountKey = app.credentialsStore.accountIdentity.value!!
        val sessionIdentity = app.credentialsStore.sessionIdentity.value!!
        val fileCount = 8
        val bytesPerFile = 64 * 1024
        val files = (0 until fileCount).map { index ->
            File(app.cacheDir, "bench-streaming-$index-${System.nanoTime()}.bin").also {
                writeBenchmarkFile(it, bytesPerFile, seed = 700 + index)
            }
        }
        serverChunkSizeBytes = bytesPerFile.toLong()
        delayInitRequests.set(true)
        delayChunkRequests.set(true)
        delayCompleteRequests.set(true)
        val manager = SyncUploadManager(app.contentResolver, dbHelper) { session ->
            app.apiClient.apiServiceForSession(session)
        }

        try {
            val result = SyncQueueCoordinator.scanAndDrain(
                scanAndEnqueue = { signalQueue ->
                    val firstFile = files.first()
                    assertTrue(
                        manager.enqueueMedia(
                            accountKey = accountKey,
                            uri = Uri.fromFile(firstFile),
                            filename = firstFile.name,
                            size = firstFile.length(),
                            capturedAtIso = "2026-09-24T00:00:00Z",
                        ) > 0L
                    )
                    signalQueue()

                    // Hold discovery open while the first init RPC is in flight.
                    // The other upload workers should wait for more queue work,
                    // instead of exiting because the queue is temporarily empty.
                    firstInitRequestObserved.await()
                    delay(75L)
                    files.drop(1).forEach { file ->
                        assertTrue(
                            manager.enqueueMedia(
                                accountKey = accountKey,
                                uri = Uri.fromFile(file),
                                filename = file.name,
                                size = file.length(),
                                capturedAtIso = "2026-09-24T00:00:00Z",
                            ) > 0L
                        )
                        signalQueue()
                    }
                    fileCount
                },
                drainQueue = { workSignal ->
                    manager.processQueue(
                        accountKey = accountKey,
                        sessionIdentity = sessionIdentity,
                        isSessionCurrent = {
                            app.credentialsStore.sessionIdentity.value == sessionIdentity
                        },
                        workSignal = workSignal,
                    )
                },
            )

            assertEquals(fileCount, result.scanResult)
            assertTrue("Every media item should finish uploading", result.queueCompleted)
            assertTrue(
                "The waiting worker pool should batch newly discovered init requests",
                initRpcCount(requests.toList()) < fileCount,
            )
            assertTrue(
                "Init request concurrency must stay bounded by the upload pool",
                maxActiveInitRequests.get() <= SyncUploadManager.MAX_CONCURRENT_UPLOADS,
            )
            assertTrue(
                "The waiting worker pool should also upload chunks concurrently; " +
                    "observed max=${maxActiveChunkRequests.get()}",
                maxActiveChunkRequests.get() > 1,
            )
            assertTrue(maxActiveChunkRequests.get() <= SyncUploadManager.MAX_CONCURRENT_UPLOADS)
            val uploadRequests = requests.filter { it.path?.startsWith("/api/sync/uploads") == true }
            assertEquals(fileCount, initializedMediaCount(uploadRequests))
            assertEquals(fileCount, uploadRequests.count { it.method == "PUT" })
            assertEquals(fileCount, completedMediaCount(uploadRequests))
            val completedJobs = dbHelper.getAllJobs(accountKey)
            assertEquals(fileCount, completedJobs.size)
            assertTrue(completedJobs.all { it.state.name == "READY" })
            Log.i(
                "IrisUploadBench",
                "IRIS_UPLOAD_STREAMING_BENCH files=$fileCount bytes=${fileCount * bytesPerFile} " +
                    "upload_requests=${uploadRequests.size} init_batches=${initRpcCount(uploadRequests)} " +
                    "max_active_init=${maxActiveInitRequests.get()} " +
                    "max_active_chunk=${maxActiveChunkRequests.get()} " +
                    "max_active_complete=${maxActiveCompleteRequests.get()}"
            )
        } finally {
            files.forEach(File::delete)
        }
    }

    @Test
    fun legacy_unassigned_media_can_be_queued_again_for_the_authenticated_account() = runBlocking {
        val localUri = "content://media/external/images/media/legacy-requeue"
        dbHelper.writableDatabase.insertOrThrow("upload_jobs", null, ContentValues().apply {
            putNull("account_key")
            put("local_uri", localUri)
            put("filename", "legacy-photo.jpg")
            put("byte_size", 20L)
            put("sha256", "a".repeat(64))
            put("captured_at", "2026-09-23T00:00:00Z")
            put("upload_id", "legacy-upload-id")
            put("next_byte_offset", 5L)
            put("state", "QUEUED")
            put("updated_at", 1L)
        })

        val accountKey = "server|user:22"
        val newAccountJob = dbHelper.insertOrIgnoreJob(
            accountKey = accountKey,
            localUri = localUri,
            filename = "legacy-photo.jpg",
            byteSize = 20L,
            sha256 = "a".repeat(64),
            capturedAt = "2026-09-23T00:00:00Z"
        )

        assertTrue(
            "An account's policy-approved scan must be able to create its own job; " +
                "an unassigned legacy row must not reserve the media URI globally",
            newAccountJob > 0L
        )
        assertEquals(1, dbHelper.getAllJobs(accountKey).size)
        val freshAccountJob = dbHelper.getAllJobs(accountKey).single()
        assertEquals("A legacy upload session must never be resumed under a new account", null, freshAccountJob.uploadId)
        assertEquals(0L, freshAccountJob.nextByteOffset)
        assertEquals(0, dbHelper.countUnassignedPendingJobs())
    }

    @Test
    fun upgrading_legacy_queue_keeps_jobs_unassigned_and_resets_account_cursors() = runBlocking {
        dbHelper.close()
        app.deleteDatabase(testDatabaseName)
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val legacy = SQLiteDatabase.openOrCreateDatabase(context.getDatabasePath(testDatabaseName), null)
        legacy.execSQL(
            """
            CREATE TABLE upload_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                local_uri TEXT NOT NULL UNIQUE,
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
                upload_id TEXT,
                next_byte_offset INTEGER NOT NULL DEFAULT 0,
                chunk_size INTEGER NOT NULL DEFAULT 33554432,
                state TEXT NOT NULL DEFAULT 'QUEUED',
                error_message TEXT,
                updated_at INTEGER NOT NULL
            )
            """.trimIndent()
        )
        legacy.execSQL(
            """
            CREATE TABLE sync_cursor (
                id INTEGER PRIMARY KEY,
                last_cursor INTEGER NOT NULL DEFAULT 0,
                updated_at INTEGER NOT NULL
            )
            """.trimIndent()
        )
        legacy.insertOrThrow("upload_jobs", null, ContentValues().apply {
            put("local_uri", "content://legacy/photo/1")
            put("filename", "legacy-photo.jpg")
            put("byte_size", 20L)
            put("sha256", "a".repeat(64))
            put("captured_at", "2026-09-23T00:00:00Z")
            put("state", "QUEUED")
            put("updated_at", 1L)
        })
        legacy.insertOrThrow("sync_cursor", null, ContentValues().apply {
            put("id", 1)
            put("last_cursor", 123L)
            put("updated_at", 1L)
        })
        legacy.version = 2
        legacy.close()
        dbHelper = UploadDatabaseHelper(context, testDatabaseName)

        assertTrue(dbHelper.getAllJobs("server|user:11").isEmpty())
        assertEquals(1, dbHelper.countUnassignedPendingJobs())
        assertEquals(null, dbHelper.claimNextPendingJob("server|user:11"))
        assertEquals(0L, dbHelper.getLastSyncCursor("server|user:11"))
        assertEquals(0L, dbHelper.getLastSyncCursor("server|user:12"))

        assertTrue(dbHelper.insertOrIgnoreJob(
            accountKey = "server|user:11",
            localUri = "content://legacy/photo/1",
            filename = "legacy-photo.jpg",
            byteSize = 20L,
            sha256 = "a".repeat(64),
            capturedAt = "2026-09-23T00:00:00Z"
        ) > 0L)
        assertTrue(dbHelper.insertOrIgnoreJob(
            accountKey = "server|user:11",
            localUri = "content://legacy/photo/2",
            filename = "new-photo.jpg",
            byteSize = 20L,
            sha256 = "b".repeat(64),
            capturedAt = "2026-09-23T00:00:00Z"
        ) > 0L)
        assertEquals(2, dbHelper.getAllJobs("server|user:11").size)
        assertEquals(0, dbHelper.countUnassignedPendingJobs())
        assertTrue(dbHelper.getAllJobs("server|user:12").isEmpty())
        dbHelper.saveSyncCursor("server|user:11", 9L)
        assertEquals(9L, dbHelper.getLastSyncCursor("server|user:11"))
        assertEquals(0L, dbHelper.getLastSyncCursor("server|user:12"))
        SQLiteDatabase.openDatabase(
            context.getDatabasePath(testDatabaseName).path,
            null,
            SQLiteDatabase.OPEN_READONLY
        ).use { migrated ->
            migrated.rawQuery("SELECT last_cursor FROM sync_cursor_unassigned_legacy WHERE id = 1", null).use { cursor ->
                assertTrue(cursor.moveToFirst())
                assertEquals(123L, cursor.getLong(0))
            }
        }
    }

    @Test
    fun request_bound_to_account_a_is_rejected_after_switch_instead_of_using_b_token() = runBlocking {
        app.credentialsStore.saveSession(
            deviceId = "account-a-device",
            accessToken = "account-a-token",
            refreshToken = "account-a-refresh",
            expiresInSeconds = 3600,
            username = "account-a",
            serverOrigin = IrisApiClient.getOrigin(server.url("/").toString()),
            userId = 11
        )
        val accountASession = app.credentialsStore.sessionIdentity.value!!
        val accountAService = app.apiClient.apiServiceForSession(accountASession)

        app.credentialsStore.saveSession(
            deviceId = "account-b-device",
            accessToken = "account-b-token",
            refreshToken = "account-b-refresh",
            expiresInSeconds = 3600,
            username = "account-b",
            serverOrigin = IrisApiClient.getOrigin(server.url("/").toString()),
            userId = 12
        )

        val failure = runCatching { accountAService.getChanges(cursor = 456, limit = 9) }.exceptionOrNull()
        assertTrue("A stale session request should fail closed", failure is retrofit2.HttpException)
        assertEquals(409, (failure as retrofit2.HttpException).code())
        assertFalse(
            "The stale A request must not reach the server under B's bearer token",
            requests.any { it.path == "/api/sync/changes?cursor=456&limit=9" }
        )
    }

    @Test
    fun account_queue_restarts_device_bound_partial_upload_after_relogin() = runBlocking {
        val origin = IrisApiClient.getOrigin(server.url("/").toString())
        app.credentialsStore.saveSession(
            deviceId = "account-a-old-device",
            accessToken = "account-a-old-token",
            refreshToken = "account-a-old-refresh",
            expiresInSeconds = 3600,
            username = "account-a",
            serverOrigin = origin,
            userId = 11
        )
        val accountAKey = app.credentialsStore.accountIdentity.value!!
        val jobId = dbHelper.insertOrIgnoreJob(
            accountKey = accountAKey,
            localUri = "content://media/external/images/media/222",
            filename = "resumable.jpg",
            byteSize = 0,
            sha256 = "2".repeat(64),
            capturedAt = "2026-09-24T00:00:00Z"
        )
        dbHelper.updateUploadStarted(accountAKey, jobId, "stale-upload", 0L, 32768)

        app.credentialsStore.saveSession(
            deviceId = "account-b-device",
            accessToken = "account-b-token",
            refreshToken = "account-b-refresh",
            expiresInSeconds = 3600,
            username = "account-b",
            serverOrigin = origin,
            userId = 12
        )
        app.credentialsStore.saveSession(
            deviceId = "account-a-new-device",
            accessToken = "account-a-new-token",
            refreshToken = "account-a-new-refresh",
            expiresInSeconds = 3600,
            username = "account-a",
            serverOrigin = origin,
            userId = 11
        )
        val newSession = app.credentialsStore.sessionIdentity.value!!
        val manager = SyncUploadManager(app.contentResolver, dbHelper) { session ->
            app.apiClient.apiServiceForSession(session)
        }

        assertFalse(manager.processQueue(accountAKey, newSession) {
            app.credentialsStore.sessionIdentity.value == newSession
        })

        val job = dbHelper.getAllJobs(accountAKey).single()
        assertEquals("Expected stale device-bound upload to reset for retry: ${job.errorMessage}", "QUEUED", job.state.name)
        assertEquals(null, job.uploadId)
        assertEquals(0L, job.nextByteOffset)
        assertTrue(
            "A's re-login must restart the job with A's new device credentials",
            requests.any {
                it.path == "/api/sync/uploads/complete-batch" &&
                    JSONObject(it.body.clone().readUtf8()).getJSONArray("uploads").toString().contains("stale-upload") &&
                    it.getHeader("Authorization") == "Bearer account-a-new-token"
            }
        )
    }

    private data class MediaStoreBenchmarkItem(
        val uri: Uri,
        val filename: String,
        val sizeBytes: Long,
    )

    private fun createMediaStoreBenchmarkItem(
        resolver: android.content.ContentResolver,
        filename: String,
        isVideo: Boolean,
        sizeBytes: Int,
        seed: Long,
    ): MediaStoreBenchmarkItem {
        val collection = if (isVideo) MediaStore.Video.Media.EXTERNAL_CONTENT_URI
        else MediaStore.Images.Media.EXTERNAL_CONTENT_URI
        val values = ContentValues().apply {
            put(MediaStore.MediaColumns.DISPLAY_NAME, filename)
            put(MediaStore.MediaColumns.MIME_TYPE, if (isVideo) "video/mp4" else "image/jpeg")
            put(MediaStore.MediaColumns.RELATIVE_PATH, "Pictures/IrisUploadBenchmark/")
            put(MediaStore.MediaColumns.IS_PENDING, 1)
        }
        val uri = resolver.insert(collection, values)
            ?: error("MediaStore refused benchmark item $filename")
        try {
            val random = java.util.Random(seed)
            val block = ByteArray(64 * 1024)
            resolver.openOutputStream(uri, "w")!!.buffered().use { output ->
                var remaining = sizeBytes
                while (remaining > 0) {
                    random.nextBytes(block)
                    val count = minOf(remaining, block.size)
                    output.write(block, 0, count)
                    remaining -= count
                }
            }
            val published = ContentValues().apply { put(MediaStore.MediaColumns.IS_PENDING, 0) }
            check(resolver.update(uri, published, null, null) == 1) {
                "MediaStore benchmark item could not be published: $filename"
            }
            return MediaStoreBenchmarkItem(uri, filename, sizeBytes.toLong())
        } catch (failure: Throwable) {
            resolver.delete(uri, null, null)
            throw failure
        }
    }

    private fun writeBenchmarkFile(file: File, sizeBytes: Int, seed: Int) {
        val block = ByteArray(64 * 1024) { index -> ((index * 31 + seed * 17 + 17) and 0xff).toByte() }
        file.outputStream().buffered().use { output ->
            var remaining = sizeBytes
            while (remaining > 0) {
                val count = minOf(remaining, block.size)
                output.write(block, 0, count)
                remaining -= count
            }
        }
    }

    private fun writeUniqueBenchmarkFile(file: File, sizeBytes: Int, seed: Long) {
        val random = java.util.Random(seed)
        val block = ByteArray(64 * 1024)
        file.outputStream().buffered().use { output ->
            var remaining = sizeBytes
            while (remaining > 0) {
                random.nextBytes(block)
                val count = minOf(remaining, block.size)
                output.write(block, 0, count)
                remaining -= count
            }
        }
    }

    /** Test-only shared pacing for PUT payloads; the cap applies across all workers. */
    private class SharedPayloadRateLimiter(private val bytesPerSecond: Long) {
        private var startedAtNanos = 0L
        private var scheduledBytes = 0L

        init {
            require(bytesPerSecond > 0L)
        }

        fun reserve(byteCount: Long) {
            if (byteCount <= 0L) return
            val delayNanos = synchronized(this) {
                val now = System.nanoTime()
                if (startedAtNanos == 0L) startedAtNanos = now
                scheduledBytes += byteCount
                val deadline = startedAtNanos + scheduledBytes * 1_000_000_000L / bytesPerSecond
                (deadline - now).coerceAtLeast(0L)
            }
            if (delayNanos > 0L) LockSupport.parkNanos(delayNanos)
        }
    }

    /** Buffers pacing to 64 KiB writes so sub-millisecond sleeps don't dominate. */
    private class RateLimitedRequestBody(
        private val delegate: RequestBody,
        private val limiter: SharedPayloadRateLimiter,
    ) : RequestBody() {
        override fun contentType() = delegate.contentType()
        override fun contentLength() = delegate.contentLength()
        override fun isOneShot() = delegate.isOneShot()

        override fun writeTo(sink: BufferedSink) {
            val pacedSink = object : ForwardingSink(sink) {
                private val pending = Buffer()

                override fun write(source: Buffer, byteCount: Long) {
                    pending.write(source, byteCount)
                    emitFullBlocks()
                }

                override fun flush() {
                    emitPending()
                    super.flush()
                }

                private fun emitFullBlocks() {
                    while (pending.size >= PACE_BLOCK_BYTES) {
                        limiter.reserve(PACE_BLOCK_BYTES)
                        super.write(pending, PACE_BLOCK_BYTES)
                    }
                }

                private fun emitPending() {
                    val remaining = pending.size
                    if (remaining > 0L) {
                        limiter.reserve(remaining)
                        super.write(pending, remaining)
                    }
                }
            }.buffer()

            delegate.writeTo(pacedSink)
            pacedSink.flush()
        }

        private companion object {
            const val PACE_BLOCK_BYTES = 64L * 1024L
        }
    }

    private fun runShellCommand(
        instrumentation: android.app.Instrumentation,
        command: String,
    ) {
        val descriptor = instrumentation.uiAutomation.executeShellCommand(command)
        FileInputStream(descriptor.fileDescriptor).use { it.readBytes() }
        descriptor.close()
    }

    private fun formatMs(value: Double): String = String.format(java.util.Locale.US, "%.2f", value)
}
