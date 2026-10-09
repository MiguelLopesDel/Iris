package com.iris.app.ui.screens.detail

import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import java.time.Instant
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.OffsetDateTime
import java.time.ZoneId

/**
 * The viewer's title: when and where the photo was taken, the way people
 * recall a photo and the way the phone's gallery names it ("8 de out." over
 * the time), instead of its file name.
 */
internal data class ViewerTitle(val headline: String, val subline: String?) {
    companion object {
        // Fixed tables: the platform's short names differ between Android
        // versions and the JVM ("out" or "out."), and the title must not.
        private val months = listOf(
            "jan.", "fev.", "mar.", "abr.", "mai.", "jun.",
            "jul.", "ago.", "set.", "out.", "nov.", "dez.",
        )
        private val weekdays = listOf("Seg.", "Ter.", "Qua.", "Qui.", "Sex.", "Sáb.", "Dom.")

        /**
         * [capturedAt] is an ISO capture time. Explicit offsets are converted
         * to the device's zone; values without an offset remain local times.
         * [fileMtime] in epoch seconds stands in when there is none.
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
            val taken = takenAt(capturedAt, fileMtime, zone)
                ?: return ViewerTitle(fileName, place?.ifBlank { null })
            val date = taken.toLocalDate()
            val day = when {
                date == today -> "Hoje"
                date == today.minusDays(1) -> "Ontem"
                // The year only when it is not this one, as a gallery does.
                date.year == today.year -> "${date.dayOfMonth} de ${months[date.monthValue - 1]}"
                else -> "${date.dayOfMonth} de ${months[date.monthValue - 1]} de ${date.year}"
            }
            val sub = listOfNotNull(time(taken), place?.ifBlank { null }).joinToString(" · ")
            return ViewerTitle(day, sub)
        }

        /** The capture time in full for the information panel ("Qui., 8 de out. de 2026 • 07:23"); null when unknown. */
        fun fullDate(capturedAt: String?, fileMtime: Double?, zone: ZoneId = ZoneId.systemDefault()): String? {
            val taken = takenAt(capturedAt, fileMtime, zone) ?: return null
            val date = taken.toLocalDate()
            return "${weekdays[date.dayOfWeek.value - 1]}, ${date.dayOfMonth} de " +
                "${months[date.monthValue - 1]} de ${date.year} • ${time(taken)}"
        }

        private fun time(taken: LocalDateTime) = "%02d:%02d".format(taken.hour, taken.minute)

        private fun takenAt(capturedAt: String?, fileMtime: Double?, zone: ZoneId): LocalDateTime? =
            parse(capturedAt, zone)
                ?: fileMtime?.takeIf { it > 0.0 }?.let {
                    LocalDateTime.ofInstant(Instant.ofEpochMilli((it * 1000).toLong()), zone)
                }

        private fun parse(value: String?, zone: ZoneId): LocalDateTime? {
            if (value.isNullOrBlank()) return null
            return runCatching { OffsetDateTime.parse(value).atZoneSameInstant(zone).toLocalDateTime() }
                .getOrNull()
                ?: runCatching { LocalDateTime.parse(value) }.getOrNull()
        }
    }
}

/** Reads a field out of the server's free-form metadata object. */
internal fun JsonObject.textOf(key: String): String? =
    (this[key] as? JsonPrimitive)?.content?.ifBlank { null }
