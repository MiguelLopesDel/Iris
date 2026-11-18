package com.iris.app.data.net

import org.junit.Assert.assertEquals
import org.junit.Test

class TailscaleTest {
    @Test
    fun `nothing is offered while the server answers`() {
        assertEquals(Tailscale.Hint.NONE, Tailscale.hint(offline = false, installed = true, vpnActive = false))
    }

    @Test
    fun `nothing is offered when Tailscale is not installed`() {
        assertEquals(Tailscale.Hint.NONE, Tailscale.hint(offline = true, installed = false, vpnActive = false))
    }

    @Test
    fun `a switched off VPN is called out`() {
        assertEquals(Tailscale.Hint.OPEN_VPN_OFF, Tailscale.hint(offline = true, installed = true, vpnActive = false))
    }

    @Test
    fun `with the VPN on Tailscale is still offered`() {
        assertEquals(Tailscale.Hint.OPEN, Tailscale.hint(offline = true, installed = true, vpnActive = true))
    }
}
