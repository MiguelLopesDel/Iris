package com.iris.app.ui.theme

import androidx.compose.material3.ColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.Immutable
import androidx.compose.runtime.ReadOnlyComposable
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.graphics.Color

/**
 * The Iris colours, in a light and a dark version.
 *
 * Screens use the names below (IrisBackground, IrisTextSoft…), which read the
 * palette of the current theme ([LocalIrisPalette]), so one screen serves every
 * theme. A theme is a Material ColorScheme; Iris's own two keep the hand-tuned
 * palettes below, any other (Material You, say) derives one with [IrisPalette.from].
 * Media viewers keep their own black, as photo apps do.
 */
@Immutable
data class IrisPalette(
    val background: Color,
    val surface: Color,
    val surfaceBright: Color,
    val surfaceHover: Color,
    /** Highlights, selected items, primary buttons. */
    val accent: Color,
    val accentStrong: Color,
    /** Text and icons drawn on [accent]. */
    val onAccent: Color,
    val violet: Color,
    val danger: Color,
    val text: Color,
    /** Secondary text: still readable at a glance. */
    val textSoft: Color,
    /** Least important text: captions, hints. */
    val textMuted: Color,
    val border: Color,
    val borderStrong: Color,
) {
    companion object {
        /** The palette a Material [ColorScheme] implies, for themes that are not Iris's own. */
        fun from(scheme: ColorScheme) = IrisPalette(
            background = scheme.background,
            surface = scheme.surface,
            surfaceBright = scheme.surfaceContainerHighest,
            surfaceHover = scheme.surfaceContainerHigh,
            accent = scheme.primary,
            accentStrong = scheme.primary,
            onAccent = scheme.onPrimary,
            violet = scheme.tertiary,
            danger = scheme.error,
            text = scheme.onBackground,
            textSoft = scheme.onSurfaceVariant,
            textMuted = scheme.outline,
            border = scheme.outlineVariant.copy(alpha = 0.6f),
            borderStrong = scheme.outlineVariant,
        )
    }
}

val IrisDarkPalette = IrisPalette(
    background = Color(0xFF090B10),
    surface = Color(0xFF151922),
    surfaceBright = Color(0xFF232A38),
    surfaceHover = Color(0xFF1B202C),
    accent = Color(0xFFB8FF5A),
    accentStrong = Color(0xFF9CE83E),
    onAccent = Color(0xFF111708),
    violet = Color(0xFF8F7CFF),
    danger = Color(0xFFFF667D),
    text = Color(0xFFF5F3EE),
    textSoft = Color(0xFFB4B7C2),
    textMuted = Color(0xFF777D8D),
    border = Color(0x17FFFFFF),
    borderStrong = Color(0x29FFFFFF),
)

/**
 * White pages, near-black text and a deep green accent: the lime of the dark
 * theme is unreadable on white. Secondary text stays dark enough to read
 * easily (contrast well above WCAG AA on white), for people with weaker sight.
 */
val IrisLightPalette = IrisPalette(
    background = Color(0xFFF6F7F9),
    surface = Color(0xFFFFFFFF),
    surfaceBright = Color(0xFFE9EDF2),
    surfaceHover = Color(0xFFEEF1F5),
    accent = Color(0xFF3D6B00),
    accentStrong = Color(0xFF2F5600),
    onAccent = Color(0xFFFFFFFF),
    violet = Color(0xFF5B47D6),
    danger = Color(0xFFC0283D),
    text = Color(0xFF14171F),
    textSoft = Color(0xFF3B4250),
    textMuted = Color(0xFF5B6271),
    border = Color(0x1F14171F),
    borderStrong = Color(0x3314171F),
)

val LocalIrisPalette = staticCompositionLocalOf { IrisLightPalette }

val IrisBackground: Color @Composable @ReadOnlyComposable get() = LocalIrisPalette.current.background
val IrisSurface: Color @Composable @ReadOnlyComposable get() = LocalIrisPalette.current.surface
val IrisSurfaceBright: Color @Composable @ReadOnlyComposable get() = LocalIrisPalette.current.surfaceBright
val IrisSurfaceHover: Color @Composable @ReadOnlyComposable get() = LocalIrisPalette.current.surfaceHover
val IrisAccent: Color @Composable @ReadOnlyComposable get() = LocalIrisPalette.current.accent
val IrisAccentStrong: Color @Composable @ReadOnlyComposable get() = LocalIrisPalette.current.accentStrong
val IrisOnAccent: Color @Composable @ReadOnlyComposable get() = LocalIrisPalette.current.onAccent
val IrisViolet: Color @Composable @ReadOnlyComposable get() = LocalIrisPalette.current.violet
val IrisDanger: Color @Composable @ReadOnlyComposable get() = LocalIrisPalette.current.danger
val IrisText: Color @Composable @ReadOnlyComposable get() = LocalIrisPalette.current.text
val IrisTextSoft: Color @Composable @ReadOnlyComposable get() = LocalIrisPalette.current.textSoft
val IrisTextMuted: Color @Composable @ReadOnlyComposable get() = LocalIrisPalette.current.textMuted
val IrisBorder: Color @Composable @ReadOnlyComposable get() = LocalIrisPalette.current.border
val IrisBorderStrong: Color @Composable @ReadOnlyComposable get() = LocalIrisPalette.current.borderStrong
