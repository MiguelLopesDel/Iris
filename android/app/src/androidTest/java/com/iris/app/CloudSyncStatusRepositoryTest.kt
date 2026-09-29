package com.iris.app

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.iris.app.data.model.CloudConnectionState
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class CloudSyncStatusRepositoryTest {

    @Test
    fun cloud_status_is_persisted_and_isolated_between_accounts() = runBlocking {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val repository = (context.applicationContext as IrisApplication).settingsRepository
        val accountA = "cloud-status-test-${System.nanoTime()}|user:1"
        val accountB = "$accountA|user:2"

        assertEquals(
            CloudConnectionState.UNKNOWN,
            repository.cloudSyncStatusForAccount(accountA).first().connectionState,
        )
        repository.markCloudUnavailable(accountA)
        assertEquals(
            CloudConnectionState.OFFLINE,
            repository.cloudSyncStatusForAccount(accountA).first().connectionState,
        )
        assertEquals(
            CloudConnectionState.UNKNOWN,
            repository.cloudSyncStatusForAccount(accountB).first().connectionState,
        )

        repository.markCloudSyncSucceeded(accountA)
        val recovered = repository.cloudSyncStatusForAccount(accountA).first()
        assertEquals(CloudConnectionState.CONNECTED, recovered.connectionState)
        assertNotNull(recovered.lastCheckedAtMillis)
        assertNotNull(recovered.lastSuccessfulSyncAtMillis)
        assertNull(recovered.syncError)
    }
}
