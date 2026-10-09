package com.iris.app.data.remote.security

import okhttp3.OkHttpClient
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import okhttp3.tls.HandshakeCertificates
import okhttp3.tls.HeldCertificate
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.net.URLEncoder
import java.security.KeyPair
import java.security.KeyPairGenerator
import java.security.Signature
import java.security.spec.ECGenParameterSpec
import java.util.Base64
import java.util.concurrent.atomic.AtomicReference

/**
 * The app checks a server's identity key: against an answer produced by the
 * real server code, and against HTTPS servers, the paired one and an impostor
 * at the same kind of address.
 */
class ServerIdentityTest {
    private val servers = mutableListOf<MockWebServer>()
    private val store = InMemoryServerSecurityStore()
    private val security = ConnectionSecurity(store, systemTrust = ConnectionSecurity.defaultTrustManager(null), deviceCaStore = { null })
    private val id = "0123456789abcdef0123456789abcdef"

    @After
    fun stop() = servers.forEach { runCatching { it.shutdown() } }

    // Produced by the server's core/server_identity.py for a synthetic key:
    // answer_challenge(identity, "a" * 32, "n" * 32, "https://192.168.1.20:8501").
    private val serverAnswer = ServerIdentityVerifier.Answer(
        version = 1,
        algorithm = "ecdsa-p256-sha256",
        instance_id = "a".repeat(32),
        public_key = "MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEkMWmMIZEDKil9wiELMjiNZ0qshH63PopejfqyfAObJwxioiWRFuq2akmJKccb+aHtTEHZN+ZIg7HVfNQ2JVbkw==",
        key_sha256 = "5dca56b20c3222732d3711b6185e15a138941b2dc7af8954d7c68a38a1ac1b64",
        signature = "MEUCIGmB+xbxNyNfE/xg7o5oLT9a1v8qfgbcSlWBMmdS1DhzAiEA7W6Leu3G/BE4z7RVG0MHS6oXw9+YY5YF8+lTzYUS9nQ=",
    )
    private val serverKey = "5dca56b20c3222732d3711b6185e15a138941b2dc7af8954d7c68a38a1ac1b64"
    private val origin = ServerOrigin("192.168.1.20", 8501)

    @Test
    fun `an answer from the real server is accepted for its nonce, address and key`() {
        ServerIdentityVerifier.verify(origin, serverAnswer, "n".repeat(32), "https://192.168.1.20:8501", serverKey, "a".repeat(32))
    }

    @Test
    fun `the same answer proves nothing for another challenge, address, key or installation`() {
        val wrong = listOf(
            { ServerIdentityVerifier.verify(origin, serverAnswer, "m".repeat(32), "https://192.168.1.20:8501", serverKey, null) },
            { ServerIdentityVerifier.verify(origin, serverAnswer, "n".repeat(32), "https://192.168.1.21:8501", serverKey, null) },
            { ServerIdentityVerifier.verify(origin, serverAnswer, "n".repeat(32), "https://192.168.1.20:8501", "b".repeat(64), null) },
            { ServerIdentityVerifier.verify(origin, serverAnswer, "n".repeat(32), "https://192.168.1.20:8501", serverKey, "c".repeat(32)) },
        )
        wrong.forEach { check ->
            try {
                check(); fail("accepted a forged identity")
            } catch (_: ServerIdentityMismatchException) {
            }
        }
    }

    @Test
    fun `a pairing code carries the identity key`() {
        val code = PairingCode.parse("iris://pair?v=1&id=$id&u=https%3A%2F%2Firis.example&k=$serverKey")
        assertEquals(serverKey, code.keySha256)
        assertNull(PairingCode.parse("iris://pair?v=1&id=$id&u=https%3A%2F%2Firis.example").keySha256)
        try {
            PairingCode.parse("iris://pair?v=1&id=$id&u=https%3A%2F%2Firis.example&k=zz"); fail("accepted a bad key")
        } catch (_: PairingCodeException) {
        }
    }

    @Test
    fun `pairing a different installation ends the old device session`() {
        assertTrue(PairingSessionPolicy.shouldEndSession(id, "b".repeat(32)))
        assertFalse(PairingSessionPolicy.shouldEndSession(id, id))
        assertFalse(PairingSessionPolicy.shouldEndSession(null, "b".repeat(32)))
    }

    // --- An HTTPS server with its own identity, as IRIS_TLS=self serves it -------

    private fun ecKeyPair(): KeyPair =
        KeyPairGenerator.getInstance("EC").apply { initialize(ECGenParameterSpec("secp256r1")) }.generateKeyPair()

    private fun keySha256(keys: KeyPair) = ConnectionSecurity.keySha256(keys.public.encoded)

    /** A server whose certificate and identity answers both use [keys]; it names no host at all. */
    private fun irisServer(keys: KeyPair, instance: String = id): MockWebServer {
        val server = MockWebServer()
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse = when (request.requestUrl?.encodedPath) {
                "/healthz" -> MockResponse().setBody("""{"status":"ok","mode":"multiuser","instance_id":"$instance"}""")
                "/api/identity" -> {
                    val nonce = request.requestUrl!!.queryParameter("nonce")!!
                    val address = request.requestUrl!!.queryParameter("address")!!
                    val signature = Signature.getInstance("SHA256withECDSA").run {
                        initSign(keys.private)
                        update(ServerIdentityVerifier.message(instance, nonce, address))
                        sign()
                    }
                    val encoder = Base64.getEncoder()
                    MockResponse().setBody(
                        """{"version":1,"algorithm":"ecdsa-p256-sha256","instance_id":"$instance",""" +
                            """"public_key":"${encoder.encodeToString(keys.public.encoded)}","key_sha256":"${keySha256(keys)}",""" +
                            """"signature":"${encoder.encodeToString(signature)}"}"""
                    )
                }
                else -> MockResponse().setResponseCode(404)
            }
        }
        server.start()
        // Self-signed with the identity key, naming an unrelated host: only the key can make it trusted.
        val certificate = HeldCertificate.Builder().keyPair(keys).commonName("Iris").addSubjectAlternativeName("iris.invalid").build()
        server.useHttps(HandshakeCertificates.Builder().heldCertificate(certificate).build().sslSocketFactory(), false)
        servers += server
        return server
    }

    /** A TLS-terminating proxy certificate that is independent from the Iris identity key. */
    private fun proxyServer(identityKeys: AtomicReference<KeyPair>): Pair<MockWebServer, HeldCertificate> {
        val server = MockWebServer()
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                if (request.requestUrl?.encodedPath != "/api/identity") return MockResponse().setResponseCode(404)
                val keys = identityKeys.get()
                val nonce = request.requestUrl!!.queryParameter("nonce")!!
                val address = request.requestUrl!!.queryParameter("address")!!
                val signature = Signature.getInstance("SHA256withECDSA").run {
                    initSign(keys.private)
                    update(ServerIdentityVerifier.message(id, nonce, address))
                    sign()
                }
                val encoder = Base64.getEncoder()
                return MockResponse().setBody(
                    """{"version":1,"algorithm":"ecdsa-p256-sha256","instance_id":"$id",""" +
                        """"public_key":"${encoder.encodeToString(keys.public.encoded)}","key_sha256":"${keySha256(keys)}",""" +
                        """"signature":"${encoder.encodeToString(signature)}"}"""
                )
            }
        }
        server.start()
        val proxyCertificate = HeldCertificate.Builder()
            .commonName("TLS proxy")
            .addSubjectAlternativeName(server.hostName)
            .build()
        server.useHttps(
            HandshakeCertificates.Builder().heldCertificate(proxyCertificate).build().sslSocketFactory(),
            false,
        )
        servers += server
        return server to proxyCertificate
    }

    private fun address(server: MockWebServer) = server.url("/").toString().trimEnd('/')

    private fun code(address: String, key: String) =
        PairingCode.parse("iris://pair?v=1&id=$id&u=${URLEncoder.encode(address, "UTF-8")}&k=$key")

    @Test
    fun `pairing trusts the server's own certificate by its key and checks its signature`() {
        val keys = ecKeyPair()
        val server = irisServer(keys)

        val result = PairingConnector(security).connect(code(address(server), keySha256(keys)), allowCleartext = false)

        assertEquals(address(server), result.address)
        assertEquals(keySha256(keys), store.get(ServerOrigin.of(server.url("/"))).identityKeySha256)
    }

    @Test
    fun `an impostor with the same instance id but another key is refused and nothing is saved`() {
        val paired = ecKeyPair()
        val impostor = irisServer(ecKeyPair())

        val result = PairingConnector(security).connect(code(address(impostor), keySha256(paired)), allowCleartext = false)

        assertNull(result.address)
        assertNull(store.get(ServerOrigin.of(impostor.url("/"))).identityKeySha256)
    }

    @Test
    fun `before credentials go out, the paired key must answer and a success is remembered`() {
        val keys = ecKeyPair()
        val server = irisServer(keys)
        val url = server.url("/")
        store.put(ServerOrigin.of(url), ServerSecurity(identityKeySha256 = keySha256(keys)))
        val client = security.apply(OkHttpClient.Builder()).build()
        val verifier = ServerIdentityVerifier(client = { client })

        verifier.requireIdentity(url, keySha256(keys), id)
        verifier.requireIdentity(url, keySha256(keys), id)

        val asked = generateSequence { server.takeRequest(100, java.util.concurrent.TimeUnit.MILLISECONDS) }
            .count { it.requestUrl?.encodedPath == "/api/identity" }
        assertEquals(1, asked)
    }

    @Test
    fun `a proxy certificate never caches identity proof across a backend replacement`() {
        val pairedKeys = ecKeyPair()
        val activeBackendKey = AtomicReference(pairedKeys)
        val (proxy, proxyCertificate) = proxyServer(activeBackendKey)
        val url = proxy.url("/")
        val origin = ServerOrigin.of(url)
        val proxyTrust = ServerSecurity(
            trustMode = TrustMode.PINNED,
            pinnedCertificates = listOf(proxyCertificate.certificate.encoded),
            identityKeySha256 = keySha256(pairedKeys),
        )
        val client = security.trying(origin, proxyTrust).apply(OkHttpClient.Builder()).build()
        val verifier = ServerIdentityVerifier(client = { client })

        verifier.requireIdentity(url, keySha256(pairedKeys), id)
        activeBackendKey.set(ecKeyPair())

        try {
            verifier.requireIdentity(url, keySha256(pairedKeys), id)
            fail("a replacement backend must prove the paired identity again")
        } catch (_: ServerIdentityMismatchException) {
            // The proxy's HTTPS certificate stayed the same; only a fresh backend proof detects this.
        }
        val identityRequests = generateSequence { proxy.takeRequest(100, java.util.concurrent.TimeUnit.MILLISECONDS) }
            .count { it.requestUrl?.encodedPath == "/api/identity" }
        assertEquals(2, identityRequests)
    }

    @Test
    fun `a server holding another key at the paired address never gets credentials`() {
        val paired = ecKeyPair()
        val impostor = irisServer(ecKeyPair())
        val url = impostor.url("/")
        // The paired policy: its key, nothing else trusted for this address.
        store.put(ServerOrigin.of(url), ServerSecurity(identityKeySha256 = keySha256(paired)))
        val client = security.apply(OkHttpClient.Builder()).build()

        try {
            ServerIdentityVerifier(client = { client }).requireIdentity(url, keySha256(paired), id)
            fail("trusted an impostor")
        } catch (refused: java.io.IOException) {
            // Its certificate carries another key, so TLS itself refuses it before any request.
            assertTrue(refused is javax.net.ssl.SSLException || refused is ServerIdentityMismatchException)
        }
    }
}
