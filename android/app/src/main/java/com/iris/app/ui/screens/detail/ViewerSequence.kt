package com.iris.app.ui.screens.detail

import com.iris.app.data.model.MediaRecord

/** One item the viewer can show: a photo or video in Iris, or one only on this phone. */
internal sealed interface ViewerItem {
    /** Stable across the pager's lifetime, unique within a sequence. */
    val key: String

    data class Server(val index: Int) : ViewerItem {
        override val key: String get() = "server:$index"
    }

    data class Device(val uri: String) : ViewerItem {
        override val key: String get() = "device:$uri"
    }

    companion object {
        /** The item a grid record opens: the phone's file when it has one, Iris's otherwise. */
        fun of(record: MediaRecord): ViewerItem = record.deviceUri?.let(::Device) ?: Server(record.index)
    }
}

/**
 * The items the viewer swipes through: those of the grid it was opened from,
 * in that grid's order (the gallery, with the phone's own photos among Iris's,
 * a search, an album, a person, similar photos).
 *
 * Held in memory between the grid and the viewer. After the process is
 * recreated it is gone, and the viewer shows the one item it was opened on.
 */
internal object ViewerSequence {
    @Volatile
    private var items: List<ViewerItem> = emptyList()

    fun set(items: List<ViewerItem>) {
        this.items = items.distinctBy { it.key }
    }

    fun setRecords(records: List<MediaRecord>) = set(records.map(ViewerItem::of))

    /** The sequence to page through, always containing [start]. */
    fun around(start: ViewerItem): List<ViewerItem> = items.takeIf { start in it } ?: listOf(start)
}
