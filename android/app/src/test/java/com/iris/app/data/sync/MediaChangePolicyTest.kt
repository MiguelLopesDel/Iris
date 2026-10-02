package com.iris.app.data.sync

import com.iris.app.data.sync.MediaChangePolicy.Verdict
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class MediaChangePolicyTest {

    private val fingerprint = MediaFingerprint(size = 1_000L, dateModifiedSeconds = 1_700_000_000L, generation = 42L)
    private fun known(fp: MediaFingerprint = fingerprint, verifiedAt: Long? = 100L) =
        KnownMedia(jobId = 1L, sha256 = "a".repeat(64), fingerprint = fp, verifiedAt = verifiedAt)

    @Test
    fun `an item not in the queue is new`() {
        assertEquals(Verdict.NEW, MediaChangePolicy.decide(null, fingerprint, null))
    }

    @Test
    fun `the same fingerprint needs nothing`() {
        assertEquals(Verdict.UNCHANGED, MediaChangePolicy.decide(known(), fingerprint, null))
    }

    @Test
    fun `an in-place edit that changes the size is verified`() {
        assertEquals(Verdict.VERIFY, MediaChangePolicy.decide(known(), fingerprint.copy(size = 1_001L), null))
    }

    @Test
    fun `an overwrite that keeps the size but bumps the generation is verified`() {
        assertEquals(Verdict.VERIFY, MediaChangePolicy.decide(known(), fingerprint.copy(generation = 43L), null))
    }

    @Test
    fun `without generations a changed modification time is verified`() {
        val old = fingerprint.copy(generation = 0L)
        assertEquals(Verdict.VERIFY, MediaChangePolicy.decide(known(old), old.copy(dateModifiedSeconds = 1_700_000_100L), null))
        assertEquals(Verdict.UNCHANGED, MediaChangePolicy.decide(known(old), old, null))
    }

    @Test
    fun `a row from before fingerprints adopts one when the size matches`() {
        val legacy = fingerprint.copy(dateModifiedSeconds = null)
        assertEquals(Verdict.ADOPT_FINGERPRINT, MediaChangePolicy.decide(known(legacy), fingerprint, null))
        assertEquals(Verdict.VERIFY, MediaChangePolicy.decide(known(legacy), fingerprint.copy(size = 5L), null))
    }

    @Test
    fun `a full verification rehashes rows not verified since it started`() {
        assertEquals(Verdict.VERIFY, MediaChangePolicy.decide(known(verifiedAt = 100L), fingerprint, 200L))
        assertEquals(Verdict.VERIFY, MediaChangePolicy.decide(known(verifiedAt = null), fingerprint, 200L))
        // Rows already verified in this run are skipped, so an interrupted run resumes.
        assertEquals(Verdict.UNCHANGED, MediaChangePolicy.decide(known(verifiedAt = 300L), fingerprint, 200L))
    }

    @Test
    fun `a changed MediaStore version on a known volume is a rebuild`() {
        assertTrue(MediaChangePolicy.mediaStoreRebuilt(mapOf("external_primary" to "v1"), mapOf("external_primary" to "v2")))
    }

    @Test
    fun `a first scan or a new volume is not a rebuild`() {
        assertFalse(MediaChangePolicy.mediaStoreRebuilt(emptyMap(), mapOf("external_primary" to "v1")))
        assertFalse(MediaChangePolicy.mediaStoreRebuilt(mapOf("external_primary" to "v1"), mapOf("external_primary" to "v1", "1234-abcd" to "x")))
        // A removed card leaves no current version to compare.
        assertFalse(MediaChangePolicy.mediaStoreRebuilt(mapOf("1234-abcd" to "x"), emptyMap()))
    }
}
