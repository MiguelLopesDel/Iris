package com.iris.app

import android.Manifest
import android.content.ContentValues
import android.net.Uri
import android.os.ParcelFileDescriptor
import android.provider.MediaStore
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import androidx.work.WorkInfo
import androidx.work.WorkManager
import com.iris.app.data.model.SyncRunOutcome
import com.iris.app.data.sync.MediaSyncWorker
import com.iris.app.data.sync.SyncRunRecorder
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith

/**
 * The real worker, WorkManager and Android service manager against an isolated
 * Iris lab server, with no activity visible: the situation of a backup with
 * the app closed. Runs only when a lab server is supplied, like the upload
 * benchmark (`-e irisBenchBaseUrl http://10.0.2.2:8851` plus a lab account).
 */
@RunWith(AndroidJUnit4::class)
class BackgroundSyncServiceTest {
    private val instrumentation = InstrumentationRegistry.getInstrumentation()
    private val app = instrumentation.targetContext.applicationContext as IrisApplication
    private val packageName = app.packageName
    private val createdMedia = mutableListOf<Uri>()
    /** Login in setUp already schedules a sync; every run from then on belongs to this test. */
    private var testStartedAt = 0L
    /** Restored afterwards, so the suites that run next see the app as they left it. */
    private var originalServerUrl: String? = null

    @Before
    fun setUp() = runBlocking {
        testStartedAt = System.currentTimeMillis()
        val args = InstrumentationRegistry.getArguments()
        val baseUrl = args.getString("irisBenchBaseUrl")?.takeIf(String::isNotBlank)
        val username = args.getString("irisBenchUsername")?.takeIf(String::isNotBlank)
        val password = args.getString("irisBenchPassword")?.takeIf(String::isNotBlank)
        assumeTrue("Only run against an isolated lab server", baseUrl != null && username != null && password != null)
        val host = Uri.parse(baseUrl).host
        check(host in setOf("127.0.0.1", "localhost", "10.0.2.2")) {
            "Refusing to upload test media anywhere except the local lab server"
        }

        instrumentation.uiAutomation.grantRuntimePermission(packageName, Manifest.permission.READ_MEDIA_IMAGES)
        instrumentation.uiAutomation.grantRuntimePermission(packageName, Manifest.permission.READ_MEDIA_VIDEO)
        instrumentation.uiAutomation.grantRuntimePermission(packageName, Manifest.permission.POST_NOTIFICATIONS)

        originalServerUrl = app.settingsRepository.serverUrl.first()
        app.settingsRepository.updateServerUrl(baseUrl!!)
        withTimeout(10_000) {
            while (app.apiClient.baseUrl.trimEnd('/') != baseUrl.trimEnd('/')) delay(100)
        }
        app.irisRepository.deviceLogin(username!!, password!!, "background service test").getOrThrow()
        val accountKey = app.credentialsStore.accountIdentity.value!!
        app.settingsRepository.updateAutoBackupEnabled(accountKey, true)
        app.settingsRepository.updateSyncSourceMode(accountKey, "all")
    }

    @After
    fun tearDown() = runBlocking {
        if (originalServerUrl == null) return@runBlocking // skipped: nothing was changed
        if (app.credentialsStore.accountIdentity.value != null) app.irisRepository.logoutDevice()
        WorkManager.getInstance(app).cancelAllWork().result.get()
        createdMedia.forEach { app.contentResolver.delete(it, null, null) }
        shell("dumpsys deviceidle whitelist -$packageName")
        app.settingsRepository.updateServerUrl(originalServerUrl!!)
    }

    @Test
    fun with_the_battery_exemption_a_closed_app_backup_runs_as_a_data_sync_service() = runBlocking {
        shell("dumpsys deviceidle whitelist +$packageName")
        val run = runBackgroundSync()

        assertTrue("The service manager never showed a dataSync foreground service", run.sawDataSyncService)
        assertEquals(WorkInfo.State.SUCCEEDED, run.finalState)
        // The periodic sync may be the one that uploads while the requested one
        // finds the queue busy; wait until whichever runs has finished.
        val accountKey = app.credentialsStore.accountIdentity.value!!
        withTimeout(120_000) {
            while (SyncRunRecorder.activeRunIds.value.isNotEmpty()) delay(250)
        }
        val runs = app.dbHelper.recentSyncRuns(accountKey, 50).filter { it.startedAtMillis >= testStartedAt }
        android.util.Log.i("BgSyncTest", "runs=" + runs.joinToString("\n"))
        val uploading = runs.filter { it.bytes > 0L }
        assertTrue("No run uploaded the test media: $runs", uploading.isNotEmpty())
        // "All folders" also uploads media other tests left on the emulator.
        assertTrue("Runs sent less than the test media: $uploading", uploading.sumOf { it.bytes } >= MEDIA_BYTES * MEDIA_COUNT)
        assertTrue("Every uploading run should be a foreground service: $uploading",
            uploading.all { it.foregroundService == true })
        // App start and setting changes must never cancel a sync that is
        // uploading. A retry after a transient failure is allowed; the media
        // must still all reach the server.
        assertTrue("A sync was cancelled: $runs", runs.none { it.outcome == SyncRunOutcome.STOPPED })
        // A retried item is picked up by WorkManager's next attempt, about 30 s later.
        withTimeout(180_000) {
            while (app.dbHelper.getAllJobs(accountKey)
                    .count { it.filename.startsWith("bg-service-") && it.state.name == "READY" } < MEDIA_COUNT
            ) delay(500)
        }
    }

    private data class Observed(val sawDataSyncService: Boolean, val finalState: WorkInfo.State)

    private suspend fun runBackgroundSync(): Observed {
        repeat(MEDIA_COUNT) { index -> createdMedia += createVideo("bg-service-$index-${System.nanoTime()}.mp4") }
        MediaSyncWorker.enqueueImmediate(app)
        var sawService = false
        val state = withTimeout(180_000) {
            while (true) {
                if (!sawService) sawService = dataSyncServiceRunning()
                val infos = WorkManager.getInstance(app).getWorkInfosForUniqueWorkFlow(ONE_TIME_WORK).first()
                android.util.Log.i("BgSyncTest", "work=" + infos.joinToString { "${it.id}:${it.state}:${it.runAttemptCount}" })
                // A sync requested while another runs is appended after it; wait for the whole chain.
                if (infos.isNotEmpty() && infos.all { it.state.isFinished }) return@withTimeout infos.last().state
                delay(250)
            }
            @Suppress("UNREACHABLE_CODE")
            WorkInfo.State.FAILED
        }
        return Observed(sawService, state)
    }

    /** What Android's service manager reports: WorkManager's service, in the foreground, typed dataSync. */
    private fun dataSyncServiceRunning(): Boolean {
        val services = shell("dumpsys activity services $packageName")
        val block = services.substringAfter("SystemForegroundService", missingDelimiterValue = "")
        return block.isNotEmpty() && "isForeground=true" in block &&
            // Android 14+ prints "types=", older releases "foregroundServiceType=". 0x1 is dataSync.
            Regex("(types|foregroundServiceType)=0x0*1\\b").containsMatchIn(block)
    }

    private fun createVideo(filename: String): Uri {
        val resolver = app.contentResolver
        val values = ContentValues().apply {
            put(MediaStore.MediaColumns.DISPLAY_NAME, filename)
            put(MediaStore.MediaColumns.MIME_TYPE, "video/mp4")
            put(MediaStore.MediaColumns.RELATIVE_PATH, "Movies/IrisBackgroundServiceTest/")
            put(MediaStore.MediaColumns.IS_PENDING, 1)
        }
        val uri = resolver.insert(MediaStore.Video.Media.EXTERNAL_CONTENT_URI, values)
            ?: error("MediaStore refused $filename")
        val random = java.util.Random(filename.hashCode().toLong())
        val block = ByteArray(64 * 1024)
        resolver.openOutputStream(uri, "w")!!.buffered().use { output ->
            var remaining = MEDIA_BYTES
            while (remaining > 0) {
                random.nextBytes(block)
                val count = minOf(remaining, block.size.toLong()).toInt()
                output.write(block, 0, count)
                remaining -= count
            }
        }
        resolver.update(uri, ContentValues().apply { put(MediaStore.MediaColumns.IS_PENDING, 0) }, null, null)
        return uri
    }

    private fun shell(command: String): String =
        ParcelFileDescriptor.AutoCloseInputStream(instrumentation.uiAutomation.executeShellCommand(command))
            .bufferedReader().use { it.readText() }

    private companion object {
        const val ONE_TIME_WORK = "iris_immediate_sync"
        const val MEDIA_COUNT = 4
        const val MEDIA_BYTES = 36L * 1024 * 1024
    }
}
