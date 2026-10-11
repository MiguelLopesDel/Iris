package com.iris.app.ui.theme

import androidx.compose.material3.lightColorScheme
import androidx.compose.ui.graphics.Color
import org.junit.Assert.assertEquals
import org.junit.Test

class ThemeModeTest {

    @Test
    fun `the app is light unless another theme was chosen`() {
        assertEquals(ThemeMode.LIGHT, ThemeMode.fromKey(null))
        assertEquals(ThemeMode.LIGHT, ThemeMode.fromKey("unknown"))
        assertEquals(ThemeMode.DARK, ThemeMode.fromKey("dark"))
        assertEquals(ThemeMode.SYSTEM, ThemeMode.fromKey("system"))
    }

    @Test
    fun `colours are Iris's unless the phone's were chosen`() {
        assertEquals(ThemeColors.IRIS, ThemeColors.fromKey(null))
        assertEquals(ThemeColors.SYSTEM, ThemeColors.fromKey("system"))
    }

    @Test
    fun `any material scheme gives a palette, so a new theme only supplies a scheme`() {
        val scheme = lightColorScheme(primary = Color(0xFF123456), background = Color(0xFFFAFAFA))

        val palette = IrisPalette.from(scheme)

        assertEquals(Color(0xFF123456), palette.accent)
        assertEquals(Color(0xFFFAFAFA), palette.background)
        assertEquals(scheme.onBackground, palette.text)
    }
}
