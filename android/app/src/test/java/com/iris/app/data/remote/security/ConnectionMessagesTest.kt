package com.iris.app.data.remote.security

import okhttp3.OkHttpClient
import okhttp3.Request
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.io.IOException
import java.net.ConnectException
import java.net.ServerSocket
import java.net.SocketTimeoutException
import java.net.UnknownHostException

class ConnectionMessagesTest {

    private val security = ConnectionSecurity.Unrestricted

    private fun problem(error: Throwable, origin: ServerOrigin) = ConnectionProblem.from(error, origin, security)

    @Test
    fun `a closed port is told apart from a silent address`() {
        // A port that was free a moment ago: nothing listens there.
        val port = ServerSocket(0).use { it.localPort }
        val origin = ServerOrigin("127.0.0.1", port)
        val error = try {
            OkHttpClient().newCall(Request.Builder().url("http://127.0.0.1:$port/healthz").build()).execute().close()
            fail("nothing should answer on a closed port")
            return
        } catch (error: IOException) {
            error
        }

        val found = problem(error, origin)
        assertEquals(ConnectionProblem.Unreachable(origin, ConnectionProblem.Unreachable.Reason.REFUSED), found)
        assertTrue(ConnectionMessages.describe(found!!).contains("porta $port"))
    }

    @Test
    fun `a Tailscale address that does not answer points at Tailscale`() {
        val origin = ServerOrigin("100.64.0.10", 8443)
        val found = problem(SocketTimeoutException("connect timed out"), origin)

        assertEquals(ConnectionProblem.Unreachable(origin, ConnectionProblem.Unreachable.Reason.SILENT), found)
        assertTrue(ConnectionMessages.describe(found!!).contains("Tailscale está ligado"))
    }

    @Test
    fun `a home network address that does not answer points at the Wi-Fi`() {
        val origin = ServerOrigin("192.168.1.20", 8443)
        val found = problem(ConnectException("Failed to connect to /192.168.1.20:8443"), origin)

        assertTrue(ConnectionMessages.describe(found!!).contains("mesmo Wi-Fi"))
    }

    @Test
    fun `a name that does not resolve says so`() {
        val origin = ServerOrigin("iris.example", 443)
        val found = problem(UnknownHostException("iris.example"), origin)

        assertEquals(ConnectionProblem.Unreachable(origin, ConnectionProblem.Unreachable.Reason.UNKNOWN_HOST), found)
        assertTrue(ConnectionMessages.describe(found!!).contains("não foi encontrado"))
    }

    @Test
    fun `Tailscale addresses are recognised by range and name`() {
        assertTrue(ConnectionProblem.isTailscaleAddress("100.64.0.1"))
        assertTrue(ConnectionProblem.isTailscaleAddress("100.127.255.254"))
        assertTrue(ConnectionProblem.isTailscaleAddress("iris.tailnet-name.ts.net"))
        assertEquals(false, ConnectionProblem.isTailscaleAddress("100.128.0.1"))
        assertEquals(false, ConnectionProblem.isTailscaleAddress("192.168.1.20"))
    }

    @Test
    fun `other failures are not taken for an unreachable server`() {
        assertEquals(null, problem(IOException("HTTP 500"), ServerOrigin("192.168.1.20", 8443)))
    }
}
