package com.iris.app.ui.screens.gallery

/** Choosing several gallery items: a long press starts, each tap adds or removes. */
object GallerySelection {
    fun toggled(selected: Set<Int>, index: Int): Set<Int> =
        if (index in selected) selected - index else selected + index

    /** What sending several items to a group did, in one sentence. */
    fun addedSummary(spaceName: String, created: Int, already: Int, failed: Int): String {
        val parts = buildList {
            if (created > 0) add(if (created == 1) "1 enviada para $spaceName" else "$created enviadas para $spaceName")
            if (already > 0) add(if (already == 1) "1 já estava lá" else "$already já estavam lá")
            if (failed > 0) add(if (failed == 1) "1 não pôde ser enviada" else "$failed não puderam ser enviadas")
        }
        return parts.joinToString("; ").ifEmpty { "Nada foi enviado" }
    }
}
