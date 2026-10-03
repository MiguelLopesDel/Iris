package com.iris.app.data.remote

import com.iris.app.data.model.HealthResponse

/**
 * Whether a saved device session still belongs to the server answering at
 * its address.
 *
 * A server reinstalled at the same address keeps the address but none of the
 * accounts or devices: the phone kept showing the account, "connected", and
 * a gallery mirrored from the old installation, while every request got 503.
 * The session records the installation it was made on and is compared with
 * the instance_id reported by /healthz.
 */
object SessionServerCheck {

    enum class Verdict {
        /** Same installation, or a server too old to say. */
        SAME,
        /** A session from before this check: record the installation it now talks to. */
        RECORD,
        /** Another installation, or one with no accounts yet: the session is void. */
        REPLACED,
    }

    fun verdict(health: HealthResponse, recordedInstanceId: String?): Verdict {
        // A server still asking for its first account has none; no saved session can be its.
        if (health.status == "setup_required") return Verdict.REPLACED
        val current = health.instanceId?.takeIf { it.isNotBlank() } ?: return Verdict.SAME
        return when (recordedInstanceId) {
            null -> Verdict.RECORD
            current -> Verdict.SAME
            else -> Verdict.REPLACED
        }
    }
}
