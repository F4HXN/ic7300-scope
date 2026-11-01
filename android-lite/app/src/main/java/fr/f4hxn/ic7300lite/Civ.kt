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
