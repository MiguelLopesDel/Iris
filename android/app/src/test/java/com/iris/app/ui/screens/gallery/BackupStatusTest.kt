package com.iris.app.ui.screens.gallery

import com.iris.app.data.model.CloudConnectionState
import org.junit.Assert.assertEquals
import org.junit.Test

class BackupStatusTest {
    private fun state(
        signedIn: Boolean = true,
        online: Boolean? = true,
        connection: CloudConnectionState = CloudConnectionState.CONNECTED,
        error: String? = null,
    ) = backupState(signedIn, online, connection, error)

    @Test
    fun `signed out asks to sign in before anything else`() {
        assertEquals(BackupState.SIGNED_OUT, state(signedIn = false, online = false))
    }

    @Test
    fun `an unreachable server is reported as offline`() {
        assertEquals(BackupState.OFFLINE, state(online = false))
        assertEquals(BackupState.OFFLINE, state(connection = CloudConnectionState.OFFLINE))
    }

    @Test
    fun `a sync error needs attention`() {
        assertEquals(BackupState.ATTENTION, state(error = "timeout"))
    }

    @Test
    fun `otherwise the backup is up to date`() {
        assertEquals(BackupState.OK, state())
        // Still checking is not a problem worth showing.
        assertEquals(BackupState.OK, state(online = null, connection = CloudConnectionState.CHECKING))
    }
}
