package com.iris.app.data.remote.security

import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import okhttp3.tls.HandshakeCertificates
import okhttp3.tls.HeldCertificate
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.net.URLEncoder

class PairingTest {
    private val servers = mutableListOf<MockWebServer>()
    private val store = InMemoryServerSecurityStore()
    private val security = ConnectionSecurity(store, systemTrust = ConnectionSecurity.defaultTrustManager(null), deviceCaStore = { null })
    private val id = "0123456789abcdef0123456789abcdef"

    // A private CA that sends leaf + intermediate, never the root: what the code's CA is for.
    private val root = HeldCertificate.Builder().commonName("Pairing Root").certificateAuthority(1).build()

    @After
    fun stop() = servers.forEach { runCatching { it.shutdown() } }

    private fun server(instance: String, https: Boolean = true, caPem: String = root.certificatePem()): MockWebServer {
        val server = MockWebServer()
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest) = when (request.path) {
                "/healthz" -> MockResponse().setBody("""{"status":"ok","mode":"multiuser","instance_id":"$instance"}""")
                "/api/pairing/ca.pem" -> MockResponse().setBody(caPem)
                else -> MockResponse().setResponseCode(404)
            }
        }
        server.start()
        if (https) {
            val intermediate = HeldCertificate.Builder().commonName("Intermediate").certificateAuthority(0).signedBy(root).build()
            val leaf = HeldCertificate.Builder().addSubjectAlternativeName(server.hostName).signedBy(intermediate).build()
            server.useHttps(HandshakeCertificates.Builder().heldCertificate(leaf, intermediate.certificate).build().sslSocketFactory(), false)
        }
        servers += server
        return server
    }

    private fun address(server: MockWebServer, https: Boolean = true) =
        server.url("/").newBuilder().scheme(if (https) "https" else "http").build().toString().trimEnd('/')

    private fun code(vararg addresses: String, ca: String? = PairingConnector.sha256Hex(root.certificate.encoded)): PairingCode {
        val query = buildList {
            add("v=1"); add("id=$id")
            addresses.forEach { add("u=" + URLEncoder.encode(it, "UTF-8")) }
            ca?.let { add("ca=$it") }
        }.joinToString("&")
        return PairingCode.parse("iris://pair?$query")
    }

    @Test
    fun `a pairing link is read as the server writes it`() {
        val parsed = PairingCode.parse(
            "iris://pair?v=1&id=$id&u=http%3A%2F%2F192.168.1.20%3A8501&u=https%3A%2F%2Firis.example&ca=" + "a".repeat(64)
        )
        assertEquals(listOf("http://192.168.1.20:8501", "https://iris.example"), parsed.addresses)
        assertEquals("a".repeat(64), parsed.caSha256)
        assertTrue(parsed.usesCleartext)
        for (bad in listOf(
            "https://iris.example", "iris://pair?v=2&id=$id&u=https%3A%2F%2Fx", "iris://pair?v=1&u=https%3A%2F%2Fx",
            "iris://pair?v=1&id=$id", "iris://pair?v=1&id=$id&u=ftp%3A%2F%2Fx", "iris://pair?v=1&id=$id&u=https%3A%2F%2Fx%2Fapp",
            "iris://pair?v=1&id=$id&u=https%3A%2F%2Fx&ca=zz",
            "iris://pair?v=1&id=$id&u=https%3A%2F%2Fuser%3Asecret%40x", "iris://pair?v=1&id=$id&u=https%3A%2F%2Fuser%40x",
            "iris://pair?v=1&id=$id&u=https%3A%2F%2Fx%23frag",
        )) {
            try {
                PairingCode.parse(bad); fail("accepted $bad")
            } catch (_: PairingCodeException) {
            }
        }
    }

    @Test
    fun `the code's authority is fetched, checked and trusted for the server`() {
        val server = server(id)
        val result = PairingConnector(security).connect(code(address(server)), allowCleartext = false)
        assertEquals(address(server), result.address)
        val pinned = store.get(ServerOrigin.of(server.url("/")))
        assertEquals(TrustMode.PINNED, pinned.trustMode)
        assertTrue(pinned.pinnedCertificates.single().contentEquals(root.certificate.encoded))
    }

    @Test
    fun `an authority that does not match the code changes nothing`() {
        val impostor = HeldCertificate.Builder().commonName("Impostor").certificateAuthority(1).build()
        val server = server(id, caPem = impostor.certificatePem())
        try {
            PairingConnector(security).connect(code(address(server)), allowCleartext = false)
            fail("an authority with another fingerprint must be refused")
        } catch (error: PairingAuthorityException) {
            assertTrue(error.message!!.contains("não confere"))
        }
        assertEquals(ServerSecurity(), store.get(ServerOrigin.of(server.url("/"))))
    }

    @Test
    fun `addresses are tried in order and another server is not taken for this one`() {
        val other = server("f".repeat(32))
        val unreachable = "https://127.0.0.1:1"
        val right = server(id)
        val result = PairingConnector(security, timeoutSeconds = 2)
            .connect(code(address(other), unreachable, address(right)), allowCleartext = false)
        assertEquals(address(right), result.address)
        assertTrue(result.outcomes[0] is AddressOutcome.OtherServer)
        assertTrue(result.outcomes[1] is AddressOutcome.Failed)
        assertTrue(result.outcomes[2] is AddressOutcome.Connected)
        // Only the address that proved to be this server keeps the code's authority.
        assertEquals(ServerSecurity(), store.get(ServerOrigin.of(other.url("/"))))
        assertEquals(ServerSecurity(), store.get(ServerOrigin.of(unreachable)!!))
        assertEquals(TrustMode.PINNED, store.get(ServerOrigin.of(right.url("/"))).trustMode)
    }

    @Test
    fun `a failed pairing leaves every address as it was`() {
        val other = server("f".repeat(32))
        val plainOther = server("e".repeat(32), https = false)
        val result = PairingConnector(security, timeoutSeconds = 2)
            .connect(code(address(other), address(plainOther, https = false)), allowCleartext = true)
        assertNull(result.address)
        assertEquals(ServerSecurity(), store.get(ServerOrigin.of(other.url("/"))))
        assertEquals("http stays refused where pairing failed", ServerSecurity(), store.get(ServerOrigin.of(plainOther.url("/"))))
    }

    @Test
    fun `http addresses are used only when the user accepted http`() {
        val plain = server(id, https = false)
        val plainAddress = address(plain, https = false)
        val refused = PairingConnector(security).connect(code(plainAddress, ca = null), allowCleartext = false)
        assertNull(refused.address)
        assertTrue(refused.outcomes.single() is AddressOutcome.Skipped)
        assertEquals("nothing was contacted", 0, plain.requestCount)

        val accepted = PairingConnector(security).connect(code(plainAddress), allowCleartext = true)
        assertEquals(plainAddress, accepted.address)
        assertTrue(store.get(ServerOrigin.of(plain.url("/"))).cleartextAllowed)
    }
}
