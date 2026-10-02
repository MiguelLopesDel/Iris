package com.iris.app.data.sync

/**
 * What MediaStore says about an item's current bytes, cheaply: no file read.
 *
 * [generation] is MediaStore's change counter (API 30+), 0 where the platform
 * has none. [dateModifiedSeconds] is null for queue rows written before
 * fingerprints were recorded.
 */
data class MediaFingerprint(
    val size: Long,
    val dateModifiedSeconds: Long?,
    val generation: Long,
)

/** What the queue already knows about one local item. */
data class KnownMedia(
    val jobId: Long,
    val sha256: String,
    val fingerprint: MediaFingerprint,
    /** When this row's hash was last confirmed against the file, or null. */
    val verifiedAt: Long?,
)

/**
 * Decides whether a scanned item needs hashing.
 *
 * The MediaStore id says which item this is, not whether its bytes are the
 * ones hashed before: an editor saving over the file, a computer overwriting it
 * over USB, or a rebuilt MediaStore that reassigned ids all keep a URI while
 * the content changes. The fingerprint catches those cheaply; the hash stays the
 * authority whenever it does not match.
 */
object MediaChangePolicy {

    enum class Verdict {
        /** Not in the queue: hash and enqueue. */
        NEW,
        /** Same fingerprint: nothing to do. */
        UNCHANGED,
        /** A row from before fingerprints whose size still matches: record the fingerprint, no hashing. */
        ADOPT_FINGERPRINT,
        /** The fingerprint changed or a full verification is running: hash and compare. */
        VERIFY,
    }

    /**
     * [fullVerificationStartedAt] is set while a full verification runs (after
     * MediaStore was rebuilt, or the periodic one): every row not verified since
     * it started is hashed again, whatever its fingerprint says.
     */
    fun decide(known: KnownMedia?, current: MediaFingerprint, fullVerificationStartedAt: Long?): Verdict {
        if (known == null) return Verdict.NEW
        if (fullVerificationStartedAt != null && (known.verifiedAt ?: 0L) < fullVerificationStartedAt) {
            return Verdict.VERIFY
        }
        val stored = known.fingerprint
        if (stored.size != current.size) return Verdict.VERIFY
        if (stored.generation > 0L && current.generation > 0L && stored.generation != current.generation) {
            return Verdict.VERIFY
        }
        val storedDate = stored.dateModifiedSeconds ?: return Verdict.ADOPT_FINGERPRINT
        return if (current.dateModifiedSeconds == storedDate) Verdict.UNCHANGED else Verdict.VERIFY
    }

    /**
     * True when MediaStore was rebuilt on some volume since the last scan: its
     * ids and generations restarted, so a stored id may now name another item.
     * A volume seen for the first time (a new SD card, or no record yet) is
     * not a rebuild.
     */
    fun mediaStoreRebuilt(stored: Map<String, String>, current: Map<String, String>): Boolean =
        current.any { (volume, version) -> stored[volume]?.let { it != version } ?: false }

    /** How often everything is hashed again, to catch changes MediaStore never reported. */
    const val FULL_VERIFICATION_INTERVAL_MILLIS = 7L * 24 * 60 * 60 * 1000
}
