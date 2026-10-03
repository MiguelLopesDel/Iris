package com.iris.app.data.sync

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

class OriginalReadsTest {

    private fun original(path: String) = "$path?requireOriginal=1"
    private var now = 0L
    private fun reads(allowed: Boolean = true) = OriginalReads({ allowed }, { now }, refusalMillis = 1_000)
    private fun refuse(reads: OriginalReads, item: String) =
        runCatching { reads.open(item, ::original) { throw SecurityException("refused") } }

    @Test
    fun `reads the original while allowed and the plain file without the permission`() {
        assertEquals("photo?requireOriginal=1", reads().open("photo", ::original) { it })
        assertEquals("photo", reads(allowed = false).open("photo", ::original) { it })
    }

    @Test
    fun `a refusal fails that read instead of quietly reading the redacted file`() {
        val reads = reads()
        val opened = mutableListOf<String>()
        assertThrows(OriginalRefusedException::class.java) {
            reads.open("photo", ::original) { uri ->
                opened += uri
                if (uri.endsWith("=1")) throw SecurityException("refused") else uri
            }
        }
        assertEquals(listOf("photo?requireOriginal=1"), opened)
        assertTrue(java.io.IOException::class.java.isAssignableFrom(OriginalRefusedException::class.java))
    }

    @Test
    fun `after a refusal the same item reads one version consistently`() {
        val reads = reads()
        refuse(reads, "photo")
        assertEquals("photo", reads.open("photo", ::original) { it })
        assertEquals("photo", reads.open("photo", ::original) { it })
    }

    @Test
    fun `a refusal on one item leaves the others with their location`() {
        val reads = reads()
        refuse(reads, "photo-a")
        assertTrue(reads.isRefused("photo-a"))
        assertFalse(reads.isRefused("photo-b"))
        assertEquals("photo-b?requireOriginal=1", reads.open("photo-b", ::original) { it })
    }

    @Test
    fun `a passing refusal expires and the original is tried again`() {
        val reads = reads()
        refuse(reads, "photo")
        now += 1_001
        assertFalse(reads.isRefused("photo"))
        assertEquals("photo?requireOriginal=1", reads.open("photo", ::original) { it })
    }
}
