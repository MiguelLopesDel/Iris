package com.iris.app.data

import org.junit.Assert.assertEquals
import org.junit.Test

class LibraryChangesTest {
    @Test
    fun `each change is a new version a late listener can still see`() {
        val before = LibraryChanges.version.value

        LibraryChanges.changed()
        LibraryChanges.changed()

        assertEquals(before + 2, LibraryChanges.version.value)
    }
}
