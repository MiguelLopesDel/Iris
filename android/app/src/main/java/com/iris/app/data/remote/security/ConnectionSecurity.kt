package com.iris.app.data.remote.security

import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import okhttp3.Connection
import okhttp3.Interceptor
import okhttp3.OkHttpClient
import java.io.IOException
import java.net.InetAddress
import java.net.Socket
import java.security.KeyStore
import java.security.MessageDigest
import java.security.cert.CertificateException
import java.security.cert.CertificateFactory
import java.security.cert.X509Certificate
import java.util.WeakHashMap
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.atomic.AtomicLong
import javax.net.ssl.SSLContext
import javax.net.ssl.SSLEngine
import javax.net.ssl.SSLHandshakeException
import javax.net.ssl.SSLSocket
import javax.net.ssl.SSLSocketFactory
import javax.net.ssl.TrustManagerFactory
import javax.net.ssl.X509ExtendedTrustManager
import javax.net.ssl.X509TrustManager

/** A server as the network sees it: scheme-independent host and port. */
data class ServerOrigin(val host: String, val port: Int) {
    val key: String get() = "${host.lowercase()}:$port"
    override fun toString(): String = key

    companion object {
        fun of(url: HttpUrl): ServerOrigin = ServerOrigin(url.host.lowercase(), url.port)
        fun of(url: String): ServerOrigin? = url.toHttpUrlOrNull()?.let(::of)
    }
}

/** Which certificates a server may present. */
enum class TrustMode {
    /** Certificates from public authorities only: Android's default. */
    SYSTEM,

    /** Also authorities the device owner installed (Settings > Security). */
    DEVICE_CAS,

    /** Only chains that lead to the certificates stored for this server. */
    PINNED,
}

/** How the app may talk to one server. Everything defaults to the strictest choice. */
data class ServerSecurity(
    val trustMode: TrustMode = TrustMode.SYSTEM,
    /** DER-encoded anchors for [TrustMode.PINNED]: a private CA or a self-signed certificate. */
    val pinnedCertificates: List<ByteArray> = emptyList(),
    /** Unencrypted HTTP, allowed only after the user accepted it for this server. */
    val cleartextAllowed: Boolean = false,
) {
    override fun equals(other: Any?): Boolean =
        other is ServerSecurity && trustMode == other.trustMode && cleartextAllowed == other.cleartextAllowed &&
            pinnedCertificates.size == other.pinnedCertificates.size &&
            pinnedCertificates.zip(other.pinnedCertificates).all { (a, b) -> a.contentEquals(b) }

    override fun hashCode(): Int =
        listOf(trustMode, cleartextAllowed, pinnedCertificates.map { it.contentHashCode() }).hashCode()
}

/** Persists [ServerSecurity] per [ServerOrigin]. Reads must be fast: they happen during TLS handshakes. */
interface ServerSecurityStore {
    fun get(origin: ServerOrigin): ServerSecurity
    fun put(origin: ServerOrigin, security: ServerSecurity)
}

class InMemoryServerSecurityStore : ServerSecurityStore {
    private val entries = ConcurrentHashMap<String, ServerSecurity>()
    override fun get(origin: ServerOrigin): ServerSecurity = entries[origin.key] ?: ServerSecurity()
    override fun put(origin: ServerOrigin, security: ServerSecurity) {
        entries[origin.key] = security
    }
}

/** HTTP to a server whose user has not accepted unencrypted traffic. */
class CleartextNotPermittedException(val origin: ServerOrigin) :
    IOException("Conexão sem criptografia (HTTP) com $origin não autorizada")

/**
 * Connection policy shared by every HTTP client of the app: API calls,
 * thumbnails, video and downloads all go through [apply]ed clients, so one
 * decision per server covers the whole app.
 */
open class ConnectionSecurity(
    private val store: ServerSecurityStore,
    private val systemTrust: X509TrustManager = defaultTrustManager(null),
    private val deviceCaStore: () -> KeyStore? = ::androidCaStore,
) {
    /**
     * A throwaway copy that applies [candidate] to [origin] and the saved policy
     * elsewhere, with nothing persisted: to try a policy before committing to it.
     */
    fun trying(origin: ServerOrigin, candidate: ServerSecurity): ConnectionSecurity {
        val overlay = object : ServerSecurityStore {
            override fun get(origin2: ServerOrigin) = if (origin2 == origin) candidate else store.get(origin2)
            override fun put(origin2: ServerOrigin, security: ServerSecurity) =
                throw UnsupportedOperationException("A trial policy is never saved")
        }
        return ConnectionSecurity(overlay, systemTrust, deviceCaStore)
    }

    private val rejected = ConcurrentHashMap<String, RejectedChain>()
    private val trustManager = PolicyTrustManager(
        policyFor = ::securityFor,
        system = systemTrust,
        deviceCas = lazy { deviceCaStore()?.let { defaultTrustManager(it) } },
        onRejected = { origin, chain, authType -> rejected[origin.key] = RejectedChain(chain, authType) },
    )
    // Replaced whenever a policy changes: a resumed TLS session skips certificate
    // validation, so the old session cache must not outlive the trust that filled it.
    @Volatile
    private var sslContext: SSLContext = newContext()
    private val socketFactory = DelegatingSslSocketFactory { sslContext.socketFactory }

    private fun newContext(): SSLContext = SSLContext.getInstance("TLS").apply {
        init(null, arrayOf(trustManager), null)
    }

    open fun securityFor(origin: ServerOrigin): ServerSecurity = store.get(origin)

    /**
     * Changes the policy of [origin]. Takes effect at once: the TLS session cache is
     * dropped and every connection the app opened is closed, including ones in use
     * (a playing video reconnects). Closing a TLS connection writes to the network:
     * call this off the main thread.
     */
    fun update(origin: ServerOrigin, change: (ServerSecurity) -> ServerSecurity) {
        store.put(origin, change(store.get(origin)))
        trustManager.forgetCachedAnchors()
        sslContext = newContext()
        generation.incrementAndGet()
        rejected.remove(origin.key)
        val open = synchronized(connections) { connections.keys.toList().also { connections.clear() } }
        open.forEach { runCatching { it.socket().close() } }
    }

    // Connections seen by the guard, with the policy generation they were last checked under.
    private val connections = WeakHashMap<Connection, Long>()
    private val generation = AtomicLong()

    /** The chain a server presented the last time it was refused, for the user to inspect. */
    fun lastRejectedChain(origin: ServerOrigin): List<X509Certificate>? = rejected[origin.key]?.chain

    /** Whether the chain [origin] last presented is trusted by the authorities installed on the device. */
    fun deviceCasTrustLastRejected(origin: ServerOrigin): Boolean =
        rejected[origin.key]?.let { trustManager.validatesWithDeviceCas(it.chain, it.authType) } ?: false

    /** Whether [certificate] is an authority the chain [origin] last presented leads to. */
    fun anchorsLastRejected(origin: ServerOrigin, certificate: X509Certificate): Boolean =
        rejected[origin.key]?.let { trustManager.validatesWithAnchors(it.chain, it.authType, listOf(certificate.encoded)) }
            ?: false

    private data class RejectedChain(val chain: List<X509Certificate>, val authType: String)

    open fun apply(builder: OkHttpClient.Builder): OkHttpClient.Builder = builder
        .sslSocketFactory(socketFactory, trustManager)
        // Refuses HTTP before anything is dialled.
        .addInterceptor(cleartextGuard)
        // Runs for every exchange on the network: after each redirect and on every
        // reused (possibly multiplexed) connection, which the line above never sees.
        .addNetworkInterceptor(exchangeGuard)

    private val cleartextGuard = Interceptor { chain ->
        requireCleartextConsent(chain.request().url)
        chain.proceed(chain.request())
    }

    private val exchangeGuard = Interceptor { chain ->
        val url = chain.request().url
        requireCleartextConsent(url)
        chain.connection()?.let { connection -> checkConnection(connection, url) }
        chain.proceed(chain.request())
    }

    private fun requireCleartextConsent(url: HttpUrl) {
        if (!url.isHttps && !securityFor(ServerOrigin.of(url)).cleartextAllowed) {
            throw CleartextNotPermittedException(ServerOrigin.of(url))
        }
    }

    /**
     * A connection is used only under the policy it satisfies now: one opened before
     * the policy changed is checked again (once per change) before carrying a request.
     */
    private fun checkConnection(connection: Connection, url: HttpUrl) {
        val current = generation.get()
        if (synchronized(connections) { connections[connection] } == current) return
        val session = (connection.socket() as? SSLSocket)?.session
        if (url.isHttps && session != null) {
            // The chain as received. OkHttp's Handshake.peerCertificates is "cleaned" against
            // the system authorities only and comes back empty for a private CA.
            val chain = runCatching { session.peerCertificates.filterIsInstance<X509Certificate>() }
                .getOrDefault(emptyList())
            try {
                trustManager.revalidate(ServerOrigin.of(url), chain)
            } catch (failure: CertificateException) {
                runCatching { connection.socket().close() }
                throw SSLHandshakeException("Connection no longer trusted for ${ServerOrigin.of(url)}")
                    .apply { initCause(failure) }
            }
        }
        synchronized(connections) { connections[connection] = current }
    }

    companion object {
        /** No per-server policy: Android's default TLS trust and HTTP allowed. Tests and tools only. */
        val Unrestricted: ConnectionSecurity = object : ConnectionSecurity(InMemoryServerSecurityStore()) {
            override fun securityFor(origin: ServerOrigin) = ServerSecurity(cleartextAllowed = true)
            override fun apply(builder: OkHttpClient.Builder) = builder
        }

        fun defaultTrustManager(keyStore: KeyStore?): X509TrustManager {
            val factory = TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm())
            factory.init(keyStore)
            return factory.trustManagers.filterIsInstance<X509TrustManager>().first()
        }

        /** System plus user-installed authorities; absent off Android. */
        fun androidCaStore(): KeyStore? = runCatching {
            KeyStore.getInstance("AndroidCAStore").apply { load(null) }
        }.getOrNull()

        fun parseCertificates(bytes: ByteArray): List<X509Certificate> =
            CertificateFactory.getInstance("X.509")
                .generateCertificates(bytes.inputStream())
                .filterIsInstance<X509Certificate>()

        fun sha256(certificate: X509Certificate): String =
            MessageDigest.getInstance("SHA-256").digest(certificate.encoded)
                .joinToString(":") { "%02X".format(it.toInt() and 0xff) }

        fun isSelfSigned(certificate: X509Certificate): Boolean =
            certificate.subjectX500Principal == certificate.issuerX500Principal &&
                runCatching { certificate.verify(certificate.publicKey) }.isSuccess

        /**
         * Addresses only reachable from a private network: loopback, RFC 1918,
         * carrier-grade NAT (where Tailscale and similar meshes live), link-local
         * and IPv6 unique-local, plus names reserved for local networks.
         * Decided from the address as written; nothing is resolved.
         */
        fun isPrivateAddress(host: String): Boolean {
            val name = host.lowercase().trim('[', ']')
            if (name == "localhost" || name.endsWith(".local") || name.endsWith(".lan") ||
                name.endsWith(".home.arpa") || name.endsWith(".internal")
            ) return true
            val literal = name.all { it.isDigit() || it == '.' } || name.contains(':')
            if (!literal) return false
            val address = runCatching { InetAddress.getByName(name) }.getOrNull() ?: return false
            val bytes = address.address.map { it.toInt() and 0xff }
            return when (bytes.size) {
                4 -> bytes[0] == 10 || bytes[0] == 127 ||
                    (bytes[0] == 172 && bytes[1] in 16..31) ||
                    (bytes[0] == 192 && bytes[1] == 168) ||
                    (bytes[0] == 100 && bytes[1] in 64..127) ||
                    (bytes[0] == 169 && bytes[1] == 254)
                16 -> address.isLoopbackAddress || address.isLinkLocalAddress || (bytes[0] and 0xfe) == 0xfc
                else -> false
            }
        }
    }
}

/**
 * Chooses, at each handshake, the trust rules of the server being contacted.
 * Hostname verification is not touched: OkHttp still checks that the
 * certificate names the address, whatever the trust mode.
 */
internal class PolicyTrustManager(
    private val policyFor: (ServerOrigin) -> ServerSecurity,
    private val system: X509TrustManager,
    private val deviceCas: Lazy<X509TrustManager?>,
    private val onRejected: (ServerOrigin, List<X509Certificate>, String) -> Unit,
) : X509ExtendedTrustManager() {
    private val pinnedManagers = ConcurrentHashMap<List<String>, X509TrustManager>()
    private val acceptedAuthTypes = ConcurrentHashMap<String, String>()

    /** Checks an already established chain against [origin]'s current policy. */
    fun revalidate(origin: ServerOrigin, chain: List<X509Certificate>) {
        if (chain.isEmpty()) throw CertificateException("No certificates for $origin")
        // The key-exchange type the handshake reported; TLS 1.3 reports a generic one.
        val authType = acceptedAuthTypes[origin.key] ?: "UNKNOWN"
        verify(chain.toTypedArray(), authType, origin.host, origin.port) { manager ->
            manager.checkServerTrusted(chain.toTypedArray(), authType)
        }
    }

    fun forgetCachedAnchors() = pinnedManagers.clear()

    fun validatesWithDeviceCas(chain: List<X509Certificate>, authType: String): Boolean {
        val manager = deviceCas.value ?: return false
        return runCatching { manager.checkServerTrusted(chain.toTypedArray(), authType) }.isSuccess
    }

    fun validatesWithAnchors(chain: List<X509Certificate>, authType: String, anchors: List<ByteArray>): Boolean =
        runCatching { pinnedManager(anchors).checkServerTrusted(chain.toTypedArray(), authType) }.isSuccess

    override fun checkServerTrusted(chain: Array<X509Certificate>, authType: String, socket: Socket?) {
        val session = (socket as? SSLSocket)?.handshakeSession
        verify(chain, authType, session?.peerHost, session?.peerPort ?: -1) { manager ->
            if (manager is X509ExtendedTrustManager) manager.checkServerTrusted(chain, authType, socket)
            else manager.checkServerTrusted(chain, authType)
        }
    }

    override fun checkServerTrusted(chain: Array<X509Certificate>, authType: String, engine: SSLEngine?) {
        val session = engine?.handshakeSession
        verify(chain, authType, session?.peerHost, session?.peerPort ?: -1) { manager ->
            if (manager is X509ExtendedTrustManager) manager.checkServerTrusted(chain, authType, engine)
            else manager.checkServerTrusted(chain, authType)
        }
    }

    override fun checkServerTrusted(chain: Array<X509Certificate>, authType: String) =
        verify(chain, authType, null, -1) { it.checkServerTrusted(chain, authType) }

    private fun verify(
        chain: Array<X509Certificate>,
        authType: String,
        host: String?,
        port: Int,
        check: (X509TrustManager) -> Unit,
    ) {
        val origin = if (host != null && port > 0) ServerOrigin(host.lowercase(), port) else null
        val security = origin?.let(policyFor) ?: ServerSecurity()
        try {
            when (security.trustMode) {
                TrustMode.SYSTEM -> check(system)
                TrustMode.DEVICE_CAS -> check(deviceCas.value ?: system)
                TrustMode.PINNED -> {
                    val anchors = security.pinnedCertificates
                    if (anchors.isEmpty()) throw CertificateException("Nenhum certificado fixado para $origin")
                    pinnedManager(anchors).checkServerTrusted(chain, authType)
                }
            }
            if (origin != null) acceptedAuthTypes[origin.key] = authType
        } catch (failure: CertificateException) {
            if (origin != null) onRejected(origin, chain.toList(), authType)
            throw failure
        }
    }

    private fun pinnedManager(anchors: List<ByteArray>): X509TrustManager {
        val key = anchors.map { der ->
            MessageDigest.getInstance("SHA-256").digest(der).joinToString("") { "%02x".format(it.toInt() and 0xff) }
        }
        return pinnedManagers.getOrPut(key) {
            val store = KeyStore.getInstance(KeyStore.getDefaultType()).apply { load(null) }
            anchors.forEachIndexed { index, der ->
                ConnectionSecurity.parseCertificates(der).forEach { store.setCertificateEntry("pinned-$index", it) }
            }
            ConnectionSecurity.defaultTrustManager(store)
        }
    }

    override fun checkClientTrusted(chain: Array<X509Certificate>, authType: String, socket: Socket?) =
        throw CertificateException("Client certificates are not used")

    override fun checkClientTrusted(chain: Array<X509Certificate>, authType: String, engine: SSLEngine?) =
        throw CertificateException("Client certificates are not used")

    override fun checkClientTrusted(chain: Array<X509Certificate>, authType: String) =
        throw CertificateException("Client certificates are not used")

    override fun getAcceptedIssuers(): Array<X509Certificate> = system.acceptedIssuers
}

/** An [SSLSocketFactory] that always uses the current [SSLContext]: clients keep one factory for life. */
internal class DelegatingSslSocketFactory(private val current: () -> SSLSocketFactory) : SSLSocketFactory() {
    override fun getDefaultCipherSuites(): Array<String> = current().defaultCipherSuites
    override fun getSupportedCipherSuites(): Array<String> = current().supportedCipherSuites
    override fun createSocket(): Socket = current().createSocket()
    override fun createSocket(socket: Socket, host: String, port: Int, autoClose: Boolean): Socket =
        current().createSocket(socket, host, port, autoClose)
    override fun createSocket(host: String, port: Int): Socket = current().createSocket(host, port)
    override fun createSocket(host: String, port: Int, localHost: InetAddress, localPort: Int): Socket =
        current().createSocket(host, port, localHost, localPort)
    override fun createSocket(host: InetAddress, port: Int): Socket = current().createSocket(host, port)
    override fun createSocket(address: InetAddress, port: Int, localAddress: InetAddress, localPort: Int): Socket =
        current().createSocket(address, port, localAddress, localPort)
}
