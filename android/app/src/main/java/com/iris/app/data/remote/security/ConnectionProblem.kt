package com.iris.app.data.remote.security

import java.net.ConnectException
import java.net.NoRouteToHostException
import java.net.SocketTimeoutException
import java.net.UnknownHostException
import java.security.cert.CertPathValidatorException
import java.security.cert.CertificateException
import java.security.cert.X509Certificate
import javax.net.ssl.SSLException
import javax.net.ssl.SSLHandshakeException
import javax.net.ssl.SSLPeerUnverifiedException

/** Why a connection failed, in terms of what the user can do about it. */
sealed interface ConnectionProblem {
    val origin: ServerOrigin

    /** The address uses HTTP and the user has not accepted unencrypted traffic to it. */
    data class CleartextNotAllowed(override val origin: ServerOrigin, val privateAddress: Boolean) : ConnectionProblem

    /**
     * The certificate does not lead to an authority this server is trusted by.
     * [chain] is what the server presented, leaf first.
     */
    data class UntrustedCertificate(
        override val origin: ServerOrigin,
        val chain: List<X509Certificate>,
        /** The authorities installed on the device would accept it. */
        val trustedByDeviceCas: Boolean,
    ) : ConnectionProblem {
        val leaf: X509Certificate get() = chain.first()

        /** The top of the chain signs itself, so trusting it stays valid as long as it does. */
        val canTrustPresentedCertificate: Boolean get() = ConnectionSecurity.isSelfSigned(chain.last())
    }

    /** The certificate is trusted but does not name this address (a certificate for a name, used by IP, say). */
    data class NameMismatch(override val origin: ServerOrigin, val detail: String) : ConnectionProblem

    /** Something answered at this address, but not with HTTPS: usually the wrong port or scheme. */
    data class NotHttps(override val origin: ServerOrigin) : ConnectionProblem

    /** Nothing answered at all; [reason] says how, which points at what to check. */
    data class Unreachable(override val origin: ServerOrigin, val reason: Reason) : ConnectionProblem {
        enum class Reason {
            /** The machine answered, but nothing listens on the port: a wrong port, or Iris is not running. */
            REFUSED,

            /** No answer in time, or no way there: the network, a VPN that is off, a machine that is off. */
            SILENT,

            /** The name does not resolve. */
            UNKNOWN_HOST,
        }

        /** An address of Tailscale (100.64.0.0/10, or a `.ts.net` name): it only answers with Tailscale on. */
        val viaTailscale: Boolean get() = isTailscaleAddress(origin.host)
    }

    companion object {
        fun isTailscaleAddress(host: String): Boolean {
            val name = host.lowercase().trim('[', ']')
            if (name.endsWith(".ts.net")) return true
            val parts = name.split('.').mapNotNull { it.toIntOrNull() }
            return parts.size == 4 && name.count { it == '.' } == 3 && parts[0] == 100 && parts[1] in 64..127
        }

        private fun unreachable(causes: List<Throwable>): Unreachable.Reason? = when {
            causes.any { it is UnknownHostException } -> Unreachable.Reason.UNKNOWN_HOST
            causes.any { it is ConnectException && "refused" in it.message.orEmpty().lowercase() } ->
                Unreachable.Reason.REFUSED
            causes.any { it is SocketTimeoutException || it is NoRouteToHostException || it is ConnectException } ->
                Unreachable.Reason.SILENT
            else -> null
        }

        /**
         * The failure, its causes and what it suppressed: OkHttp retries other
         * addresses of a host (IPv6, then IPv4) and reports the last error,
         * keeping the earlier ones -- often the TLS one -- as suppressed.
         */
        private fun everyCause(failure: Throwable): List<Throwable> {
            val seen = LinkedHashSet<Throwable>()
            val pending = ArrayDeque(listOf(failure))
            while (pending.isNotEmpty()) {
                val next = pending.removeFirst()
                if (!seen.add(next)) continue
                next.cause?.let(pending::addLast)
                next.suppressed.forEach(pending::addLast)
            }
            return seen.toList()
        }

        /** Classifies [failure], or returns null when it is neither a security nor a reachability problem. */
        fun from(failure: Throwable, origin: ServerOrigin, security: ConnectionSecurity): ConnectionProblem? {
            val causes = everyCause(failure)
            causes.filterIsInstance<CleartextNotPermittedException>().firstOrNull()?.let {
                return CleartextNotAllowed(it.origin, ConnectionSecurity.isPrivateAddress(it.origin.host))
            }
            causes.filterIsInstance<SSLPeerUnverifiedException>().firstOrNull()?.let {
                return NameMismatch(origin, it.message.orEmpty())
            }
            val untrusted = causes.any { it is CertPathValidatorException || it is CertificateException }
            val chain = security.lastRejectedChain(origin)
            if (untrusted && chain != null && causes.any { it is SSLHandshakeException }) {
                return UntrustedCertificate(origin, chain, security.deviceCasTrustLastRejected(origin))
            }
            if (causes.any { it is SSLException }) {
                val text = causes.joinToString(" ") { it.message.orEmpty() }.lowercase()
                if ("protocol" in text || "unrecognized ssl message" in text || "not an ssl" in text ||
                    "plaintext" in text || "wrong version number" in text
                ) return NotHttps(origin)
            }
            unreachable(causes)?.let { return Unreachable(origin, it) }
            return null
        }
    }
}
