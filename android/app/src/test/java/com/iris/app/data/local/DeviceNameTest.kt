package com.iris.app.data.local

import org.junit.Assert.assertEquals
import org.junit.Test

class DeviceNameTest {

    @Test
    fun `the maker comes before the model`() {
        assertEquals("Samsung SM-S911B", DeviceName.model("samsung", "SM-S911B"))
    }

    @Test
    fun `the maker is not repeated when the model already starts with it`() {
        assertEquals("Google Pixel 8", DeviceName.model("Google", "Pixel 8"))
        assertEquals("motorola edge 40", DeviceName.model("motorola", "motorola edge 40"))
    }

    @Test
    fun `missing parts never give an empty name`() {
        assertEquals("Android", DeviceName.model(null, null))
        assertEquals("Acme", DeviceName.model("acme", ""))
        assertEquals("Phone 2", DeviceName.model("", "Phone 2"))
    }
}
