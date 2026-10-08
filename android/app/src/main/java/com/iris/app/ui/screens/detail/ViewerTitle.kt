package com.iris.app.ui.screens.detail

import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import java.time.Instant
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale

/**
 * The viewer's title: when and where the photo was taken, the way people
 * recall a photo, instead of its file name.
 */
internal data class ViewerTitle(val headline: String, val subline: String?) {
    companion object {
        private val locale = Locale("pt", "BR")
        private val dateFormat = DateTimeFormatter.ofPattern("d 'de' MMMM 'de' yyyy", locale)
        private val timeFormat = DateTimeFormatter.ofPattern("HH:mm", locale)

        /**
         * [capturedAt] is the server's capture time (ISO, the camera's local
         * time); [fileMtime] in epoch seconds stands in when there is none.
         * With neither, the file name is all there is.
         */
        fun of(
            capturedAt: String?,
            fileMtime: Double?,
            place: String?,
            fileName: String,
            today: LocalDate = LocalDate.now(),
            zone: ZoneId = ZoneId.systemDefault(),
        ): ViewerTitle {
            val taken = parse(capturedAt)
                ?: fileMtime?.takeIf { it > 0.0 }?.let {
                    LocalDateTime.ofInstant(Instant.ofEpochMilli((it * 1000).toLong()), zone)
                }
                ?: return ViewerTitle(fileName, place?.ifBlank { null })
            val day = when (taken.toLocalDate()) {
                today -> "Hoje"
                today.minusDays(1) -> "Ontem"
                else -> taken.format(dateFormat)
            }
            val sub = listOfNotNull(taken.format(timeFormat), place?.ifBlank { null }).joinToString(" · ")
            return ViewerTitle(day, sub)
        }

        private val fullFormat = DateTimeFormatter.ofPattern("EEEE, d 'de' MMMM 'de' yyyy 'às' HH:mm", locale)

        /** The capture time in full, for the information panel; null when unknown. */
        fun fullDate(capturedAt: String?, fileMtime: Double?, zone: ZoneId = ZoneId.systemDefault()): String? {
            val taken = parse(capturedAt)
                ?: fileMtime?.takeIf { it > 0.0 }?.let {
                    LocalDateTime.ofInstant(Instant.ofEpochMilli((it * 1000).toLong()), zone)
                }
                ?: return null
            return taken.format(fullFormat).replaceFirstChar { it.uppercase(locale) }
        }

        private fun parse(value: String?): LocalDateTime? {
            if (value.isNullOrBlank()) return null
            return runCatching { LocalDateTime.parse(value.take(19)) }.getOrNull()
        }
    }
}

/** Reads a field out of the server's free-form metadata object. */
internal fun JsonObject.textOf(key: String): String? =
    (this[key] as? JsonPrimitive)?.content?.ifBlank { null }
