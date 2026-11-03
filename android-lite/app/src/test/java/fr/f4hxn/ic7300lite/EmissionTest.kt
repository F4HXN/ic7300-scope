package fr.f4hxn.ic7300lite

import java.net.ServerSocket
import java.util.Collections
import kotlin.concurrent.thread
import org.junit.Assert.*
import org.junit.Test

class EmissionTest {
    private val lecture = listOf(0x15, 0x02)

    /** Radio simulée : note les commandes hors interrogations, menu DATA OFF MOD à `menu`. */
    private fun radio(serveur: ServerSocket, menu: Int, recu: MutableList<List<Int>>) = thread(isDaemon = true) {
        val c = serveur.accept()
        val e = c.getInputStream()
        val t = ArrayList<Int>()
        fun repondre(vararg d: Int) = c.getOutputStream().write((listOf(0xFE, 0xFE, 0xE0, 0x94) + d.toList() + 0xFD).map { it.toByte() }.toByteArray())
        while (true) {
            val b = e.read()
            if (b < 0) return@thread
            if (b != 0xFD) { t.add(b); continue }
            val d = t.dropWhile { it == 0xFE }.drop(2)
            t.clear()
            when (d) {
                lecture -> repondre(0x15, 0x02, 0x01, 0x20)
                listOf(0x1C, 0x00) -> repondre(0x1C, 0x00, 0x00)
                listOf(0x1C, 0x01) -> repondre(0x1C, 0x01, 0x00)
                listOf(0x14, 0x0A) -> repondre(0x14, 0x0A, 0x02, 0x55)
                listOf(0x1A, 0x05, 0x00, 0x66) -> { recu.add(d); repondre(0x1A, 0x05, 0x00, 0x66, menu) }
                listOf(0x03), listOf(0x04), listOf(0x1A, 0x06), listOf(0x27, 0x11, 0x01) -> repondre(0xFB)
                else -> { recu.add(d); repondre(0xFB) }
            }
        }
    }

    private fun attendre(ms: Long, c: () -> Boolean): Boolean {
        val fin = System.currentTimeMillis() + ms
        while (System.currentTimeMillis() < fin) { if (c()) return true; Thread.sleep(20) }
        return false
    }

    private fun connecte(maxMs: Long, menu: Int, recu: MutableList<List<Int>>): CatClient {
        val s = ServerSocket(0)
        radio(s, menu, recu)
        val c = CatClient(maxMs)
        c.connecter("127.0.0.1", s.localPort)
        assertTrue(attendre(3000) { c.enMarche })
        return c
    }

    private val menuUsb = listOf(0x1A, 0x05, 0x00, 0x66, 0x03)
    private val ptt1 = listOf(0x1C, 0x00, 0x01)
    private val ptt0 = listOf(0x1C, 0x00, 0x00)

    @Test fun menuMisPuisRemis() {
        val r = Collections.synchronizedList(mutableListOf<List<Int>>())
        val c = connecte(60_000, 0x01, r)
        c.emettre(true)
        assertTrue(attendre(1000) { ptt1 in r })
        assertTrue(r.indexOf(menuUsb) in 0 until r.indexOf(ptt1))     // l'entrée avant le PTT
        c.regler(14_000_000)
        Thread.sleep(100)
        assertFalse(r.any { it.firstOrNull() == 0x05 })                // pas de changement de fréquence en émission
        c.emettre(false)
        assertTrue(attendre(1000) { listOf(0x1A, 0x05, 0x00, 0x66, 0x01) in r })
        assertTrue(r.indexOf(ptt0) < r.indexOf(listOf(0x1A, 0x05, 0x00, 0x66, 0x01)))
        c.fermer()
    }

    @Test fun relacheAvantLePtt() {
        val r = Collections.synchronizedList(mutableListOf<List<Int>>())
        val c = connecte(60_000, 0x01, r)
        c.emettre(true)
        c.emettre(false)
        Thread.sleep(400)
        assertFalse(ptt1 in r)      // appui très bref : jamais de porteuse
        c.fermer()
    }

    @Test fun limiteDeDuree() {
        val r = Collections.synchronizedList(mutableListOf<List<Int>>())
        val c = connecte(500, 0x03, r)
        c.emettre(true)
        assertTrue(attendre(1000) { ptt1 in r })
        assertTrue(attendre(2000) { ptt0 in r })
        assertTrue(c.limiteAtteinte)
        assertFalse(c.enEmission)
        c.fermer()
    }

    @Test fun fermerCoupeLEmission() {
        val r = Collections.synchronizedList(mutableListOf<List<Int>>())
        val c = connecte(60_000, 0x03, r)
        c.emettre(true)
        assertTrue(attendre(1000) { ptt1 in r })
        c.fermer()
        assertTrue(attendre(1000) { ptt0 in r })
    }

    @Test fun accordEtAtu() {
        val r = Collections.synchronizedList(mutableListOf<List<Int>>())
        val c = connecte(60_000, 0x03, r)
        attendre(500) { false }
        c.basculerAtu()
        assertTrue(attendre(500) { listOf(0x1C, 0x01, 0x01) in r })
        c.accorder()
        assertTrue(attendre(500) { listOf(0x1C, 0x01, 0x02) in r })
        c.emettre(true)
        r.clear()
        c.accorder()                // en émission : refusé
        Thread.sleep(100)
        assertFalse(listOf(0x1C, 0x01, 0x02) in r)
        c.fermer()
    }

    @Test fun reglageDePuissance() {
        val r = Collections.synchronizedList(mutableListOf<List<Int>>())
        val c = connecte(60_000, 0x03, r)
        c.reglerPuissance(50)
        assertTrue(attendre(500) { listOf(0x14, 0x0A, 0x01, 0x28) in r })
        assertEquals(50, c.puissance)
        c.fermer()
    }

    @Test fun gardeDuPont() {
        val g = GardePont(20_000, 400)
        assertFalse(g.veutEmettre(-96f, 1000))          // qFT8 muet
        assertTrue(g.veutEmettre(-10f, 1020))           // son : émission
        assertTrue(g.veutEmettre(-96f, 1300))           // tenue
        assertFalse(g.veutEmettre(-96f, 1500))          // fin
        assertTrue(g.veutEmettre(-10f, 2000))
        assertTrue(g.veutEmettre(-10f, 21_000))
        assertFalse(g.veutEmettre(-10f, 22_500))        // plus de 20 s : coupé
        assertTrue(g.depasse)
        assertFalse(g.veutEmettre(-10f, 23_000))        // reste coupé tant que ça joue
        assertFalse(g.veutEmettre(-96f, 24_000))        // silence : réarmé
        assertTrue(g.veutEmettre(-10f, 25_000))
    }

    @Test fun vox() {
        val v = Vox(800)
        assertFalse(v.actif(-50f, -30f, 1000))
        assertTrue(v.actif(-20f, -30f, 1100))
        assertTrue(v.actif(-50f, -30f, 1800))     // tenue
        assertFalse(v.actif(-50f, -30f, 1950))
    }

    @Test fun niveau() {
        assertEquals(-96f, niveauDbfs(ByteArray(4), 4), 0f)
        val plein = byteArrayOf(0xFF.toByte(), 0x7F, 0x00, 0x80.toByte())
        assertEquals(0f, niveauDbfs(plein, 4), 0.1f)
    }
}
