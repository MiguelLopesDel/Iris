package com.iris.app.ui.screens.gallery

/** Choosing several gallery items: a long press starts, each tap adds or removes. */
object GallerySelection {
    fun toggled(selected: Set<Int>, index: Int): Set<Int> =
        if (index in selected) selected - index else selected + index
}
