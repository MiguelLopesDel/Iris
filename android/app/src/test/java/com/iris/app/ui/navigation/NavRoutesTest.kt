package com.iris.app.ui.navigation

import org.junit.Assert.assertEquals
import org.junit.Test

class NavRoutesTest {

    /** How Navigation reads a query argument: percent-decoding, where "+" stays a plus. */
    private fun asNavigationReadsIt(route: String): String =
        java.net.URLDecoder.decode(route.substringAfter("?name=").replace("+", "%2B"), "UTF-8")

    @Test
    fun `names with spaces reach the screen as typed`() {
        assertEquals("Ana Maria", asNavigationReadsIt(NavRoute.PersonMedia.createRoute(1, "Ana Maria")))
        assertEquals("Férias 2024", asNavigationReadsIt(NavRoute.CollectionMedia.createRoute(2, "Férias 2024")))
        assertEquals("Casa & praia", asNavigationReadsIt(NavRoute.Space.createRoute(3, "Casa & praia")))
    }

    @Test
    fun `a real plus sign survives too`() {
        assertEquals("C++ / 1+1", asNavigationReadsIt(NavRoute.PersonMedia.createRoute(1, "C++ / 1+1")))
    }
}
