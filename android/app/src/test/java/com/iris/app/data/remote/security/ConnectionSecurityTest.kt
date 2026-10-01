package com.iris.app.data.remote.security

import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.tls.HandshakeCertificates
import okhttp3.tls.HeldCertificate
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import kotlin.concurrent.thread
import java.io.IOException
import java.net.InetAddress
import java.net.ServerSocket
import java.security.KeyStore
import java.security.cert.X509Certificate

/**
 * Real TLS handshakes against a local server, one test per deployment shape
 * the connectivity guide describes.
 */
class ConnectionSecurityTest {
    private val servers = mutableListOf<MockWebServer>()
    private val store = InMemoryServerSecurityStore()

    @After
    fun stopServers() = servers.forEach { runCatching { it.shutdown() } }

    // A private CA in the shape Caddy's "tls internal" uses: a long-lived root,
    // short-lived intermediates and leaves, and only leaf + intermediate sent.
    private val root = authority("Private Root")
    private val otherRoot = authority("Unrelated Root")

    private fun authority(name: String, signer: HeldCertificate? = null) = HeldCertificate.Builder()
        .commonName(name)
        .certificateAuthority(if (signer == null) 1 else 0)
        .apply { if (signer != null) signedBy(signer) }
        .build()

    private fun leaf(host: String, signer: HeldCertificate?) = HeldCertificate.Builder()
        .commonName(host)
        .addSubjectAlternativeName(host)
        .apply { if (signer != null) signedBy(signer) }
        .build()

    /** Serves a leaf issued by a fresh intermediate of [issuerRoot] (self-signed when null). */
    private fun MockWebServer.issue(issuerRoot: HeldCertificate?, name: String? = null) {
        val intermediate = issuerRoot?.let { authority("Intermediate", it) }
        val leaf = leaf(name ?: hostName, intermediate)
        val chain = listOfNotNull(intermediate?.certificate).toTypedArray()
        useHttps(HandshakeCertificates.Builder().heldCertificate(leaf, *chain).build().sslSocketFactory(), false)
    }

    private fun httpsServer(issuerRoot: HeldCertificate?, name: String? = null): MockWebServer {
        val server = MockWebServer()
        server.start()
        server.issue(issuerRoot, name)
        repeat(4) { server.enqueue(MockResponse().setBody("ok")) }
        servers += server
        return server
    }

    private fun security(deviceRoots: List<X509Certificate> = emptyList()) = ConnectionSecurity(
        store,
        // The JVM's public authorities: none of them signed the test certificates.
        systemTrust = ConnectionSecurity.defaultTrustManager(null),
        deviceCaStore = {
            KeyStore.getInstance(KeyStore.getDefaultType()).apply {
                load(null)
                deviceRoots.forEachIndexed { i, cert -> setCertificateEntry("user-$i", cert) }
            }
        },
    )

    private fun ConnectionSecurity.client(): OkHttpClient = apply(OkHttpClient.Builder()).build()

    private fun ConnectionSecurity.get(server: MockWebServer, https: Boolean = true): String {
        val url = server.url("/healthz").newBuilder().scheme(if (https) "https" else "http").build()
        return client().newCall(Request.Builder().url(url).build()).execute().use { it.body!!.string() }
    }

    private fun ConnectionSecurity.failure(server: MockWebServer, https: Boolean = true): ConnectionProblem? {
        try {
            get(server, https)
        } catch (error: IOException) {
            return ConnectionProblem.from(error, ServerOrigin.of(server.url("/")), this)
        }
        fail("the connection should have been refused")
        return null
    }

    private fun origin(server: MockWebServer) = ServerOrigin.of(server.url("/"))

    @Test
    fun `by default only public authorities are trusted and the refused chain is kept for the user`() {
        val server = httpsServer(root)
        val problem = security().failure(server) as ConnectionProblem.UntrustedCertificate
        assertEquals(2, problem.chain.size) // leaf + intermediate: the root never travels
        assertFalse("an intermediate must not be offered for pinning", problem.canTrustPresentedCertificate)
        assertFalse(problem.trustedByDeviceCas)
    }

    @Test
    fun `authorities installed on the device are used only when chosen for that server`() {
        val server = httpsServer(root)
        val security = security(deviceRoots = listOf(root.certificate))
        val problem = security.failure(server) as ConnectionProblem.UntrustedCertificate
        assertTrue("the user is told the device already trusts it", problem.trustedByDeviceCas)

        security.update(origin(server)) { it.copy(trustMode = TrustMode.DEVICE_CAS) }
        assertEquals("ok", security.get(server))
    }

    @Test
    fun `an imported root keeps working when leaf and intermediate are reissued`() {
        val security = security()
        val first = httpsServer(root)
        security.update(origin(first)) {
            it.copy(trustMode = TrustMode.PINNED, pinnedCertificates = listOf(root.certificate.encoded))
        }
        assertEquals("ok", security.get(first))

        first.issue(root) // new intermediate and leaf, same root: what a private CA does every few hours
        assertEquals("ok", security.get(first))
    }

    @Test
    fun `a pinned server refuses a chain from any other authority`() {
        val server = httpsServer(otherRoot)
        val security = security()
        security.update(origin(server)) {
            it.copy(trustMode = TrustMode.PINNED, pinnedCertificates = listOf(root.certificate.encoded))
        }
        assertTrue(security.failure(server) is ConnectionProblem.UntrustedCertificate)
    }

    @Test
    fun `a self-signed certificate can be trusted as presented`() {
        val server = httpsServer(issuerRoot = null)
        val security = security()
        val problem = security.failure(server) as ConnectionProblem.UntrustedCertificate
        assertTrue(problem.canTrustPresentedCertificate)

        security.update(origin(server)) {
            it.copy(trustMode = TrustMode.PINNED, pinnedCertificates = listOf(problem.chain.last().encoded))
        }
        assertEquals("ok", security.get(server))
    }

    @Test
    fun `trust is per server`() {
        val trusted = httpsServer(root)
        val other = httpsServer(root)
        val security = security()
        security.update(origin(trusted)) {
            it.copy(trustMode = TrustMode.PINNED, pinnedCertificates = listOf(root.certificate.encoded))
        }
        assertEquals("ok", security.get(trusted))
        assertTrue(security.failure(other) is ConnectionProblem.UntrustedCertificate)
    }

    @Test
    fun `trust never overrides a certificate that names another address`() {
        val server = httpsServer(root, name = "iris.example")
        val security = security()
        security.update(origin(server)) {
            it.copy(trustMode = TrustMode.PINNED, pinnedCertificates = listOf(root.certificate.encoded))
        }
        assertTrue(security.failure(server) is ConnectionProblem.NameMismatch)
    }

    @Test
    fun `https to a port that speaks plain http is reported as such`() {
        // What an HTTP server (uvicorn, nginx) does with a TLS hello: answers 400 in plain text.
        ServerSocket(0, 1, InetAddress.getLoopbackAddress()).use { plain ->
            thread(isDaemon = true) {
                runCatching {
                    plain.accept().use { socket ->
                        socket.getInputStream().read(ByteArray(512))
                        socket.getOutputStream().write(
                            "HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".toByteArray()
                        )
                    }
                }
            }
            val url = "https://127.0.0.1:${plain.localPort}/healthz"
            val security = security()
            val problem = try {
                security.client().newCall(Request.Builder().url(url).build()).execute().close()
                null
            } catch (error: IOException) {
                ConnectionProblem.from(error, ServerOrigin("127.0.0.1", plain.localPort), security)
            }
            assertTrue("$problem", problem is ConnectionProblem.NotHttps)
        }
    }

    @Test
    fun `http needs the user's consent for that server`() {
        val plain = MockWebServer().also { it.start(); servers += it }
        repeat(2) { plain.enqueue(MockResponse().setBody("ok")) }
        val security = security()
        val problem = security.failure(plain, https = false) as ConnectionProblem.CleartextNotAllowed
        assertTrue(problem.privateAddress)
        assertEquals("nothing was sent before consent", 0, plain.requestCount)

        security.update(origin(plain)) { it.copy(cleartextAllowed = true) }
        assertEquals("ok", security.get(plain, https = false))
    }

    @Test
    fun `unrestricted keeps the previous behaviour for tests and tools`() {
        val plain = MockWebServer().also { it.start(); servers += it }
        plain.enqueue(MockResponse().setBody("ok"))
        assertEquals("ok", ConnectionSecurity.Unrestricted.get(plain, https = false))
    }

    @Test
    fun `private addresses are recognised without resolving names`() {
        listOf(
            "localhost", "127.0.0.1", "10.1.2.3", "172.16.0.1", "172.31.255.255", "192.168.0.10",
            "100.64.0.1", "100.99.219.81", "169.254.1.1", "nas.local", "server.lan", "box.home.arpa",
            "::1", "fd12:3456::1", "fe80::1",
        ).forEach { assertTrue(it, ConnectionSecurity.isPrivateAddress(it)) }
        listOf("8.8.8.8", "172.32.0.1", "100.128.0.1", "iris.example.com", "2001:db8::1")
            .forEach { assertFalse(it, ConnectionSecurity.isPrivateAddress(it)) }
    }

    @Test
    fun `revoking trust takes effect on connections already open`() {
        val server = httpsServer(root)
        val security = security(deviceRoots = listOf(root.certificate))
        val api = com.iris.app.data.remote.IrisApiClient(
            server.url("/").newBuilder().scheme("https").build().toString(),
            connectionSecurity = security,
        )
        val origin = origin(server)
        security.update(origin) { it.copy(trustMode = TrustMode.DEVICE_CAS) }
        val call = { api.authenticatedOkHttpClient.newCall(Request.Builder().url(api.baseUrl + "healthz").build()) }
        assertEquals("ok", call().execute().use { it.body!!.string() })

        security.update(origin) { ServerSecurity() }
        api.resetConnections()
        try {
            call().execute().close()
            fail("a pooled connection must not outlive the trust that accepted it")
        } catch (error: IOException) {
            assertTrue(ConnectionProblem.from(error, origin, security) is ConnectionProblem.UntrustedCertificate)
        }
    }

    @Test
    fun `a redirect cannot reach http without consent for the target`() {
        val target = MockWebServer().also { it.start(); servers += it }
        target.enqueue(MockResponse().setBody("leaked"))
        val security = security()

        // https (trusted) -> http
        val secure = httpsServer(root)
        security.update(origin(secure)) {
            it.copy(trustMode = TrustMode.PINNED, pinnedCertificates = listOf(root.certificate.encoded))
        }
        // http (allowed) -> another http server (not allowed)
        val allowed = MockWebServer().also { it.start(); servers += it }
        security.update(origin(allowed)) { it.copy(cleartextAllowed = true) }
        allowed.enqueue(MockResponse().setResponseCode(302).setHeader("Location", target.url("/y")))

        val following = security.apply(OkHttpClient.Builder().followRedirects(true).followSslRedirects(true)).build()
        // Replace the queued "ok" answers: the redirect is what this server answers.
        secure.dispatcher = queueOf(MockResponse().setResponseCode(302).setHeader("Location", target.url("/x")))
        for (start in listOf(secure.url("/").newBuilder().scheme("https").build(), allowed.url("/"))) {
            try {
                following.newCall(Request.Builder().url(start).build()).execute().close()
                fail("followed a redirect to unconsented http from $start")
            } catch (error: IOException) {
                assertTrue("$error", generateSequence<Throwable>(error) { it.cause }.any { it is CleartextNotPermittedException })
            }
        }
        assertEquals("nothing reached the unconsented server", 0, target.requestCount)
    }

    @Test
    fun `revoking trust cuts connections in use and is not bypassed by multiplexing`() {
        val server = MockWebServer()
        server.protocols = listOf(okhttp3.Protocol.HTTP_2, okhttp3.Protocol.HTTP_1_1)
        server.start()
        servers += server
        server.issue(root)
        val slowBody = okio.Buffer().write(ByteArray(2_000_000))
        server.dispatcher = object : okhttp3.mockwebserver.Dispatcher() {
            override fun dispatch(request: okhttp3.mockwebserver.RecordedRequest) =
                if (request.path == "/video") MockResponse().setBody(slowBody.clone()).throttleBody(16_384, 50, java.util.concurrent.TimeUnit.MILLISECONDS)
                else MockResponse().setBody("ok")
        }
        val security = security()
        val origin = origin(server)
        security.update(origin) {
            it.copy(trustMode = TrustMode.PINNED, pinnedCertificates = listOf(root.certificate.encoded))
        }
        val client = security.client()
        val base = server.url("/").newBuilder().scheme("https")
        val video = client.newCall(Request.Builder().url(base.encodedPath("/video").build()).build()).execute()
        assertEquals(okhttp3.Protocol.HTTP_2, video.protocol)
        val stream = video.body!!.source()
        stream.require(16_384) // playing

        security.update(origin) { ServerSecurity() } // revoked
        val interrupted = try {
            stream.readByteArray(); false
        } catch (_: IOException) {
            true
        }
        assertTrue("the stream in use must be cut", interrupted)
        try {
            client.newCall(Request.Builder().url(base.encodedPath("/healthz").build()).build()).execute().close()
            fail("a new request must not ride the connection opened under the revoked trust")
        } catch (error: IOException) {
            assertTrue("$error", ConnectionProblem.from(error, origin, security) is ConnectionProblem.UntrustedCertificate)
        }
    }

    private fun queueOf(vararg responses: MockResponse) = okhttp3.mockwebserver.QueueDispatcher().apply {
        responses.forEach(::enqueueResponse)
    }
}

