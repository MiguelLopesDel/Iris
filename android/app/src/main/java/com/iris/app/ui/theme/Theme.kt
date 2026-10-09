package com.iris.app.ui.theme

import android.app.Activity
import android.os.Build
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.ColorScheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.dynamicDarkColorScheme
import androidx.compose.material3.dynamicLightColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.SideEffect
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalView
import androidx.core.view.WindowCompat

/** Which theme the app shows; stored as its [key] in the app settings. */
enum class ThemeMode(val key: String) {
    LIGHT("light"),
    DARK("dark"),
    SYSTEM("system");

    @Composable
    fun isDark(): Boolean = when (this) {
        LIGHT -> false
        DARK -> true
        SYSTEM -> isSystemInDarkTheme()
    }

    companion object {
        /**
         * Light unless chosen otherwise: it is what most people know from their
         * phone's own apps, and the easiest to read for many of them.
         */
        val DEFAULT = LIGHT

        fun fromKey(key: String?): ThemeMode = entries.firstOrNull { it.key == key } ?: DEFAULT
    }
}

/**
 * Where the colours come from. Each source gives a Material [ColorScheme]; the
 * Iris palette the screens read is derived from it ([IrisPalette.from]), so a
 * new source (another brand, a high-contrast set) only has to supply a scheme.
 */
enum class ThemeColors(val key: String) {
    /** The Iris colours. */
    IRIS("iris"),

    /** Material You: the colours Android 12+ derives from the wallpaper; Iris's elsewhere. */
    SYSTEM("system");

    companion object {
        val DEFAULT = IRIS

        fun fromKey(key: String?): ThemeColors = entries.firstOrNull { it.key == key } ?: DEFAULT
    }
}

@Composable
private fun colorSchemeFor(colors: ThemeColors, dark: Boolean): ColorScheme {
    if (colors == ThemeColors.SYSTEM && Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
        val context = LocalContext.current
        return if (dark) dynamicDarkColorScheme(context) else dynamicLightColorScheme(context)
    }
    return if (dark) DarkColorScheme else LightColorScheme
}

private fun schemeOf(palette: IrisPalette, dark: Boolean): ColorScheme {
    val base = if (dark) darkColorScheme() else lightColorScheme()
    return base.copy(
        primary = palette.accent,
        onPrimary = palette.onAccent,
        primaryContainer = palette.surfaceBright,
        onPrimaryContainer = palette.text,
        secondary = palette.violet,
        onSecondary = palette.onAccent,
        tertiary = palette.accentStrong,
        background = palette.background,
        onBackground = palette.text,
        surface = palette.surface,
        onSurface = palette.text,
        surfaceVariant = palette.surfaceBright,
        onSurfaceVariant = palette.textSoft,
        surfaceContainer = palette.surface,
        surfaceContainerHigh = palette.surfaceHover,
        surfaceContainerHighest = palette.surfaceBright,
        outline = palette.textMuted,
        outlineVariant = palette.borderStrong,
        error = palette.danger,
        onError = palette.onAccent,
    )
}

private val DarkColorScheme = schemeOf(IrisDarkPalette, dark = true)
private val LightColorScheme = schemeOf(IrisLightPalette, dark = false)

@Composable
fun IrisTheme(
    darkTheme: Boolean = ThemeMode.DEFAULT.isDark(),
    colors: ThemeColors = ThemeColors.DEFAULT,
    content: @Composable () -> Unit
) {
    val colorScheme = colorSchemeFor(colors, darkTheme)
    // The Iris schemes keep their hand-tuned palette; any other scheme lends its roles.
    val palette = when (colorScheme) {
        DarkColorScheme -> IrisDarkPalette
        LightColorScheme -> IrisLightPalette
        else -> IrisPalette.from(colorScheme)
    }
    val view = LocalView.current

    if (!view.isInEditMode) {
        SideEffect {
            val window = (view.context as? Activity)?.window ?: return@SideEffect
            WindowCompat.getInsetsController(window, view).isAppearanceLightStatusBars = !darkTheme
            WindowCompat.getInsetsController(window, view).isAppearanceLightNavigationBars = !darkTheme
        }
    }

    CompositionLocalProvider(LocalIrisPalette provides palette) {
        MaterialTheme(
            colorScheme = colorScheme,
            content = content
        )
    }
}
