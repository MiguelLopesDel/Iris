package com.iris.app.data.remote.security

import android.content.Context
import android.content.SharedPreferences
import android.util.Base64
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import java.util.concurrent.ConcurrentHashMap

/**
 * [ServerSecurityStore] on SharedPreferences, mirrored in memory: TLS
 * handshakes read it on network threads and must not wait for disk.
 */
class PreferencesServerSecurityStore(context: Context) : ServerSecurityStore {
    private val preferences: SharedPreferences =
        context.getSharedPreferences("iris_connection_security", Context.MODE_PRIVATE)
    private val cache = ConcurrentHashMap<String, ServerSecurity>()

    init {
        preferences.all.forEach { (key, value) ->
            if (key.startsWith(PREFIX) && value is String) {
                decode(value)?.let { cache[key.removePrefix(PREFIX)] = it }
            }
        }
    }

    override fun get(origin: ServerOrigin): ServerSecurity = cache[origin.key] ?: ServerSecurity()

    override fun put(origin: ServerOrigin, security: ServerSecurity) {
        cache[origin.key] = security
        preferences.edit().putString(PREFIX + origin.key, encode(security)).apply()
    }

    /**
     * Before this policy existed, HTTP was allowed silently. A server the user
     * already configured over HTTP keeps working, background sync included;
     * only addresses chosen from now on ask first. Runs once per install.
     */
    fun grandfatherCleartext(serverUrl: String) {
        if (preferences.getBoolean(GRANDFATHERED, false)) return
        val origin = ServerOrigin.of(serverUrl)
        if (origin != null && serverUrl.trim().startsWith("http://")) {
            put(origin, get(origin).copy(cleartextAllowed = true))
        }
        preferences.edit().putBoolean(GRANDFATHERED, true).apply()
    }

    @Serializable
    private data class Stored(
        val trustMode: String,
        val pinned: List<String> = emptyList(),
        val cleartextAllowed: Boolean = false,
        val identityKeySha256: String? = null,
    )

    private fun encode(security: ServerSecurity): String = Json.encodeToString(
        Stored.serializer(),
        Stored(
            trustMode = security.trustMode.name,
            pinned = security.pinnedCertificates.map { Base64.encodeToString(it, Base64.NO_WRAP) },
            cleartextAllowed = security.cleartextAllowed,
            identityKeySha256 = security.identityKeySha256,
        ),
    )

    private fun decode(text: String): ServerSecurity? = runCatching {
        val stored = Json.decodeFromString(Stored.serializer(), text)
        ServerSecurity(
            trustMode = TrustMode.valueOf(stored.trustMode),
            pinnedCertificates = stored.pinned.map { Base64.decode(it, Base64.NO_WRAP) },
            cleartextAllowed = stored.cleartextAllowed,
            identityKeySha256 = stored.identityKeySha256,
        )
    }.getOrNull()

    private companion object {
        const val PREFIX = "server:"
        const val GRANDFATHERED = "cleartext_grandfathered_v1"
    }
}
