package fr.f4hxn.ic7300lite

import java.net.InetSocketAddress
import java.net.Socket
import java.util.concurrent.Executors
import kotlin.concurrent.thread

/**
 * Client du serveur CAT (cat_server.py) : il relaie le CI-V tel quel.
 * Le serveur n'accepte qu'un client et met les suivants en attente : une liaison
 * ouverte sans réponse de la radio est donc annoncée, pas prise pour un succès.
 */
class CatClient {
    @Volatile var etat = "Déconnecté"; private set
    @Volatile var connecte = false; private set
    @Volatile var frequence = 0L; private set
    @Volatile var mode = ""; private set
    @Volatile var smetre = 0; private set
    @Volatile var enMarche = false; private set
    @Volatile var vfo: String? = null; private set      // la radio ne dit pas lequel est actif
    @Volatile var memoires = emptyMap<Int, Memoire>(); private set
    @Volatile var lecture = 0; private set              // canal en cours de lecture, 0 sinon

    private val ecriture = Executors.newSingleThreadExecutor()   // garde l'ordre des trames
    @Volatile private var socket: Socket? = null
    @Volatile private var derniereReponse = 0L
    @Volatile private var frequenceReglee = 0L
    @Volatile private var filtre = 1
    @Volatile private var codeMode: Int? = null
    @Volatile private var modeData = false
    @Volatile private var refusA = 0L
    @Volatile private var canalAttendu = 0
    private val aLire = java.util.concurrent.ConcurrentLinkedQueue<Int>()

    fun connecter(hote: String, port: Int) {
        fermer()
        etat = "Connexion à $hote…"
        thread(isDaemon = true) {
            val s = Socket()
            socket = s
            try {
                s.connect(InetSocketAddress(hote, port), 5000)
                s.tcpNoDelay = true
                derniereReponse = 0
                connecte = true
                val debut = System.currentTimeMillis()
                thread(isDaemon = true) { interroger(s, hote, port, debut) }
                recevoir(s)
                if (socket === s) etat = "Le serveur a fermé la liaison"
            } catch (e: java.io.IOException) {
                if (socket === s) etat = "Serveur injoignable, ou occupé par un autre client (IC-7300 Scope ouvert ?)"
            } finally {
                if (socket === s) {
                    connecte = false
                    enMarche = false
                }
                runCatching { s.close() }
            }
        }
    }

    fun fermer() {
        val s = socket
        socket = null
        connecte = false
        enMarche = false
        aLire.clear()
        lecture = 0
        etat = "Déconnecté"
        runCatching { s?.close() }
    }

    private fun recevoir(s: Socket) {
        val entree = s.getInputStream()
        val tampon = ArrayList<Int>()
        while (true) {
            val b = entree.read()
            if (b < 0) return
            if (b != 0xFD) {
                tampon.add(b)
                if (tampon.size > 1024) tampon.clear()
                continue
            }
            traiter(tampon.dropWhile { it == 0xFE })
            tampon.clear()
        }
    }

    private fun interroger(s: Socket, hote: String, port: Int, debut: Long) {
        var tour = 0
        while (socket === s && !s.isClosed) {
            lireCanalSuivant()
            if (canalAttendu == 0) {
                envoyer(0x15, 0x02)
                if (tour % 3 == 0) {
                    envoyer(0x03)
                    envoyer(0x04)
                    envoyer(0x1A, 0x06)
                }
                tour++
            }
            val maintenant = System.currentTimeMillis()
            enMarche = maintenant - derniereReponse < 3000
            etat = when {
                maintenant - refusA < 3000 -> "Commande refusée par la radio"
                enMarche -> "Connecté à $hote:$port"
                maintenant - debut < 3000 -> "Connecté, attente de la radio…"
                else -> "Pas de réponse : radio éteinte, ou serveur occupé par un autre client"
            }
            Thread.sleep(if (canalAttendu != 0) 60 else 300)
        }
    }

    /** Un canal à la fois, trois essais : le S-mètre reprend entre deux lectures. */
    private var essais = 0
    private var attenduDepuis = 0L
    private fun lireCanalSuivant() {
        val maintenant = System.currentTimeMillis()
        if (canalAttendu != 0 && maintenant - attenduDepuis < 300) return
        if (canalAttendu != 0 && essais < 3) {
            essais++
        } else {
            canalAttendu = aLire.poll() ?: 0
            lecture = canalAttendu
            essais = 1
        }
        if (canalAttendu != 0) {
            attenduDepuis = maintenant
            envoyer(0x1A, 0x00, 0x00, versBcdOctet(canalAttendu))
        }
    }

    private fun traiter(f: List<Int>) {
        if (f.size < 3 || f[0] != CTRL_ADDR || f[1] != RIG_ADDR) return     // écho de nos commandes
        val cmd = f[2]
        val d = f.drop(3)
        if (cmd == NG) {
            // radio arrêtée, elle répond encore NG à tout : ni marche, ni refus
            if (enMarche && canalAttendu == 0) refusA = System.currentTimeMillis()
            return
        }
        derniereReponse = System.currentTimeMillis()
        when {
            cmd in listOf(0x00, 0x03) && d.size == 5 -> {
                // juste après un réglage, une réponse ancienne ferait reculer l'affichage
                if (System.currentTimeMillis() - frequenceReglee > 600) frequence = bcdLe(d)
            }
            cmd in listOf(0x01, 0x04) && d.isNotEmpty() -> {
                codeMode = d[0]
                d.getOrNull(1)?.let { filtre = it }
                majMode()
            }
            cmd == 0x1A && d.size >= 2 && d[0] == 0x06 -> {
                modeData = d[1] != 0
                majMode()
            }
            cmd == 0x15 && d.size == 3 && d[0] == 0x02 -> smetre = bcdBe(d.drop(1)).toInt().coerceIn(0, 241)
            cmd == 0x1A && d.size >= 4 && d[0] == 0x00 -> recevoirMemoire(bcdBe(d.subList(2, 3)).toInt(), d.drop(3))
        }
    }

    private fun majMode() {
        nomMode(codeMode ?: return, modeData)?.let { mode = it }
    }

    private fun recevoirMemoire(canal: Int, contenu: List<Int>) {
        if (canal != canalAttendu) return
        val m = lireMemoire(canal, contenu)
        memoires = if (m != null) memoires + (canal to m) else memoires - canal
        canalAttendu = 0
        lecture = 0
        essais = 0
    }

    private fun envoyerBrut(octets: ByteArray) {
        val s = socket ?: return
        ecriture.execute {
            try {
                s.getOutputStream().write(octets)
            } catch (_: java.io.IOException) {
                runCatching { s.close() }   // recevoir() annonce la coupure
            }
        }
    }

    private fun envoyer(vararg donnees: Int) = envoyerBrut(trame(*donnees))

    fun regler(hz: Long) {
        frequenceReglee = System.currentTimeMillis()
        frequence = hz
        envoyer(0x05, *versBcdLe(hz))
    }

    fun changerMode(nom: String) {
        val data = nom.endsWith("-D")
        val base = nom.removeSuffix("-D")
        val code = MODES.entries.firstOrNull { it.value == base }?.key ?: return
        envoyer(0x06, code, filtre)     // sans le filtre, la radio repasse sur son filtre par défaut
        if (base in MODES_DATA || base == "AM" || base == "FM") {
            envoyer(0x1A, 0x06, if (data) 1 else 0, if (data) filtre else 0)
        }
    }

    fun choisirVfo(nom: String) {
        envoyer(0x07, if (nom == "A") 0x00 else 0x01)
        vfo = nom
    }

    fun allumer() {
        // radio arrêtée : une suite de 0xFE réveille son port avant la commande
        envoyerBrut(ByteArray(175) { 0xFE.toByte() })
        envoyer(0x18, 0x01)
    }

    fun eteindre() = envoyer(0x18, 0x00)

    fun lireMemoires() {
        aLire.clear()
        aLire.addAll(1..99)
    }

    fun lireMemoire(canal: Int) {
        aLire.clear()
        aLire.add(canal)
    }

    fun appliquer(m: Memoire) {
        regler(m.hz)
        changerMode(m.mode)
    }
}
