package com.iris.app.ui.screens.gallery

import com.iris.app.data.model.MediaRecord

/** Composes the local and remote pages shown together in the device gallery. */
class GalleryRecordMerger {

    /**
     * Keeps local-only records visible until their matching server row has
     * actually been loaded, then orders both sources by their media timestamp.
     * A server row whose file is also on the phone carries the phone's copy
     * ([MediaRecord.localCopyUri]), so it is drawn from the phone.
     */
    fun merge(serverRecords: List<MediaRecord>, deviceRecords: List<MediaRecord>): List<MediaRecord> {
        val localByHash = deviceRecords
            .filter { it.deviceUri != null && !it.contentHash.isNullOrBlank() }
            .associateBy { it.contentHash!!.lowercase() }
        val serverHashes = serverRecords.mapNotNull { record ->
            record.contentHash
                ?.takeIf(String::isNotBlank)
                ?.lowercase()
        }.toSet()

        val withLocalCopies = serverRecords.map { record ->
            val local = record.contentHash?.lowercase()?.let(localByHash::get)
            if (local != null) record.copy(localCopyUri = local.deviceUri) else record
        }
        val localOnlyRecords = deviceRecords.filter { record ->
            val hash = record.contentHash?.lowercase()
            hash.isNullOrBlank() || hash !in serverHashes
        }

        return (withLocalCopies + localOnlyRecords)
            .sortedByDescending { it.fileMtime ?: 0.0 }
    }
}
