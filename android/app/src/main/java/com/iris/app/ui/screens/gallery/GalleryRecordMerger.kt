package com.iris.app.ui.screens.gallery

import com.iris.app.data.model.MediaRecord

/** Composes the local and remote pages shown together in the device gallery. */
class GalleryRecordMerger {

    /**
     * Keeps local-only records visible until their matching server row has
     * actually been loaded, then orders both sources by their media timestamp.
     */
    fun merge(serverRecords: List<MediaRecord>, deviceRecords: List<MediaRecord>): List<MediaRecord> {
        val serverHashes = serverRecords.mapNotNull { record ->
            record.contentHash
                ?.takeIf(String::isNotBlank)
                ?.lowercase()
        }.toSet()

        val localOnlyRecords = deviceRecords.filter { record ->
            val hash = record.contentHash?.lowercase()
            hash.isNullOrBlank() || hash !in serverHashes
        }

        return (serverRecords + localOnlyRecords)
            .sortedByDescending { it.fileMtime ?: 0.0 }
    }
}
