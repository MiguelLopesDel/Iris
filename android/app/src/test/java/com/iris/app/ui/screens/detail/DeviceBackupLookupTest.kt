package com.iris.app.ui.screens.detail

import com.iris.app.data.local.DeviceBackupState
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class DeviceBackupLookupTest {
    @Test
    fun `backup state is visible only for the current uri and account while the panel is open`() {
        val lookupA = deviceBackupLookup("content://media/1", "account-a", panelOpen = true, panelRevision = 1)!!
        val savedForA = DeviceBackupSnapshot(lookupA, DeviceBackupState.SAVED)

        assertEquals(DeviceBackupState.SAVED, visibleDeviceBackupState(lookupA, savedForA, "account-a"))
        assertNull(visibleDeviceBackupState(lookupA, savedForA, "account-b"))
        assertNull(visibleDeviceBackupState(deviceBackupLookup("content://media/2", "account-a", true, 1), savedForA, "account-a"))
        assertNull(visibleDeviceBackupState(deviceBackupLookup("content://media/1", "account-b", true, 1), savedForA, "account-b"))
        assertNull(visibleDeviceBackupState(deviceBackupLookup("content://media/1", "account-a", true, 2), savedForA, "account-a"))
        assertNull(visibleDeviceBackupState(deviceBackupLookup("content://media/1", "account-a", false, 1), savedForA, "account-a"))
        assertNull(deviceBackupLookup("content://media/1", null, panelOpen = true, panelRevision = 1))
    }

    @Test
    fun `opening the panel again gets a fresh backup lookup identity`() {
        val first = deviceBackupLookup("content://media/1", "account-a", panelOpen = true, panelRevision = 4)!!
        val previous = DeviceBackupSnapshot(first, DeviceBackupState.SAVED)
        val reopened = deviceBackupLookup("content://media/1", "account-a", panelOpen = true, panelRevision = 5)

        assertNull(visibleDeviceBackupState(reopened, previous, "account-a"))
    }
}
