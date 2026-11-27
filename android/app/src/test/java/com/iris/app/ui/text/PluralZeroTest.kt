package com.iris.app.ui.text

import com.iris.app.R
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Test

class PluralZeroTest {

    @Before
    fun readTextsFromResources() = TestStrings.install()

    @After
    fun restoreResolver() = TestStrings.uninstall()

    @Test
    fun `zero reads as plural even though Portuguese CLDR files it under one`() {
        assertEquals("0 mídias", appPlural(R.plurals.common_media_count, 0, 0))
        assertEquals("1 mídia", appPlural(R.plurals.common_media_count, 1, 1))
        assertEquals("2 mídias", appPlural(R.plurals.common_media_count, 2, 2))
    }

    @Test
    fun `zero is routed to the other quantity`() {
        assertEquals(2, pluralQuantity(0))
        assertEquals(1, pluralQuantity(1))
        assertEquals(7, pluralQuantity(7))
    }
}
