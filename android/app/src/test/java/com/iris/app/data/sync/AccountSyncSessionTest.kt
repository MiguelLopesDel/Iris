package com.iris.app.data.sync

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class AccountSyncSessionTest {
    private val session = AccountSyncSession("server|alice|device-a", "server|user:1")

    @Test
    fun `matches only when both session and account remain current`() {
        assertTrue(session.matches("server|alice|device-a", "server|user:1"))
        assertFalse(session.matches("server|bob|device-b", "server|user:1"))
        assertFalse(session.matches("server|alice|device-a", "server|user:2"))
        assertFalse(session.matches(null, null))
    }
}
