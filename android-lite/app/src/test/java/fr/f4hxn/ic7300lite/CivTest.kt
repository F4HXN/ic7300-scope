package fr.f4hxn.ic7300lite

import org.junit.Assert.*
import org.junit.Test

class CivTest {
    @Test fun frequence() {
        assertArrayEquals(intArrayOf(0x00, 0x40, 0x07, 0x14, 0x00), versBcdLe(14_074_000))
        assertEquals(14_074_000L, bcdLe(listOf(0x00, 0x40, 0x07, 0x14, 0x00)))
        assertEquals("14.074.000", texteFrequence(14_074_000))
        assertEquals(14_074_000L, lireFrequence("14.074.000"))
        assertEquals(7_100_000L, lireFrequence("7100000"))
        assertNull(lireFrequence("14,074"))
        assertNull(lireFrequence(""))
        assertNull(lireFrequence("999000000"))
    }

    @Test fun smetre() {
        assertEquals(120L, bcdBe(listOf(0x01, 0x20)))
        assertEquals(241L, bcdBe(listOf(0x02, 0x41)))
        assertEquals("S0", texteS(0))
        assertEquals("S9", texteS(120))
        assertEquals("S9+60 dB", texteS(241))
    }

    @Test fun memoire() {
        val vfo = listOf(0x00, 0x40, 0x07, 0x14, 0x00, 0x01, 0x01, 0x10, 0, 8, 0x85, 0, 8, 0x85)
        val contenu = listOf(0) + vfo + vfo + "FT8 20m   ".map { it.code }
        val m = lireMemoire(12, contenu)!!
        assertEquals(Memoire(12, "FT8 20m", 14_074_000, "USB-D"), m)
        assertNull(lireMemoire(1, listOf(0xFF)))
    }

    @Test fun trameComplete() {
        assertArrayEquals(byteArrayOf(0xFE.toByte(), 0xFE.toByte(), 0x94.toByte(), 0xE0.toByte(), 0x03, 0xFD.toByte()), trame(0x03))
        assertEquals(0x12, versBcdOctet(12))
    }

    @Test fun volume() {
        assertEquals(0f, gainDuVolume(0), 0f)
        assertEquals(1f, gainDuVolume(100 / 3), 0.1f)
        assertEquals(100f, gainDuVolume(100), 0.01f)
        val o = byteArrayOf(0x00, 0x40, 0x00, 0xC0.toByte())    // 16384, -16384
        amplifier(o, 4, 4f)
        assertArrayEquals(byteArrayOf(0xFF.toByte(), 0x7F, 0x00, 0x80.toByte()), o)  // bridé, sans déborder
    }
}
