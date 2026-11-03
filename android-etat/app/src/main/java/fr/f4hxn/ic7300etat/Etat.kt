package fr.f4hxn.ic7300etat

import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

/** Une commande CI-V vue passer par cat_server.py. */
data class Commande(val t: Double, val sens: String, val nom: String, val valeur: String)

/** Réponse de etat_serveur.py (GET /etat) sur le PC de la radio. */
data class Etat(
    val hote: String,
    val heure: Double,                 // horloge du serveur, pour les âges
    val serviceCat: Boolean,
    val portCat: Boolean,
    val sonReseau: Boolean,
    val radioUsb: Boolean,
    val appliLocale: Boolean,
    val surveillance: Boolean,
    val client: String?,
    val clientDepuis: Double?,
    val frequence: Long?,
    val mode: String?,
    val smetre: String?,
    val smetreBrut: Int?,
    val emission: Boolean?,
    val puissance: Int?,
    val majRadio: Double?,
    val commandes: List<Commande>,
)

private fun JSONObject.texte(nom: String): String? = if (isNull(nom) || !has(nom)) null else getString(nom)
private fun JSONObject.reel(nom: String): Double? = if (isNull(nom) || !has(nom)) null else getDouble(nom)
private fun JSONObject.entier(nom: String): Int? = if (isNull(nom) || !has(nom)) null else getInt(nom)

fun lireEtat(adresse: String): Etat {
    val url = URL("http://$adresse/etat")
    val cx = url.openConnection() as HttpURLConnection
    cx.connectTimeout = 2500
    cx.readTimeout = 2500
    try {
        if (cx.responseCode != 200) error("réponse HTTP ${cx.responseCode}")
        val j = JSONObject(cx.inputStream.bufferedReader().readText())
        val r = j.getJSONObject("radio")
        val c = j.getJSONArray("commandes")
        return Etat(
            hote = j.getString("hote"),
            heure = j.getDouble("heure"),
            serviceCat = j.getBoolean("service_cat"),
            portCat = j.getBoolean("port_cat"),
            sonReseau = j.getBoolean("son_reseau"),
            radioUsb = j.getBoolean("radio_usb"),
            appliLocale = j.getBoolean("appli_locale"),
            surveillance = j.getBoolean("surveillance"),
            client = j.texte("client"),
            clientDepuis = j.reel("client_depuis"),
            frequence = if (r.has("frequence")) r.getLong("frequence") else null,
            mode = r.texte("mode"),
            smetre = r.texte("smetre"),
            smetreBrut = r.entier("smetre_brut"),
            emission = if (r.has("emission")) r.getBoolean("emission") else null,
            puissance = r.entier("puissance"),
            majRadio = j.reel("maj_radio"),
            commandes = (0 until c.length()).map {
                val o = c.getJSONObject(it)
                Commande(o.getDouble("t"), o.getString("sens"), o.getString("nom"), o.getString("valeur"))
            },
        )
    } finally {
        cx.disconnect()
    }
}

/** Noms connus des PC du réseau. */
val POSTES = mapOf(
    "192.168.1.10" to "PC principal",
    "127.0.0.1" to "le PC radio lui-même",
    "192.168.27.66" to "tablette (VPN)",
    "192.168.27.67" to "téléphone (VPN)",
)

fun nomPoste(ip: String) = POSTES[ip]?.let { "$it ($ip)" } ?: ip

/** 7096000 → « 7 096,000 MHz » */
fun texteFrequence(hz: Long): String {
    val mhz = hz / 1_000_000
    val khz = (hz / 1000) % 1000
    val reste = hz % 1000
    return "%d %03d,%03d".format(mhz, khz, reste).trimStart() + " kHz"
}

/** Durée lisible : « 12 s », « 4 min », « 2 h 05 ». */
fun duree(secondes: Double): String {
    val s = secondes.toLong().coerceAtLeast(0)
    return when {
        s < 60 -> "$s s"
        s < 3600 -> "${s / 60} min"
        else -> "%d h %02d".format(s / 3600, (s % 3600) / 60)
    }
}
