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

    @Test fun pas() {
        assertEquals(14_075_000L, frequenceDecalee(14_074_123, 1000, 1))
        assertEquals(14_074_000L, frequenceDecalee(14_074_123, 1000, -1))
        assertEquals(14_073_000L, frequenceDecalee(14_074_000, 1000, -1))
        assertEquals(30_000L, frequenceDecalee(30_000, 1000, -1))
        assertEquals(74_800_000L, frequenceDecalee(74_800_000, 1_000_000, 1))
    }

    @Test fun spectre() {
        val a = AssembleurSpectre()
        val bords = listOf(0) + versBcdLe(14_074_000).toList() + versBcdLe(25_000).toList()
        assertNull(a.ajouter(1, 3, bords))
        assertNull(a.ajouter(2, 3, listOf(10, 20)))
        val r = a.ajouter(3, 3, listOf(30))!!
        assertArrayEquals(intArrayOf(10, 20, 30), r.points)
        assertEquals(14_049_000L, r.bas)
        assertEquals(14_099_000L, r.haut)
        // trame perdue : balayage abandonné
        a.ajouter(1, 3, bords)
        assertNull(a.ajouter(3, 3, listOf(30)))
    }

    @Test fun cascade() {
        assertEquals(0xFF05080A.toInt(), couleurCascade(0, 160))
        assertEquals(0xFFF44336.toInt(), couleurCascade(160, 160))
        assertEquals(0xFFF44336.toInt(), couleurCascade(500, 160))
        val c = Cascade(3)
        c.ajouter(intArrayOf(0, 160), 160)
        c.ajouter(intArrayOf(160, 0), 160)
        assertEquals(6, c.pixels.size)
        assertEquals(0xFFF44336.toInt(), c.pixels[0])       // récent en haut
        assertEquals(0xFF05080A.toInt(), c.pixels[2])       // ancien, une ligne plus bas
        assertEquals(0xFFF44336.toInt(), c.pixels[3])
        c.ajouter(intArrayOf(1, 2, 3), 160)                 // autre largeur : on repart
        assertEquals(9, c.pixels.size)
    }

    @Test fun regle() {
        assertEquals(listOf(7_354_000L, 7_356_000L, 7_358_000L, 7_360_000L, 7_362_000L), graduations(7_353_500, 7_363_500))
        for ((a, b) in listOf(7_357_500L to 7_362_500L, 7_353_500L to 7_363_500L, 7_140_500L to 7_190_500L, 6_628_500L to 7_628_500L)) {
            val g = graduations(a, b)
            assertTrue("$a $g", g.size in 3..8 && g.all { it in a..b })
        }
        assertTrue(graduations(5, 5).isEmpty())
        assertEquals("7.355", texteGraduation(7_355_000))
        assertEquals("14.074", texteGraduation(14_074_000))
    }

    @Test fun doigt() {
        assertEquals(100L, pasTactile(1_000_000, 7_140_500, 7_190_500))   // 1 MHz sur ±25 kHz
        assertEquals(10L, pasTactile(1_000, 7_353_500, 7_363_500))        // ±5 kHz
        assertEquals(10L, pasTactile(10, 6_628_500, 7_628_500))           // le pas fin reste
        assertEquals(5_000L, pasTactile(1_000_000, 6_628_500, 7_628_500)) // ±500 kHz
    }
}
