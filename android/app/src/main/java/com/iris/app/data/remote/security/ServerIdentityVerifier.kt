package com.iris.app.data.remote.security

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import okhttp3.HttpUrl
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.IOException
import java.security.KeyFactory
import java.security.cert.X509Certificate
import java.security.SecureRandom
import java.security.Signature
import java.security.spec.X509EncodedKeySpec
import java.util.Base64
import java.util.concurrent.ConcurrentHashMap

/** The server at an address did not prove it holds the identity key this app paired with. */
class ServerIdentityMismatchException(val origin: ServerOrigin, reason: String) :
    IOException("O servidor em $origin não provou ser o Iris pareado: $reason")

/**
 * Checks that a server holds the identity key named by the pairing code, before
 * the app sends it a token or a password.
 *
 * The app sends a fresh random nonce to `GET /api/identity` with the address it
 * is using; the server answers with its public key and an ECDSA P-256
 * signature over `iris-identity-v1`, its instance id, the nonce and the address.
 * The key must hash to the pinned fingerprint and the signature must hold. A
 * machine that took over the address does not have the private key and
 * cannot answer, so nothing is sent to it.
 *
 * A success is remembered for [ttlMillis] per address and key only when the
 * TLS leaf certificate uses that same key. With a proxy or custom TLS
 * certificate, the TLS connection authenticates the proxy, not the Iris
 * backend, so every request needs a fresh identity proof. A failure is never
 * remembered.
 */
class ServerIdentityVerifier(
    private val client: () -> OkHttpClient,
    private val nowMillis: () -> Long = System::currentTimeMillis,
    private val ttlMillis: Long = TTL_MILLIS,
    private val random: SecureRandom = SecureRandom(),
) {
    private val verifiedUntil = ConcurrentHashMap<String, Long>()

    /**
     * Returns when the server at [url]'s origin holds the key [expectedKeySha256]
     * (and, when known, has [expectedInstanceId]); throws
     * [ServerIdentityMismatchException] when it does not, or an [IOException]
     * when it cannot be asked. Blocking: call off the main thread.
     */
    fun requireIdentity(url: HttpUrl, expectedKeySha256: String, expectedInstanceId: String?) {
        val origin = ServerOrigin.of(url)
        val cacheKey = "${origin.key}|$expectedKeySha256"
        if ((verifiedUntil[cacheKey] ?: 0L) > nowMillis()) return

        val address = addressOf(url)
        val nonce = ByteArray(24).also(random::nextBytes).joinToString("") { "%02x".format(it.toInt() and 0xff) }
        val request = Request.Builder()
            .url(
                url.newBuilder().encodedPath("/api/identity").query(null)
                    .addQueryParameter("nonce", nonce)
                    .addQueryParameter("address", address)
                    .build()
            )
            .build()
        val (body, tlsUsesIdentityKey) = client().newCall(request).execute().use { response ->
            when {
                response.code == 404 -> throw ServerIdentityMismatchException(origin, "ele não sabe provar a identidade")
                !response.isSuccessful -> throw IOException("HTTP ${response.code} ao verificar a identidade")
                else -> {
                    val leaf = response.handshake?.peerCertificates?.firstOrNull() as? X509Certificate
                    val certificateKey = leaf?.publicKey?.encoded?.let { ConnectionSecurity.keySha256(it) }
                    response.body?.string().orEmpty() to (url.isHttps && certificateKey == expectedKeySha256)
                }
            }
        }
        val answer = runCatching { json.decodeFromString(Answer.serializer(), body) }
            .getOrElse { throw ServerIdentityMismatchException(origin, "resposta de identidade inválida") }
        verify(origin, answer, nonce, address, expectedKeySha256, expectedInstanceId)
        if (tlsUsesIdentityKey) {
            verifiedUntil[cacheKey] = nowMillis() + ttlMillis
        } else {
            verifiedUntil.remove(cacheKey)
        }
    }

    /** Drops what was verified, so the next request asks again (the policy changed). */
    fun forget() = verifiedUntil.clear()

    @Serializable
    internal data class Answer(
        val version: Int = 0,
        val algorithm: String = "",
        val instance_id: String = "",
        val public_key: String = "",
        val key_sha256: String = "",
        val signature: String = "",
    )

    companion object {
        const val TTL_MILLIS = 10 * 60 * 1000L
        private const val DOMAIN = "iris-identity-v1"
        private val json = Json { ignoreUnknownKeys = true }

        /** `scheme://host:port`, as the server expects it and signs it. */
        internal fun addressOf(url: HttpUrl): String {
            val host = if (url.host.contains(':')) "[${url.host}]" else url.host
            return "${url.scheme}://$host:${url.port}"
        }

        internal fun message(instanceId: String, nonce: String, address: String): ByteArray =
            listOf(DOMAIN, instanceId, nonce, address).joinToString("\n").toByteArray(Charsets.UTF_8)

        /** Checks an answer; throws [ServerIdentityMismatchException] saying what is wrong. */
        internal fun verify(
            origin: ServerOrigin,
            answer: Answer,
            nonce: String,
            address: String,
            expectedKeySha256: String,
            expectedInstanceId: String?,
        ) {
            if (answer.version != 1 || answer.algorithm != "ecdsa-p256-sha256") {
                throw ServerIdentityMismatchException(origin, "formato de identidade desconhecido")
            }
            val publicKey = runCatching { Base64.getDecoder().decode(answer.public_key) }
                .getOrElse { throw ServerIdentityMismatchException(origin, "chave ilegível") }
            if (ConnectionSecurity.keySha256(publicKey) != expectedKeySha256) {
                throw ServerIdentityMismatchException(origin, "a chave não é a do pareamento")
            }
            if (expectedInstanceId != null && answer.instance_id != expectedInstanceId) {
                throw ServerIdentityMismatchException(origin, "é outra instalação")
            }
            val valid = runCatching {
                val key = KeyFactory.getInstance("EC").generatePublic(X509EncodedKeySpec(publicKey))
                Signature.getInstance("SHA256withECDSA").run {
                    initVerify(key)
                    update(message(answer.instance_id, nonce, address))
                    verify(Base64.getDecoder().decode(answer.signature))
                }
            }.getOrDefault(false)
            if (!valid) throw ServerIdentityMismatchException(origin, "assinatura inválida")
        }
    }
}
