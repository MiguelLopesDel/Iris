package com.iris.app.data.net

import android.content.Context
import android.content.Intent
import android.net.ConnectivityManager
import android.net.NetworkCapabilities

/**
 * The server is usually reached through Tailscale, whose VPN does not always
 * come back by itself after a reboot. When the server cannot be reached, the
 * person is offered a way to open Tailscale instead of a dead end.
 */
object Tailscale {
    const val PACKAGE = "com.tailscale.ipn"

    enum class Hint { NONE, OPEN, OPEN_VPN_OFF }

    fun hint(offline: Boolean, installed: Boolean, vpnActive: Boolean): Hint = when {
        !offline || !installed -> Hint.NONE
        vpnActive -> Hint.OPEN
        else -> Hint.OPEN_VPN_OFF
    }

    fun launchIntent(context: Context): Intent? =
        context.packageManager.getLaunchIntentForPackage(PACKAGE)

    fun vpnActive(context: Context): Boolean {
        val manager = context.getSystemService(ConnectivityManager::class.java) ?: return false
        // An app subject to a VPN sees it as its default network.
        val network = manager.activeNetwork ?: return false
        return manager.getNetworkCapabilities(network)?.hasTransport(NetworkCapabilities.TRANSPORT_VPN) == true
    }
}
