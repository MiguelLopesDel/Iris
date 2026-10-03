package com.iris.app

import android.content.ContentResolver
import android.content.ContentValues
import android.net.Uri
import android.provider.MediaStore
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.filters.SdkSuppress
import androidx.test.platform.app.InstrumentationRegistry
import com.iris.app.data.local.UploadDatabaseHelper
import com.iris.app.data.model.LocalUploadJob
import com.iris.app.data.model.MediaScanPolicy
import com.iris.app.data.model.UploadJobState
import com.iris.app.data.sync.MediaStoreScanner
import com.iris.app.data.sync.SyncUploadManager
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import java.security.MessageDigest

/**
 * Scans real MediaStore items and changes them the ways users and other apps
 * do, checking that the queue notices content changes and only those.
 */
@RunWith(AndroidJUnit4::class)
// Creates items with RELATIVE_PATH/IS_PENDING and reads GENERATION_MODIFIED.
// Older versions rely on size and modification time, covered by MediaChangePolicyTest.
@SdkSuppress(minSdkVersion = 30)
class MediaChangeDetectionTest {

    private val context = InstrumentationRegistry.getInstrumentation().targetContext
    private val resolver: ContentResolver = context.contentResolver
    private lateinit var dbHelper: UploadDatabaseHelper
    private lateinit var databaseName: String
    private lateinit var manager: SyncUploadManager
    private val accountKey = "https://change-test.example|user:5"
    private val created = mutableListOf<Uri>()
    private var versions = mapOf("external_primary" to "v1")

    @Before
    fun setUp() {
        databaseName = "iris_change_detection_${System.nanoTime()}.db"
        dbHelper = UploadDatabaseHelper(context, databaseName)
        manager = SyncUploadManager(resolver, dbHelper) { error("no network in these tests") }
    }

    @After
    fun tearDown() {
        created.forEach { resolver.delete(it, null, null) }
        dbHelper.close()
        context.deleteDatabase(databaseName)
    }

    private fun scanner() = MediaStoreScanner(resolver, manager, mediaStoreVersions = { versions })

    private fun scan(item: Uri, allowFullVerification: Boolean = false) = runBlocking {
        scanner().scanAndEnqueueNewMedia(
            accountKey,
            MediaScanPolicy(mode = "selected", selectedSourceIds = setOf(sourceIdOf(item)), includeVideos = false),
            allowFullVerification = allowFullVerification,
        )
    }

    private fun job(item: Uri): LocalUploadJob = runBlocking {
        dbHelper.getAllJobs(accountKey).single { it.localUri == item.toString() }
    }

    private fun markUploaded(item: Uri) = runBlocking {
        dbHelper.updateJobState(accountKey, job(item).id, UploadJobState.READY)
    }

    @Test
    fun the_gallery_lists_device_media_newest_first_with_and_without_capture_dates() = runBlocking {
        InstrumentationRegistry.getInstrumentation().uiAutomation.grantRuntimePermission(
            context.packageName, android.Manifest.permission.READ_MEDIA_IMAGES,
        )
        // A download: no capture date, added now.
        val download = createImage("no-date", bytes(10))
        // A photo taken years ago, copied in now.
        val old = createImage("taken-2020", bytes(11))
        resolver.update(old, ContentValues().apply { put(MediaStore.MediaColumns.DATE_TAKEN, 1_590_000_000_000L) }, null, null)

        val records = com.iris.app.data.local.DeviceGalleryReader(context).page(1, 200, "all").records
        val times = records.map { it.fileMtime ?: 0.0 }
        assertEquals("Newest first: $times", times.sortedDescending(), times)
        val names = records.map { it.arquivo }
        val downloadName = resolver.query(download, arrayOf(MediaStore.MediaColumns.DISPLAY_NAME), null, null, null)!!
            .use { it.moveToFirst(); it.getString(0) }
        assertTrue("The item added now is on the first page", downloadName in names)
    }

    @Test
    fun the_newest_first_order_handles_media_with_and_without_capture_dates() {
        // The rows a real phone had: a download (no capture date), a WhatsApp
        // video (zero), an old photo and one just taken.
        val db = android.database.sqlite.SQLiteDatabase.create(null)
        db.execSQL("CREATE TABLE files (_id INTEGER PRIMARY KEY, name TEXT, datetaken INTEGER, date_added INTEGER)")
        db.execSQL("INSERT INTO files VALUES (1, 'download', NULL, 1791038060)")
        db.execSQL("INSERT INTO files VALUES (2, 'whatsapp-video', 0, 1791039875)")
        db.execSQL("INSERT INTO files VALUES (3, 'old-photo', 1590000000000, 1790000000)")
        db.execSQL("INSERT INTO files VALUES (4, 'just-taken', 1791054654123, 1791054654)")
        fun order(sort: String) = db.rawQuery("SELECT name FROM files ORDER BY $sort", null)
            .use { c -> generateSequence { if (c.moveToNext()) c.getString(0) else null }.toList() }

        assertEquals(
            listOf("just-taken", "whatsapp-video", "download", "old-photo"),
            order(com.iris.app.data.local.NEWEST_FIRST),
        )
        // What QUERY_ARG_SORT_COLUMNS + DESC produced: the photo just taken came last.
        assertEquals("just-taken", order("datetaken, date_added DESC").last())
        db.close()
    }

    @Test
    fun the_gallery_and_the_scanner_name_an_item_folder_the_same_way() = runBlocking {
        // The gallery lists device media only with the media permission.
        InstrumentationRegistry.getInstrumentation().uiAutomation.grantRuntimePermission(
            context.packageName, android.Manifest.permission.READ_MEDIA_IMAGES,
        )
        val item = createImage("folder", bytes(9))
        val fromScanner = sourceIdOf(item)
        val fromLookup = com.iris.app.data.sync.DeviceFolders.of(resolver, item)
        assertEquals(fromScanner, fromLookup?.sourceId)
        assertEquals("IrisChangeDetection", fromLookup?.name)

        // The gallery reads the files collection, newest first.
        val record = com.iris.app.data.local.DeviceGalleryReader(context).page(1, 200, "all").records
            .single { com.iris.app.data.model.MediaStoreKey.of(it.deviceUri!!) == com.iris.app.data.model.MediaStoreKey.of(item.toString()) }
        assertEquals(fromScanner, record.deviceSourceId)
        assertEquals("IrisChangeDetection", record.deviceFolder)
    }

    @Test
    fun an_unchanged_item_is_not_hashed_or_queued_again() {
        val item = createImage("same", bytes(1))
        assertEquals(1, scan(item))
        markUploaded(item)

        assertEquals(0, scan(item))
        assertEquals(UploadJobState.READY, job(item).state)
    }

    @Test
    fun an_edit_saved_over_the_file_queues_the_new_content() {
        val original = bytes(1)
        val item = createImage("edited", original)
        scan(item)
        markUploaded(item)

        // Same size, different bytes: the case a size check alone misses.
        overwrite(item, bytes(2))

        assertEquals(1, scan(item))
        val updated = job(item)
        assertEquals(UploadJobState.QUEUED, updated.state)
        assertEquals(sha256(bytes(2)), updated.sha256)
        assertEquals(sha256(original), updated.previousSha256)
        assertNull(updated.uploadId)
    }

    @Test
    fun a_version_requeued_behind_a_running_pass_is_still_claimed_by_it() = runBlocking {
        val edited = createImage("behind-cursor", bytes(13))
        val later = createImage("ahead", bytes(14))
        scan(edited)
        scan(later)
        markUploaded(edited)
        markUploaded(later)
        // A pass has claimed up to the newest row.
        val cursor = maxOf(job(edited).id, job(later).id)

        overwrite(edited, bytes(15))
        assertEquals(1, scan(edited))

        val next = dbHelper.claimNextPendingJob(accountKey, cursor)
        assertEquals("The new version is ahead of the cursor", edited.toString(), next?.localUri)
        assertEquals(sha256(bytes(15)), next?.sha256)
        // The id it moved to is never handed out again.
        val fresh = createImage("after-move", bytes(16))
        assertEquals(1, scan(fresh))
        assertTrue(job(fresh).id > next!!.id)
    }

    @Test
    fun a_rename_keeps_the_upload_and_records_the_new_name() {
        val item = createImage("before", bytes(3))
        scan(item)
        markUploaded(item)

        val renamed = "after-${System.nanoTime()}.jpg"
        resolver.update(item, ContentValues().apply { put(MediaStore.MediaColumns.DISPLAY_NAME, renamed) }, null, null)

        assertEquals(0, scan(item))
        val updated = job(item)
        assertEquals(UploadJobState.READY, updated.state)
        assertEquals(sha256(bytes(3)), updated.sha256)
        assertNull(updated.previousSha256)
    }

    @Test
    fun a_rebuilt_media_store_rehashes_and_requeues_an_id_that_now_names_other_content() = runBlocking {
        val item = createImage("reused", bytes(4))
        scan(item)
        markUploaded(item)
        // What a rebuilt MediaStore can leave behind: the id now names other
        // content whose size and dates happen to match the old row.
        val stale = sha256(bytes(99))
        dbHelper.writableDatabase.execSQL(
            "UPDATE upload_jobs SET sha256 = ? WHERE id = ?",
            arrayOf<Any>(stale, job(item).id),
        )

        versions = mapOf("external_primary" to "v2")
        assertEquals(1, scan(item))

        val updated = job(item)
        assertEquals(sha256(bytes(4)), updated.sha256)
        assertEquals(stale, updated.previousSha256)
        assertEquals(UploadJobState.QUEUED, updated.state)
        val state = dbHelper.scanState(accountKey)
        assertNull("A finished scan ends the verification", state.fullVerificationStartedAt)
        assertEquals("v2", state.mediaStoreVersions["external_primary"])
    }

    @Test
    fun a_verification_stays_open_while_a_changed_item_is_being_sent() = runBlocking {
        val item = createImage("in-flight", bytes(12))
        scan(item)
        // The bytes differ from the queued hash, and the row is uploading
        // right now, so the scan cannot requeue it yet.
        val stale = sha256(bytes(98))
        dbHelper.writableDatabase.execSQL(
            "UPDATE upload_jobs SET sha256 = ?, state = 'UPLOADING' WHERE id = ?",
            arrayOf<Any>(stale, job(item).id),
        )

        versions = mapOf("external_primary" to "v2")
        scan(item)
        assertEquals(stale, job(item).sha256)
        assertNotNull(
            "The deferred item keeps the verification open",
            dbHelper.scanState(accountKey).fullVerificationStartedAt,
        )

        // The upload ends; the next ordinary scan (same fingerprint) still checks it.
        dbHelper.updateJobState(accountKey, job(item).id, UploadJobState.READY)
        assertEquals(1, scan(item))
        assertEquals(sha256(bytes(12)), job(item).sha256)
        assertEquals(UploadJobState.QUEUED, job(item).state)
        assertNull(dbHelper.scanState(accountKey).fullVerificationStartedAt)
    }

    @Test
    fun a_rebuilt_media_store_leaves_unchanged_content_uploaded() {
        val item = createImage("kept", bytes(5))
        scan(item)
        markUploaded(item)

        versions = mapOf("external_primary" to "v2")
        assertEquals(0, scan(item))

        val updated = job(item)
        assertEquals(UploadJobState.READY, updated.state)
        assertNotNull("Verified in this run", updated.verifiedAt)
    }

    @Test
    fun the_weekly_verification_runs_only_when_allowed_and_due() = runBlocking {
        val item = createImage("weekly", bytes(6))
        scan(item)
        val firstVerifiedAt = job(item).verifiedAt!!

        // Not due yet: allowed, but the baseline is from this first scan.
        scan(item, allowFullVerification = true)
        assertEquals(firstVerifiedAt, job(item).verifiedAt)

        val state = dbHelper.scanState(accountKey)
        dbHelper.saveScanState(accountKey, state.copy(lastFullVerificationAt = 1L))
        scan(item, allowFullVerification = false)
        assertEquals("Not allowed (not charging)", firstVerifiedAt, job(item).verifiedAt)

        scan(item, allowFullVerification = true)
        assertTrue(job(item).verifiedAt!! > firstVerifiedAt)
    }

    @Test
    fun a_reinstalled_server_at_the_same_address_requeues_the_account_without_rehashing() = runBlocking {
        val item = createImage("reinstalled", bytes(8))
        scan(item)
        markUploaded(item)
        val hash = job(item).sha256
        dbHelper.saveSyncCursor(accountKey, 42L)

        // A queue with no installation recorded (from before this check) is
        // reconciled once: requeued, with its hashes kept.
        assertEquals(1, manager.bindServerInstance(accountKey, "a".repeat(32)))
        dbHelper.updateJobState(accountKey, job(item).id, UploadJobState.DUPLICATE)
        assertEquals(0, manager.bindServerInstance(accountKey, "a".repeat(32)))
        assertEquals(UploadJobState.DUPLICATE, job(item).state)

        // Same address and user id, another installation: its states are void.
        dbHelper.saveSyncCursor(accountKey, 42L)
        assertEquals(1, manager.bindServerInstance(accountKey, "b".repeat(32)))
        val requeued = job(item)
        assertEquals(UploadJobState.QUEUED, requeued.state)
        assertNull(requeued.uploadId)
        assertEquals(hash, requeued.sha256)
        assertEquals(0L, dbHelper.getLastSyncCursor(accountKey))

        // A server too old to report an id changes nothing.
        dbHelper.updateJobState(accountKey, requeued.id, UploadJobState.READY)
        assertEquals(0, manager.bindServerInstance(accountKey, null))
        assertEquals(UploadJobState.READY, job(item).state)
    }

    @Test
    fun a_row_from_before_fingerprints_adopts_one_without_rehashing() = runBlocking {
        val item = createImage("legacy", bytes(7))
        // Queued by an older version: no modification time, no verification.
        dbHelper.insertOrIgnoreJob(
            accountKey = accountKey,
            localUri = item.toString(),
            filename = "legacy.jpg",
            byteSize = bytes(7).size.toLong(),
            sha256 = "0".repeat(64),
            capturedAt = "2026-10-02T00:00:00Z",
        )
        markUploaded(item)

        assertEquals(0, scan(item))

        val updated = job(item)
        // Adopted as is: the stored hash was not recomputed.
        assertEquals("0".repeat(64), updated.sha256)
        assertNotNull(updated.sourceDateModified)
        assertEquals(UploadJobState.READY, updated.state)
    }

    @Test
    fun upgrading_a_version_6_queue_adds_the_new_columns_and_retries_hash_mismatches() {
        val name = "iris_change_v6_${System.nanoTime()}.db"
        val legacy = android.database.sqlite.SQLiteDatabase.openOrCreateDatabase(context.getDatabasePath(name), null)
        legacy.execSQL(
            """
            CREATE TABLE upload_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT, account_key TEXT, local_uri TEXT NOT NULL,
                filename TEXT NOT NULL, byte_size INTEGER NOT NULL, sha256 TEXT NOT NULL,
                captured_at TEXT NOT NULL, source_id TEXT, source_name TEXT, source_relative_path TEXT,
                source_volume TEXT, source_media_store_id TEXT, source_generation INTEGER NOT NULL DEFAULT 0,
                source_media_kind TEXT, upload_id TEXT, next_byte_offset INTEGER NOT NULL DEFAULT 0,
                chunk_size INTEGER NOT NULL DEFAULT 33554432, state TEXT NOT NULL DEFAULT 'QUEUED',
                error_message TEXT, updated_at INTEGER NOT NULL
            )
            """.trimIndent()
        )
        legacy.execSQL("CREATE UNIQUE INDEX idx_upload_jobs_account_uri ON upload_jobs(account_key, local_uri)")
        legacy.execSQL(
            "INSERT INTO upload_jobs (account_key, local_uri, filename, byte_size, sha256, captured_at, state, updated_at) " +
                "VALUES ('$accountKey', 'content://media/external/images/media/1', 'a.jpg', 10, '${"a".repeat(64)}', 'x', 'READY', 1)"
        )
        legacy.execSQL(
            "INSERT INTO upload_jobs (account_key, local_uri, filename, byte_size, sha256, captured_at, state, error_message, upload_id, updated_at) " +
                "VALUES ('$accountKey', 'content://media/external/images/media/2', 'b.jpg', 10, '${"b".repeat(64)}', 'x', 'FAILED', 'Hash do arquivo não confere', 'u-2', 1)"
        )
        legacy.execSQL("CREATE TABLE sync_cursors (account_key TEXT PRIMARY KEY NOT NULL, last_cursor INTEGER NOT NULL DEFAULT 0, updated_at INTEGER NOT NULL)")
        legacy.execSQL(
            "CREATE TABLE sync_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, account_key TEXT NOT NULL, started_at INTEGER NOT NULL, " +
                "ended_at INTEGER, run_trigger TEXT NOT NULL, started_in_foreground INTEGER NOT NULL, bytes INTEGER NOT NULL DEFAULT 0, " +
                "items INTEGER NOT NULL DEFAULT 0, upload_millis INTEGER NOT NULL DEFAULT 0, outcome TEXT NOT NULL, stop_reason INTEGER, " +
                "detail TEXT, foreground_service INTEGER)"
        )
        legacy.version = 6
        legacy.close()

        val upgraded = UploadDatabaseHelper(context, name)
        try {
            runBlocking {
                val rows = upgraded.getAllJobs(accountKey).associateBy { it.filename }
                val row = rows.getValue("a.jpg")
                assertNull(row.sourceDateModified)
                assertEquals(UploadJobState.READY, row.state)
                // Refused for a hash mismatch before uploads measured the real file: retried.
                val refused = rows.getValue("b.jpg")
                assertEquals(UploadJobState.QUEUED, refused.state)
                assertNull(refused.uploadId)
                assertNull(refused.errorMessage)
                assertNotNull(upgraded.knownMedia(accountKey, "content://media/external/images/media/1"))
                assertEquals(emptyMap<String, String>(), upgraded.scanState(accountKey).mediaStoreVersions)
            }
        } finally {
            upgraded.close()
            context.deleteDatabase(name)
        }
    }

    private fun bytes(seed: Int): ByteArray = java.util.Random(seed.toLong()).let { random ->
        ByteArray(48 * 1024).also(random::nextBytes)
    }

    private fun sha256(data: ByteArray): String =
        MessageDigest.getInstance("SHA-256").digest(data).joinToString("") { "%02x".format(it) }

    private fun createImage(name: String, content: ByteArray): Uri {
        val values = ContentValues().apply {
            put(MediaStore.MediaColumns.DISPLAY_NAME, "$name-${System.nanoTime()}.jpg")
            put(MediaStore.MediaColumns.MIME_TYPE, "image/jpeg")
            put(MediaStore.MediaColumns.RELATIVE_PATH, "Pictures/IrisChangeDetection/")
            put(MediaStore.MediaColumns.IS_PENDING, 1)
        }
        val uri = resolver.insert(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, values)!!
        created += uri
        resolver.openOutputStream(uri, "w")!!.use { it.write(content) }
        resolver.update(uri, ContentValues().apply { put(MediaStore.MediaColumns.IS_PENDING, 0) }, null, null)
        return uri
    }

    /** Writes over the file the way an editor saving in place does. */
    private fun overwrite(uri: Uri, content: ByteArray) {
        // MediaStore decides whether to rescan from size and modification time
        // in whole seconds: a same-size overwrite within the same second as the
        // last write goes unnoticed by the platform itself (only the weekly full
        // verification catches it). Editors save long after the photo was taken.
        Thread.sleep(1_100)
        val before = generationOf(uri)
        resolver.openOutputStream(uri, "wt")!!.use { it.write(content) }
        // MediaStore rescans the file when the stream closes.
        val deadline = System.currentTimeMillis() + 5_000
        while (generationOf(uri) == before && System.currentTimeMillis() < deadline) Thread.sleep(50)
        assertNotEquals("MediaStore must notice the overwrite", before, generationOf(uri))
    }

    private fun generationOf(uri: Uri): Long =
        resolver.query(uri, arrayOf(MediaStore.MediaColumns.GENERATION_MODIFIED), null, null, null)!!.use {
            it.moveToFirst()
            it.getLong(0)
        }

    private fun sourceIdOf(uri: Uri): String =
        resolver.query(
            uri,
            arrayOf(MediaStore.MediaColumns.VOLUME_NAME, MediaStore.MediaColumns.BUCKET_ID),
            null,
            null,
            null,
        )!!.use {
            it.moveToFirst()
            "${it.getString(0)}:${it.getString(1)}:image"
        }
}
