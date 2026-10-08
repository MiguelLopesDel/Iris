package com.iris.app.data.remote.security

import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.IOException
import java.net.URI
import java.net.URLDecoder
import java.security.MessageDigest
import java.security.SecureRandom
import java.security.cert.X509Certificate
import java.util.concurrent.TimeUnit
import javax.net.ssl.SSLContext
import javax.net.ssl.X509TrustManager

/**
 * What a pairing code says: which server (by its instance identifier and, from
 * servers that have one, the fingerprint of its identity key), at which
 * addresses, and optionally which certificate authority it uses. Produced by the
 * server's "Conectar um celular" screen as `iris://pair?v=1&id=...&u=...&ca=...&k=...`.
 */
data class PairingCode(
    val instanceId: String,
    val addresses: List<String>,
    val caSha256: String?,
    /** SHA-256 of the server's identity key (`k`); absent in codes from older servers. */
    val keySha256: String? = null,
) {
    val usesCleartext: Boolean get() = addresses.any { it.startsWith("http://") }

    companion object {
        const val MAX_ADDRESSES = 8

        /** Parses a pairing link, or throws [PairingCodeException] saying what is wrong. */
        fun parse(text: String): PairingCode {
            val uri = runCatching { URI(text.trim()) }.getOrNull()
                ?: throw PairingCodeException("Isso não é um código de pareamento do Iris.")
            if (uri.scheme != "iris" || uri.host != "pair") {
                throw PairingCodeException("Isso não é um código de pareamento do Iris.")
            }
            val query = (uri.rawQuery ?: "").split('&').filter { it.isNotEmpty() }.map { pair ->
                val (key, value) = pair.split('=', limit = 2).let { it[0] to it.getOrElse(1) { "" } }
                URLDecoder.decode(key, "UTF-8") to URLDecoder.decode(value, "UTF-8")
            }
            fun all(key: String) = query.filter { it.first == key }.map { it.second }
            if (all("v") != listOf("1")) {
                throw PairingCodeException("Código de uma versão do Iris que este app não conhece. Atualize o app.")
            }
            val id = all("id").singleOrNull()?.takeIf { Regex("[0-9a-f]{32}").matches(it) }
                ?: throw PairingCodeException("Código incompleto: falta o identificador do servidor.")
            val addresses = all("u").map { address ->
                val url = address.toHttpUrlOrNull()
                // Credentials in an address would be sent to whoever answers it.
                if (url == null || url.encodedPath != "/" || url.query != null || url.fragment != null ||
                    url.username.isNotEmpty() || url.password.isNotEmpty()
                ) {
                    throw PairingCodeException("Endereço inválido no código: $address")
                }
                address.trimEnd('/')
            }.distinct()
            if (addresses.isEmpty()) throw PairingCodeException("O código não traz nenhum endereço.")
            if (addresses.size > MAX_ADDRESSES) throw PairingCodeException("O código traz endereços demais.")
            val ca = all("ca").singleOrNull()
            if (ca != null && !Regex("[0-9a-f]{64}").matches(ca)) {
                throw PairingCodeException("Impressão digital inválida no código.")
            }
            val key = all("k").singleOrNull()
            if (key != null && !Regex("[0-9a-f]{64}").matches(key)) {
                throw PairingCodeException("Identidade do servidor inválida no código.")
            }
            return PairingCode(id, addresses, ca, key)
        }
    }
}

class PairingCodeException(message: String) : IllegalArgumentException(message)

/** How one address answered while pairing. */
sealed interface AddressOutcome {
    val address: String

    data class Connected(override val address: String) : AddressOutcome
    data class OtherServer(override val address: String) : AddressOutcome
    data class Skipped(override val address: String, val reason: String) : AddressOutcome
    data class Failed(override val address: String, val problem: ConnectionProblem?, val message: String) : AddressOutcome
}

data class PairingResult(val address: String?, val outcomes: List<AddressOutcome>)

/** The code's CA could not be obtained, or did not match its fingerprint. */
class PairingAuthorityException(message: String) : IOException(message)

/**
 * Applies a [PairingCode]: trusts the code's certificate authority for its HTTPS
 * addresses, allows HTTP for its HTTP addresses when the user accepted that, and
 * picks the first address that answers as the same server: the same instance id
 * and, when the code names one, a signature made with the code's identity key.
 *
 * The authority is downloaded without verifying the connection -- it is only a
 * public certificate, no credential is sent, and it is accepted only if its
 * SHA-256 equals the fingerprint the code carried from a screen the user trusts.
 */
class PairingConnector(
    private val security: ConnectionSecurity,
    private val timeoutSeconds: Long = 8,
) {

    // Used only to fetch the CA certificate, whose bytes are checked against the code.
    private val unverifiedClient: OkHttpClient by lazy {
        val acceptAll = object : X509TrustManager {
            override fun checkClientTrusted(chain: Array<X509Certificate>, authType: String) = Unit
            override fun checkServerTrusted(chain: Array<X509Certificate>, authType: String) = Unit
            override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()
        }
        val context = SSLContext.getInstance("TLS").apply { init(null, arrayOf(acceptAll), SecureRandom()) }
        baseBuilder()
            .sslSocketFactory(context.socketFactory, acceptAll)
            .hostnameVerifier { _, _ -> true }
            .build()
    }

    private fun baseBuilder() = OkHttpClient.Builder()
        .connectTimeout(timeoutSeconds, TimeUnit.SECONDS)
        .readTimeout(timeoutSeconds, TimeUnit.SECONDS)
        .followRedirects(false)
        .followSslRedirects(false)

    /** Blocking: call off the main thread. */
    fun connect(code: PairingCode, allowCleartext: Boolean): PairingResult {
        // The authority only matters for HTTPS addresses; a code may carry it for others too.
        val authority = code.caSha256
            ?.takeIf { code.addresses.any { it.startsWith("https://") } }
            ?.let { authority(code, it) }
        val outcomes = mutableListOf<AddressOutcome>()
        for (address in code.addresses) {
            val origin = ServerOrigin.of(address) ?: continue
            val https = address.startsWith("https://")
            if (!https && !allowCleartext) {
                outcomes += AddressOutcome.Skipped(address, "HTTP sem criptografia não foi permitido")
                continue
            }
            val current = security.securityFor(origin).copy(identityKeySha256 = code.keySha256)
            val candidate = when {
                https && authority != null ->
                    current.copy(trustMode = TrustMode.PINNED, pinnedCertificates = listOf(authority))
                !https -> current.copy(cleartextAllowed = true)
                else -> current
            }
            // The address must prove it is this server under the candidate policy
            // before that policy is saved: nothing is stored for any other address.
            val trial = security.trying(origin, candidate)
            val outcome = try {
                when {
                    instanceAt(address, trial) != code.instanceId -> AddressOutcome.OtherServer(address)
                    // The instance id is public: only the key proves this is the server on the code.
                    code.keySha256 != null && !provesIdentity(address, trial, code) -> AddressOutcome.OtherServer(address)
                    else -> AddressOutcome.Connected(address)
                }
            } catch (error: IOException) {
                AddressOutcome.Failed(address, ConnectionProblem.from(error, origin, trial), error.message.orEmpty())
            }
            outcomes += outcome
            if (outcome is AddressOutcome.Connected) {
                security.update(origin) { candidate }
                return PairingResult(address, outcomes)
            }
        }
        return PairingResult(null, outcomes)
    }

    private fun provesIdentity(address: String, trial: ConnectionSecurity, code: PairingCode): Boolean {
        val url = address.toHttpUrlOrNull() ?: return false
        val verifier = ServerIdentityVerifier(client = { trial.apply(baseBuilder()).build() })
        return try {
            verifier.requireIdentity(url, code.keySha256 ?: return true, code.instanceId)
            true
        } catch (_: ServerIdentityMismatchException) {
            false
        }
    }

    private fun instanceAt(address: String, trial: ConnectionSecurity): String? {
        val request = Request.Builder().url("$address/healthz").build()
        trial.apply(baseBuilder()).build().newCall(request).execute().use { response ->
            if (!response.isSuccessful) throw IOException("HTTP ${response.code}")
            val body = response.body?.string().orEmpty()
            return Regex("\"instance_id\"\\s*:\\s*\"([0-9a-f]{32})\"").find(body)?.groupValues?.get(1)
        }
    }

    private fun authority(code: PairingCode, expectedSha256: String): ByteArray {
        var lastError = "nenhum endereço HTTPS respondeu"
        for (address in code.addresses.filter { it.startsWith("https://") }) {
            try {
                val request = Request.Builder().url("$address/api/pairing/ca.pem").build()
                val pem = unverifiedClient.newCall(request).execute().use { response ->
                    if (!response.isSuccessful) throw IOException("HTTP ${response.code}")
                    response.body?.bytes() ?: ByteArray(0)
                }
                val certificate = ConnectionSecurity.parseCertificates(pem).singleOrNull()
                    ?: throw IOException("resposta não é um certificado")
                if (sha256Hex(certificate.encoded) == expectedSha256) return certificate.encoded
                lastError = "o certificado recebido de $address não confere com o código"
            } catch (error: IOException) {
                lastError = "$address: ${error.message}"
            } catch (error: java.security.cert.CertificateException) {
                lastError = "$address: ${error.message}"
            }
        }
        throw PairingAuthorityException(
            "Não foi possível obter a autoridade de certificado indicada pelo código ($lastError). Nada foi alterado."
        )
    }

    companion object {
        fun sha256Hex(bytes: ByteArray): String =
            MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it.toInt() and 0xff) }
    }
}
