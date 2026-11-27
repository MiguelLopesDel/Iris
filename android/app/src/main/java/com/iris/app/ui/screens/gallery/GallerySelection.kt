package com.iris.app.ui.screens.gallery

import com.iris.app.R
import com.iris.app.ui.text.appPlural
import com.iris.app.ui.text.appString

/** Choosing several gallery items: a long press starts, each tap adds or removes. */
object GallerySelection {
    fun toggled(selected: Set<Int>, index: Int): Set<Int> =
        if (index in selected) selected - index else selected + index

    /** What sending several items to a group did, in one sentence. */
    fun addedSummary(spaceName: String, created: Int, already: Int, failed: Int): String {
        val parts = buildList {
            if (created > 0) add(appPlural(R.plurals.gallery_added_sent, created, created, spaceName))
            if (already > 0) add(appPlural(R.plurals.gallery_added_already, already, already))
            if (failed > 0) add(appPlural(R.plurals.gallery_added_failed, failed, failed))
        }
        return parts.joinToString("; ").ifEmpty { appString(R.string.gallery_added_none) }
    }
}
