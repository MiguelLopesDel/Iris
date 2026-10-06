package com.iris.app

import android.content.Context
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import androidx.work.Configuration
import androidx.work.Constraints
import androidx.work.ExistingWorkPolicy
import androidx.work.ListenableWorker
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkInfo
import androidx.work.WorkManager
import androidx.work.Worker
import androidx.work.WorkerFactory
import androidx.work.WorkerParameters
import androidx.work.testing.SynchronousExecutor
import androidx.work.testing.WorkManagerTestInitHelper
import com.iris.app.data.sync.MediaSyncWorker
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import android.annotation.SuppressLint
import androidx.work.impl.WorkManagerImpl
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith

/**
 * A sync that empties the queue ends the other work's pending retry, with the
 * real WorkManager (test executor): the screen stopped saying "the last sync did
 * not finish" only when the periodic work in backoff ran again, hours later.
 */
@RunWith(AndroidJUnit4::class)
class ObsoleteRetryWorkTest {

    /** Stands in for a sync run that did not finish. */
    class UnfinishedSync(context: Context, params: WorkerParameters) : Worker(context, params) {
        override fun doWork(): Result = Result.retry()
    }

    private val context = InstrumentationRegistry.getInstrumentation().targetContext
    private lateinit var workManager: WorkManager

    @Before
    fun setUp() {
        val unfinished = object : WorkerFactory() {
            override fun createWorker(appContext: Context, workerClassName: String, workerParameters: WorkerParameters): ListenableWorker =
                UnfinishedSync(appContext, workerParameters)
        }
        WorkManagerTestInitHelper.initializeTestWorkManager(
            context,
            Configuration.Builder().setExecutor(SynchronousExecutor()).setWorkerFactory(unfinished).build(),
        )
        workManager = WorkManager.getInstance(context)
    }

    /**
     * The test WorkManager replaces the process-wide instance, and every test
     * in the suite shares the process: left in place, its synchronous executor
     * and always-retrying worker slowed the upload benchmarks that ran after
     * it. Dropping it makes the next getInstance() initialize the app's real
     * WorkManager again; there is no public API for that.
     */
    @SuppressLint("RestrictedApi")
    @After
    fun restoreRealWorkManager() {
        WorkManagerImpl.setDelegate(null)
    }

    private fun works(name: String): List<WorkInfo> = workManager.getWorkInfosForUniqueWork(name).get()

    @Test
    fun a_manual_run_that_completes_puts_the_periodic_sync_back_on_schedule() = runBlocking {
        MediaSyncWorker.schedulePeriodic(context)
        val periodic = works(PERIODIC).single()
        val driver = WorkManagerTestInitHelper.getTestDriver(context)!!
        driver.setAllConstraintsMet(periodic.id)
        driver.setPeriodDelayMet(periodic.id)
        assertTrue("The periodic sync is waiting to retry", works(PERIODIC).single().runAttemptCount > 0)
        assertTrue(MediaSyncWorker.retryPending(context).first())

        MediaSyncWorker.clearObsoleteRetry(context, completedRunWasPeriodic = false)

        val reset = works(PERIODIC).single { it.state == WorkInfo.State.ENQUEUED }
        assertEquals(0, reset.runAttemptCount)
        assertFalse("The screen no longer says the last sync did not finish", MediaSyncWorker.retryPending(context).first())
    }

    @Test
    fun a_periodic_run_that_completes_drops_a_manual_sync_waiting_to_retry() = runBlocking {
        val manual = OneTimeWorkRequestBuilder<MediaSyncWorker>()
            .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
            .build()
        workManager.enqueueUniqueWork(ONE_TIME, ExistingWorkPolicy.REPLACE, manual).result.get()
        WorkManagerTestInitHelper.getTestDriver(context)!!.setAllConstraintsMet(manual.id)
        assertTrue(works(ONE_TIME).single().runAttemptCount > 0)

        MediaSyncWorker.clearObsoleteRetry(context, completedRunWasPeriodic = true)

        assertEquals(WorkInfo.State.CANCELLED, works(ONE_TIME).single().state)
        assertFalse(MediaSyncWorker.retryPending(context).first())
    }

    @Test
    fun a_periodic_sync_on_its_normal_schedule_is_left_alone() = runBlocking {
        MediaSyncWorker.schedulePeriodic(context)
        val before = works(PERIODIC).single()

        MediaSyncWorker.clearObsoleteRetry(context, completedRunWasPeriodic = false)

        assertEquals(before.id, works(PERIODIC).single().id)
    }

    private companion object {
        // The unique work names MediaSyncWorker enqueues under.
        const val PERIODIC = "iris_periodic_sync"
        const val ONE_TIME = "iris_immediate_sync"
    }
}
