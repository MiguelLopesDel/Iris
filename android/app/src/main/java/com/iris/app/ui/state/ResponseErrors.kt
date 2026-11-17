package com.iris.app.ui.state

import kotlinx.serialization.SerializationException

/**
 * Whether a request failed because the answer was not the JSON the app reads:
 * an HTML page from a wrong address or a proxy, most often. Matched by type:
 * the message check it replaces looked for "Unexpected token", which the
 * library never writes ("Unexpected JSON token at offset ..."), so the friendly
 * message never showed.
 */
fun isUnreadableResponse(error: Throwable): Boolean =
    generateSequence(error) { it.cause }.take(8).any { it is SerializationException }
