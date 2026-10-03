package com.iris.app.data.sync

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

class OriginalReadsTest {

    private fun original(path: String) = "$path?requireOriginal=1"
    private var now = 0L
    private var refusing = false
    private fun reads(allowed: Boolean = true) = OriginalReads({ allowed }, { now }, refusalMillis = 1_000)
    private val opener: (String) -> String = { uri ->
        if (refusing && uri.endsWith("=1")) throw SecurityException("refused") else uri
    }

    @Test
    fun `hashing prefers the original and reports which version it read`() {
        assertEquals("photo?requireOriginal=1" to true, reads().openPreferred("photo", ::original, opener))
        assertEquals("photo" to false, reads(allowed = false).openPreferred("photo", ::original, opener))
    }

    @Test
    fun `hashing falls back to the redacted file when the original is refused, and says so`() {
        val reads = reads()
        refusing = true
        assertEquals("photo" to false, reads.openPreferred("photo", ::original, opener))
        assertTrue(reads.isRefused("photo"))
    }

    @Test
    fun `an upload reads exactly its hashed version and never the other`() {
        val reads = reads()
        assertEquals("photo?requireOriginal=1", reads.openExact("photo", ::original, wantOriginal = true, opener))
        assertEquals("photo", reads.openExact("photo", ::original, wantOriginal = false, opener))
        refusing = true
        assertThrows(OriginalRefusedException::class.java) {
            reads.openExact("photo", ::original, wantOriginal = true, opener)
        }
        assertThrows(OriginalRefusedException::class.java) {
            reads(allowed = false).openExact("photo", ::original, wantOriginal = true, opener)
        }
    }

    @Test
    fun `a refusal on one item leaves the others with their location`() {
        val reads = reads()
        refusing = true
        reads.openPreferred("photo-a", ::original, opener)
        refusing = false
        assertEquals("photo-b?requireOriginal=1" to true, reads.openPreferred("photo-b", ::original, opener))
    }

    @Test
    fun `an upload converges when the refusal mark expires mid-way`() {
        val reads = reads()
        // 1. Hashed from the original.
        var hashedOriginal = reads.openPreferred("photo", ::original, opener).second
        assertTrue(hashedOriginal)
        // 2. A chunk finds the original refused: the read fails, nothing is sent.
        refusing = true
        assertThrows(OriginalRefusedException::class.java) {
            reads.openExact("photo", ::original, hashedOriginal, opener)
        }
        // 3. The item is re-hashed: the redacted file, recorded as such.
        hashedOriginal = reads.openPreferred("photo", ::original, opener).second
        assertFalse(hashedOriginal)
        // 4. The mark expires and the original is readable again before the upload ends:
        //    its chunks still read the version it declared.
        now += 1_001
        refusing = false
        assertEquals("photo", reads.openExact("photo", ::original, hashedOriginal, opener))
        // 5. A new hash (a later version) prefers the original again.
        assertEquals("photo?requireOriginal=1" to true, reads.openPreferred("photo", ::original, opener))
    }

    @Test
    fun `a refusal is an IOException, so the item is retried`() {
        assertTrue(java.io.IOException::class.java.isAssignableFrom(OriginalRefusedException::class.java))
    }
}
