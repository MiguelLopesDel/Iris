package com.iris.app.data

import org.junit.Assert.assertEquals
import org.junit.Test

class SharedMediaFileTest {

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
