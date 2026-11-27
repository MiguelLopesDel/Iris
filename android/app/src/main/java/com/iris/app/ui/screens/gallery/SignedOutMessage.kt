package com.iris.app.ui.screens.gallery

import com.iris.app.R
import com.iris.app.ui.text.appString

/**
 * What the gallery says to a signed-out user, from the server's current
 * state: the text has to change as soon as the server gets its first
 * account. Null means the generic login text.
 */
internal fun signedOutMessage(serverReplaced: Boolean, serverNeedsSetup: Boolean): String? = when {
    serverReplaced && serverNeedsSetup ->
        appString(R.string.signed_out_replaced_no_accounts)
    serverNeedsSetup ->
        appString(R.string.signed_out_no_accounts)
    serverReplaced ->
        appString(R.string.signed_out_replaced)
    else -> null
}

/** How often a signed-out gallery asks whether the server got its first account. */
internal const val SETUP_RECHECK_MILLIS = 5_000L
