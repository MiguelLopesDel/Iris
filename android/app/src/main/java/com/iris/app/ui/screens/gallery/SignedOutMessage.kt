package com.iris.app.ui.screens.gallery

/**
 * What the gallery says to a signed-out user, from the server's current
 * state: the text has to change as soon as the server gets its first
 * account. Null means the generic login text.
 */
internal fun signedOutMessage(serverReplaced: Boolean, serverNeedsSetup: Boolean): String? = when {
    serverReplaced && serverNeedsSetup ->
        "O servidor foi reinstalado e ainda não tem contas. Crie a conta no servidor e pareie este aparelho de novo."
    serverNeedsSetup ->
        "O servidor ainda não tem contas. Crie a conta no servidor e depois pareie este aparelho."
    serverReplaced ->
        "O servidor foi reinstalado. Entre com a conta nova ou pareie este aparelho de novo."
    else -> null
}

/** How often a signed-out gallery asks whether the server got its first account. */
internal const val SETUP_RECHECK_MILLIS = 5_000L
