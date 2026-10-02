package com.iris.app.ui.screens.settings

import org.junit.Assert.assertEquals
import org.junit.Test

class CertificateNameTest {
    @Test
    fun `the common name is shown instead of the full distinguished name`() {
        assertEquals("iris.example", commonName("CN=iris.example,O=Caddy Local Authority"))
        assertEquals("Caddy Local Authority - 2026 ECC Root", commonName("CN=Caddy Local Authority - 2026 ECC Root"))
        assertEquals("a, b", commonName("O=x,CN=a\\, b"))
        assertEquals("O=Only Org", commonName("O=Only Org"))
    }
}
