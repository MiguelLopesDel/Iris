package com.iris.app.data.remote.security

import com.iris.app.R
import com.iris.app.ui.text.appString

/**
 * A [ConnectionProblem] in plain words: what happened and what to check, for
 * people who do not know what a port or a certificate is.
 */
object ConnectionMessages {

    fun describe(problem: ConnectionProblem): String = when (problem) {
        is ConnectionProblem.CleartextNotAllowed ->
            appString(R.string.conn_cleartext)
        is ConnectionProblem.UntrustedCertificate ->
            appString(R.string.conn_untrusted_cert)
        is ConnectionProblem.NameMismatch ->
            appString(R.string.conn_name_mismatch, problem.origin.host)
        is ConnectionProblem.NotHttps ->
            appString(R.string.conn_not_https, problem.origin.host, problem.origin.port)
        is ConnectionProblem.Unreachable -> unreachable(problem)
    }

    private fun unreachable(problem: ConnectionProblem.Unreachable): String {
        val host = problem.origin.host
        return when {
            problem.reason == ConnectionProblem.Unreachable.Reason.UNKNOWN_HOST ->
                if (problem.viaTailscale) {
                    appString(R.string.conn_unknown_host_tailscale, host)
                } else {
                    appString(R.string.conn_unknown_host, host)
                }
            problem.reason == ConnectionProblem.Unreachable.Reason.REFUSED ->
                appString(R.string.conn_refused, host, problem.origin.port)
            problem.viaTailscale ->
                appString(R.string.conn_unreachable_tailscale, host)
            ConnectionSecurity.isPrivateAddress(host) ->
                appString(R.string.conn_unreachable_private, host)
            else ->
                appString(R.string.conn_unreachable, host)
        }
    }
}
