package com.iris.app

import com.iris.app.ui.components.decodeThumbGrid
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * Cross-language contract with the Iris server's core/thumb_hash.py.
 *
 * The fixtures below are literal output from that encoder. If the two sides
 * ever drift apart the placeholder silently stops appearing — no crash, no log
 * — so this is the only place the mismatch would be caught.
 */
class ThumbHashTest {

    // encode_thumb_hash(Image.new("RGB", (300, 300), (255, 0, 0)))
    private val serverRed =
        "/wAA/wAA/wAA/wAA/wAA/wAA/wAA/wAA/wAA/wAA/wAA/wAA/wAA/wAA/wAA/wAA/wAA/wAA" +
            "/wAA/wAA/wAA/wAA/wAA/wAA/wAA/wAA/wAA/wAA/wAA/wAA/wAA/wAA/wAA/wAA/wAA/wAA"

    // encode_thumb_hash(Image.new("RGB", (300, 300), (10, 150, 220)))
    private val serverBlue =
        "CpbcCpbcCpbcCpbcCpbcCpbcCpbcCpbcCpbcCpbcCpbcCpbcCpbcCpbcCpbcCpbcCpbcCpbc" +
            "CpbcCpbcCpbcCpbcCpbcCpbcCpbcCpbcCpbcCpbcCpbcCpbcCpbcCpbcCpbcCpbcCpbcCpbc"

    @Test
    fun `derives a 6x6 grid from real server output`() {
        val grid = decodeThumbGrid(serverRed)!!

        assertEquals(6, grid.side)
        assertEquals(36, grid.pixels.size)
    }

    @Test
    fun `recovers the colour the server encoded`() {
        val grid = decodeThumbGrid(serverBlue)!!

        // Opaque ARGB for (10, 150, 220).
        val expected = (0xFF shl 24) or (10 shl 16) or (150 shl 8) or 220
        assertEquals(expected, grid.pixels.first())
        assertEquals(expected, grid.pixels.last())
    }

    @Test
    fun `channel order is not swapped`() {
        // Red would still decode "fine" with swapped channels if the fixture
        // were grey, so assert an asymmetric colour channel by channel.
        val grid = decodeThumbGrid(serverBlue)!!
        val pixel = grid.pixels.first()

        assertEquals(10, (pixel shr 16) and 0xFF)
        assertEquals(150, (pixel shr 8) and 0xFF)
        assertEquals(220, pixel and 0xFF)
    }

    @Test
    fun `side is derived from length, not hardcoded`() {
        // An 8x8 grid from a future server version must still decode.
        val eightBySixtyFour = ByteArray(8 * 8 * 3) { 0x7F }
        val encoded = java.util.Base64.getEncoder().encodeToString(eightBySixtyFour)

        assertEquals(8, decodeThumbGrid(encoded)!!.side)
    }

    @Test
    fun `records without a placeholder decode to null`() {
        assertNull(decodeThumbGrid(null))
        assertNull(decodeThumbGrid(""))
    }

    @Test
    fun `malformed input decodes to null instead of throwing`() {
        assertNull(decodeThumbGrid("not base64 at all!!"))
        // Valid base64, but not a whole square of RGB triples.
        assertNull(decodeThumbGrid(java.util.Base64.getEncoder().encodeToString(ByteArray(7))))
    }
}
