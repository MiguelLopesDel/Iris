package com.iris.app.ui.theme

import android.app.Activity
import android.os.Build
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.SideEffect
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.platform.LocalView
import androidx.core.view.WindowCompat

private val DarkColorScheme = darkColorScheme(
    primary = IrisAccentLime,
    onPrimary = IrisAccentInk,
    primaryContainer = IrisDarkSurfaceBright,
    onPrimaryContainer = IrisAccentLime,
    secondary = IrisViolet,
    onSecondary = IrisDarkBg,
    tertiary = IrisAccentLimeStrong,
    background = IrisDarkBg,
    onBackground = IrisText,
    surface = IrisDarkSurface,
    onSurface = IrisText,
    surfaceVariant = IrisDarkSurfaceBright,
    onSurfaceVariant = IrisTextSoft,
    outline = IrisTextMuted,
    error = IrisDanger,
    onError = IrisDarkBg
)

private val LightColorScheme = lightColorScheme(
    primary = Color(0xFF4C7B00),
    onPrimary = Color.White,
    primaryContainer = Color(0xFFD6F6A0),
    onPrimaryContainer = Color(0xFF142000),
    secondary = IrisViolet,
    background = IrisLightBg,
    onBackground = IrisLightText,
    surface = IrisLightSurface,
    onSurface = IrisLightText,
    surfaceVariant = Color(0xFFE5E7EB),
    onSurfaceVariant = IrisLightTextSoft
)

@Composable
fun IrisTheme(
    darkTheme: Boolean = true, // Default to Iris' iconic dark theme
    content: @Composable () -> Unit
) {
    val colorScheme = if (darkTheme) DarkColorScheme else LightColorScheme
    val view = LocalView.current

    if (!view.isInEditMode) {
        SideEffect {
            val window = (view.context as? Activity)?.window ?: return@SideEffect
            window.statusBarColor = colorScheme.background.toArgb()
            window.navigationBarColor = colorScheme.surface.toArgb()
            WindowCompat.getInsetsController(window, view).isAppearanceLightStatusBars = !darkTheme
            WindowCompat.getInsetsController(window, view).isAppearanceLightNavigationBars = !darkTheme
        }
    }

    MaterialTheme(
        colorScheme = colorScheme,
        content = content
    )
}
