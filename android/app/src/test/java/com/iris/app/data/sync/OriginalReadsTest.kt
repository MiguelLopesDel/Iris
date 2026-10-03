package com.iris.app.data.sync

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

class OriginalReadsTest {

    private fun original(path: String) = "$path?requireOriginal=1"

    @Test
    fun `reads the original while allowed`() {
        val reads = OriginalReads { true }
        assertEquals("photo?requireOriginal=1", reads.open("photo", ::original) { it })
    }

    @Test
    fun `reads the plain file without the permission`() {
        val reads = OriginalReads { false }
        assertEquals("photo", reads.open("photo", ::original) { it })
    }

    @Test
    fun `a refusal fails that read instead of quietly reading the redacted file`() {
        val reads = OriginalReads { true }
        val opened = mutableListOf<String>()
        assertThrows(OriginalRefusedException::class.java) {
            reads.open("photo", ::original) { uri ->
                opened += uri
                if (uri.endsWith("=1")) throw SecurityException("refused") else uri
            }
        }
        // The redacted file was not read in the same operation.
        assertEquals(listOf("photo?requireOriginal=1"), opened)
        assertTrue(reads.refused)
    }

    @Test
    fun `after a refusal every read uses the same, redacted, file`() {
        val reads = OriginalReads { true }
        runCatching { reads.open("photo", ::original) { throw SecurityException("refused") } }

        // A hash and every later chunk now read the same bytes.
        assertEquals("photo", reads.open("photo", ::original) { it })
        assertEquals("photo", reads.open("photo", ::original) { it })
    }

    @Test
    fun `a refusal is an IOException so the upload is retried, not failed`() {
        assertTrue(java.io.IOException::class.java.isAssignableFrom(OriginalRefusedException::class.java))
        assertFalse(OriginalReads { true }.refused)
    }
}
