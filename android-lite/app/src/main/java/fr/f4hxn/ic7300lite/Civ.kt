package fr.f4hxn.ic7300lite

/** Protocole CI-V de l'IC-7300 : mêmes règles que ic7300_scope.py. */
const val RIG_ADDR = 0x94
const val CTRL_ADDR = 0xE0
const val ACK = 0xFB
const val NG = 0xFA
const val MEMOIRE_TAILLE = 39

val MODES = linkedMapOf(0 to "LSB", 1 to "USB", 2 to "AM", 3 to "CW", 4 to "RTTY", 5 to "FM", 7 to "CW-R", 8 to "RTTY-R")
val MODES_DATA = listOf("LSB", "USB")

/** Modes proposés, dans l'ordre de l'écran. */
val MODES_ECRAN = listOf("LSB", "LSB-D", "USB", "USB-D", "AM", "CW", "RTTY", "FM", "CW-R", "RTTY-R")

data class Memoire(val canal: Int, val nom: String, val hz: Long, val mode: String)

/** Fréquence : BCD, poids faible en premier. */
fun bcdLe(octets: List<Int>): Long = octets.reversed().fold(0L) { v, b -> v * 100 + (b shr 4) * 10 + (b and 15) }

/** Niveaux et numéros de canal : BCD, poids fort en premier. */
fun bcdBe(octets: List<Int>): Long = octets.fold(0L) { v, b -> v * 100 + (b shr 4) * 10 + (b and 15) }

fun versBcdLe(valeur: Long, taille: Int = 5): IntArray {
    var v = valeur
    return IntArray(taille) {
        val bas = (v % 10).toInt()
        val haut = (v / 10 % 10).toInt()
        v /= 100
        (haut shl 4) or bas
    }
}

fun versBcdOctet(valeur: Int): Int = ((valeur / 10) shl 4) or (valeur % 10)

fun nomMode(code: Int, data: Boolean): String? {
    val nom = MODES[code] ?: return null
    return if (data && nom in MODES_DATA) "$nom-D" else nom
}

fun trame(vararg donnees: Int): ByteArray =
    (intArrayOf(0xFE, 0xFE, RIG_ADDR, CTRL_ADDR) + donnees + intArrayOf(0xFD)).map { it.toByte() }.toByteArray()

/** Contenu d'un canal mémoire (après son numéro) ; null s'il est vide ou illisible. */
fun lireMemoire(canal: Int, contenu: List<Int>): Memoire? {
    if (contenu.size != MEMOIRE_TAILLE) return null
    val mode = nomMode(contenu[6], contenu[8] shr 4 == 1) ?: return null
    val nom = contenu.takeLast(10).map { it.toChar() }.joinToString("").trim()
    return Memoire(canal, nom, bcdLe(contenu.subList(1, 6)), mode)
}

/** Échelle Icom : 0 = S0, 120 = S9, 241 = S9+60 dB. */
fun texteS(v: Int): String =
    if (v <= 120) "S${Math.round(v * 9 / 120f)}" else "S9+${Math.round((v - 120) * 60 / 121f)} dB"

/** "14.074.000", "14074000" ou "14 074 000" → Hz, null hors de la couverture de la radio. */
fun lireFrequence(texte: String): Long? {
    if (texte.any { !it.isDigit() && it !in ". " }) return null
    return texte.filter { it.isDigit() }.toLongOrNull()?.takeIf { it in 30_000L..74_800_000L }
}

fun texteFrequence(hz: Long): String = "%d.%03d.%03d".format(java.util.Locale.ROOT, hz / 1_000_000, hz / 1000 % 1000, hz % 1000)

/** Couverture de la radio, comme la saisie de fréquence. */
const val HZ_MIN = 30_000L
const val HZ_MAX = 74_800_000L

/** Déplace d'un pas en retombant sur un multiple du pas : 14.074.123 + 1 kHz → 14.075.000. */
fun frequenceDecalee(hz: Long, pas: Long, sens: Int): Long {
    // sens 0 : le multiple du pas le plus proche
    val base = when {
        sens > 0 -> Math.floorDiv(hz, pas) + 1
        sens < 0 -> Math.floorDiv(hz - 1, pas)
        else -> Math.floorDiv(hz + pas / 2, pas)
    }
    return (base * pas).coerceIn(HZ_MIN, HZ_MAX)
}

/** Un balayage du scope de la radio : niveaux de 0 à 160, du bord bas au bord haut. */
class Spectre(val points: IntArray, val bas: Long, val haut: Long)

/** Un balayage arrive en plusieurs trames (CI-V 27 00) ; la première donne les bords. */
class AssembleurSpectre {
    private var morceaux: MutableList<List<Int>>? = null
    private var bas = 0L
    private var haut = 0L

    fun ajouter(numero: Int, total: Int, contenu: List<Int>): Spectre? {
        val liste = morceaux
        if (numero == 1) {
            morceaux = null
            if (contenu.size >= 11) {
                val a = bcdLe(contenu.subList(1, 6))
                val b = bcdLe(contenu.subList(6, 11))
                // mode centré : fréquence centrale et demi-largeur
                bas = if (contenu[0] == 0) a - b else a
                haut = if (contenu[0] == 0) a + b else b
                morceaux = mutableListOf()
            }
        } else if (liste != null && numero == liste.size + 2) {
            liste.add(contenu)
            if (numero == total) {
                morceaux = null
                return Spectre(liste.flatten().toIntArray(), bas, haut)
            }
        } else {
            morceaux = null         // trame perdue : on attend le balayage suivant
        }
        return null
    }
}

/** Couleur d'un niveau de cascade : noir, bleu, cyan, jaune puis rouge jusqu'à `haut`. */
fun couleurCascade(valeur: Int, haut: Int): Int {
    val t = (valeur.toFloat() / haut).coerceIn(0f, 1f) * 4f
    val n = t.toInt().coerceAtMost(3)
    val f = t - n
    val a = PALETTE[n]
    val b = PALETTE[n + 1]
    fun canal(decalage: Int): Int {
        val ca = (a shr decalage) and 0xFF
        val cb = (b shr decalage) and 0xFF
        return (ca + (cb - ca) * f).toInt() shl decalage
    }
    return (0xFF shl 24) or canal(16) or canal(8) or canal(0)
}

private val PALETTE = intArrayOf(0x05080A, 0x0D47A1, 0x00BCD4, 0xFFEB3B, 0xF44336)

/** Historique de la cascade : le balayage le plus récent est sur la première ligne. */
class Cascade(val hauteur: Int) {
    var largeur = 0; private set
    var pixels = IntArray(0); private set

    fun ajouter(points: IntArray, haut: Int) {
        if (points.size != largeur) {
            largeur = points.size
            pixels = IntArray(largeur * hauteur) { couleurCascade(0, 1) }
        }
        System.arraycopy(pixels, 0, pixels, largeur, largeur * (hauteur - 1))
        for (i in 0 until largeur) pixels[i] = couleurCascade(points[i], haut)
    }
}

/** Fréquences rondes à graduer entre deux bords : un pas de 1, 2 ou 5, pour 4 à 8 repères environ. */
fun graduations(bas: Long, haut: Long): List<Long> {
    if (haut <= bas) return emptyList()
    val pas = listOf(1_000L, 2_000L, 5_000L, 10_000L, 20_000L, 50_000L, 100_000L, 200_000L, 500_000L)
        .firstOrNull { (haut - bas) / it <= 7 } ?: 1_000_000L
    val premier = Math.floorDiv(bas + pas - 1, pas) * pas
    return generateSequence(premier) { it + pas }.takeWhile { it <= haut }.toList()
}

/** Repère de la règle : "7.355" (MHz.kHz). */
fun texteGraduation(hz: Long): String = "%d.%03d".format(java.util.Locale.ROOT, hz / 1_000_000, hz / 1000 % 1000)

/** Résolution du doigt : le pas choisi, sauf s'il est trop grossier pour la largeur affichée (1 MHz sur ±25 kHz). */
fun pasTactile(pas: Long, bas: Long, haut: Long): Long {
    val fin = listOf(10L, 100L, 1_000L, 5_000L, 10_000L).lastOrNull { it <= (haut - bas) / 200 } ?: 10L
    return minOf(pas, fin)
}
