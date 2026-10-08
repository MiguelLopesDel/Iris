package com.iris.app.data

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.nio.file.Files

class SharedMediaFileTest {

    @Test
    fun `each share gets a unique directory and old entries are pruned`() {
        val root = Files.createTempDirectory("iris-shared-media-test").toFile()
        try {
            val oldFile = root.resolve("old.jpg").apply {
                writeText("old")
                setLastModified(1_000L)
            }
            val recentFile = root.resolve("recent.jpg").apply {
                writeText("recent")
                setLastModified(150_000_000L)
            }

            val first = SharedMediaFile.createShareDirectory(root, nowMillis = 200_000_000L)
            val second = SharedMediaFile.createShareDirectory(root, nowMillis = 200_000_000L)

            assertNotEquals(first, second)
            assertTrue(first.isDirectory)
            assertTrue(second.isDirectory)
            assertFalse(oldFile.exists())
            assertTrue(recentFile.exists())
        } finally {
            root.deleteRecursively()
        }
    }

    @Test
    fun `a plain name is kept`() {
        assertEquals("IMG_0001.jpg", SharedMediaFile.safeName("IMG_0001.jpg"))
    }

    @Test
    fun `a name cannot climb out of the share folder`() {
        assertEquals("passwd", SharedMediaFile.safeName("../../etc/passwd"))
        assertEquals("a_b.jpg", SharedMediaFile.safeName("a:b.jpg"))
        assertEquals("hidden.jpg", SharedMediaFile.safeName(".hidden.jpg"))
    }

    @Test
    fun `an empty name still gives a file`() {
        assertEquals("midia", SharedMediaFile.safeName(" "))
    }

    @Test
    fun `the server's type wins when it names one`() {
        assertEquals("image/png", SharedMediaFile.mimeType("image/png; charset=binary", "x.jpg"))
    }

    @Test
    fun `a generic type falls back to the extension`() {
        assertEquals("image/jpeg", SharedMediaFile.mimeType("application/octet-stream", "IMG_0001.JPG"))
        assertEquals("video/mp4", SharedMediaFile.mimeType(null, "VID_0001.mp4"))
        assertEquals("application/octet-stream", SharedMediaFile.mimeType(null, "file"))
    }
}
