package com.iris.app

import com.iris.app.data.model.HealthResponse
import com.iris.app.data.remote.SessionServerCheck
import com.iris.app.data.remote.SessionServerCheck.Verdict
import org.junit.Assert.assertEquals
import org.junit.Test

class SessionServerCheckTest {

    private val a = "a".repeat(32)
    private val b = "b".repeat(32)

    @Test
    fun `the same installation keeps the session`() {
        assertEquals(Verdict.SAME, SessionServerCheck.verdict(HealthResponse("ok", "multiuser", a), a))
    }

    @Test
    fun `a session from before the check records the installation`() {
        assertEquals(Verdict.RECORD, SessionServerCheck.verdict(HealthResponse("ok", "multiuser", a), null))
    }

    @Test
    fun `another installation at the same address voids the session`() {
        assertEquals(Verdict.REPLACED, SessionServerCheck.verdict(HealthResponse("ok", "multiuser", b), a))
    }

    @Test
    fun `a server with no accounts yet voids any session, even an unrecorded one`() {
        assertEquals(Verdict.REPLACED, SessionServerCheck.verdict(HealthResponse("setup_required", "multiuser", b), null))
        assertEquals(Verdict.REPLACED, SessionServerCheck.verdict(HealthResponse("setup_required", "multiuser", a), a))
    }

    @Test
    fun `a server too old to report an id changes nothing`() {
        assertEquals(Verdict.SAME, SessionServerCheck.verdict(HealthResponse("ok", "legacy", null), a))
        assertEquals(Verdict.SAME, SessionServerCheck.verdict(HealthResponse("ok", "legacy", null), null))
    }
}
