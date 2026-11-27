package com.iris.app.data.update

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class AppUpdatesTest {

    @Test
    fun `only a strictly higher stable version is an update`() {
        assertTrue(isNewer("0.10.0", "0.9.0")) // numeric, not alphabetical
        assertTrue(isNewer("1.0.0", "0.99.99"))
        assertTrue(isNewer("v0.9.1", "0.9.0"))
        assertFalse(isNewer("0.9.0", "0.9.0"))
        assertFalse(isNewer("0.8.5", "0.9.0"))
        assertFalse(isNewer("1.0.0-rc.1", "0.9.0"))
        assertFalse(isNewer("1.0.0", "0.9.0-dev"))
    }

    private fun release(tag: String, assets: String, flags: String = "") = """
        {"tag_name": "$tag", "html_url": "https://example.com/r", $flags
         "assets": [$assets], "body": "notes"}
    """

    private val apk = """{"name": "iris-0.10.0.apk", "browser_download_url": "https://example.com/iris-0.10.0.apk"}"""

    @Test
    fun `a release with an APK becomes an app release`() {
        val parsed = parseLatestRelease(
            release("v0.10.0", """{"name": "notes.txt", "browser_download_url": "https://example.com/n"}, $apk""")
        )
        assertEquals(AppRelease("0.10.0", "https://example.com/iris-0.10.0.apk"), parsed)
    }

    @Test
    fun `drafts, pre-releases, odd tags and releases without an APK are ignored`() {
        assertNull(parseLatestRelease(release("v0.10.0", apk, """"draft": true,""")))
        assertNull(parseLatestRelease(release("v0.10.0", apk, """"prerelease": true,""")))
        assertNull(parseLatestRelease(release("v0.10.0-rc.1", apk)))
        assertNull(parseLatestRelease(release("nightly", apk)))
        assertNull(parseLatestRelease(release("v0.10.0", "")))
        assertNull(parseLatestRelease("not json"))
    }

    @Test
    fun `an APK served over plain HTTP is not offered`() {
        val insecure = """{"name": "iris.apk", "browser_download_url": "http://example.com/iris.apk"}"""
        assertNull(parseLatestRelease(release("v0.10.0", insecure)))
    }
}
