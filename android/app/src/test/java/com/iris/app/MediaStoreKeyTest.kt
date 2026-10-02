package com.iris.app

import com.iris.app.data.model.MediaStoreKey
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Test

class MediaStoreKeyTest {

    @Test
    fun `typed and files collection URIs of one item share a key`() {
        val key = MediaStoreKey.of("content://media/external/images/media/42")

        assertEquals(key, MediaStoreKey.of("content://media/external/file/42"))
        assertEquals(key, MediaStoreKey.of("content://media/external_primary/images/media/42"))
        assertEquals(MediaStoreKey.of("content://media/external/video/media/7"), MediaStoreKey.of("content://media/external/file/7"))
    }

    @Test
    fun `different items or volumes do not collide`() {
        assertNotEquals(MediaStoreKey.of("content://media/external/file/42"), MediaStoreKey.of("content://media/external/file/43"))
        assertNotEquals(MediaStoreKey.of("content://media/external/file/42"), MediaStoreKey.of("content://media/1234-abcd/file/42"))
    }

    @Test
    fun `a URI outside MediaStore is its own key`() {
        assertEquals("content://com.example/doc/1", MediaStoreKey.of("content://com.example/doc/1"))
    }
}
