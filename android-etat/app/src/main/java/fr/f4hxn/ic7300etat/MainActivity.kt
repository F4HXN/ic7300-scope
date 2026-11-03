package fr.f4hxn.ic7300etat

import android.content.Context
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

private val FOND = Color(0xFF1A1A19)
private val CARTE = Color(0xFF262624)
private val TEXTE = Color(0xFFECECE8)
private val GRIS = Color(0xFF8A8A84)
private val VERT = Color(0xFF3DDC84)
private val ROUGE = Color(0xFFEF5350)
private val JAUNE = Color(0xFFFFD54A)
private val BLEU = Color(0xFF4FC3F7)

private const val ADRESSE_DEFAUT = "192.168.1.37:7301"
private const val PERIODE_MS = 2000L
private const val VALEURS_FRAICHES_S = 10.0

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val prefs = getSharedPreferences("reglages", Context.MODE_PRIVATE)
        setContent {
            MaterialTheme(colorScheme = darkColorScheme(background = FOND, surface = CARTE)) {
                Ecran(
                    adresseInitiale = prefs.getString("adresse", ADRESSE_DEFAUT) ?: ADRESSE_DEFAUT,
                    enregistrer = { prefs.edit().putString("adresse", it).apply() },
                )
            }
        }
    }
}

@Composable
fun Ecran(adresseInitiale: String, enregistrer: (String) -> Unit) {
    var adresse by remember { mutableStateOf(adresseInitiale) }
    var etat by remember { mutableStateOf<Etat?>(null) }
    var erreur by remember { mutableStateOf<String?>(null) }
    var reglages by remember { mutableStateOf(false) }

    LaunchedEffect(adresse) {
        etat = null
        while (true) {
            try {
                etat = withContext(Dispatchers.IO) { lireEtat(adresse) }
                erreur = null
            } catch (e: Exception) {
                erreur = e.message ?: e.javaClass.simpleName
            }
            delay(PERIODE_MS)
        }
    }

    Column(
        Modifier.fillMaxSize().background(FOND).systemBarsPadding().padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Pastille(if (erreur == null && etat != null) VERT else ROUGE, 14)
            Spacer(Modifier.width(10.dp))
            Column(Modifier.weight(1f)) {
                Text("Serveur IC-7300", color = TEXTE, fontSize = 22.sp, fontWeight = FontWeight.Bold)
                Text(
                    etat?.let { "${it.hote} — $adresse" } ?: adresse,
                    color = GRIS, fontSize = 13.sp,
                )
            }
            TextButton(onClick = { reglages = !reglages }) { Text(if (reglages) "Fermer" else "⚙", color = TEXTE, fontSize = 18.sp) }
        }

        if (reglages) Reglages(adresse) {
            adresse = it
            enregistrer(it)
            reglages = false
        }

        erreur?.let {
            Bandeau("Serveur injoignable : $it", ROUGE)
        }

        val e = etat ?: run {
            if (erreur == null) Text("Connexion…", color = GRIS)
            return@Column
        }

        Utilisation(e)
        Indicateurs(e)
        PanneauRadio(e)
        Commandes(e, Modifier.weight(1f))
    }
}

@Composable
fun Pastille(couleur: Color, taille: Int = 10) {
    Box(Modifier.size(taille.dp).clip(CircleShape).background(couleur))
}

@Composable
fun Bandeau(texte: String, couleur: Color) {
    Text(
        texte, color = Color.Black, fontWeight = FontWeight.Bold,
        modifier = Modifier.fillMaxWidth().clip(RoundedCornerShape(8.dp)).background(couleur).padding(10.dp),
    )
}

@Composable
fun Carte(modifier: Modifier = Modifier, contenu: @Composable ColumnScope.() -> Unit) {
    Column(
        modifier.clip(RoundedCornerShape(10.dp)).background(CARTE).padding(14.dp),
        verticalArrangement = Arrangement.spacedBy(6.dp),
        content = contenu,
    )
}

/** Qui tient la radio en ce moment. */
@Composable
fun Utilisation(e: Etat) {
    val (texte, detail, couleur) = when {
        e.client != null -> Triple(
            "Pilotée à distance par ${nomPoste(e.client)}",
            e.clientDepuis?.let { "depuis ${duree(e.heure - it)}" } ?: "",
            BLEU,
        )
        e.appliLocale -> Triple("Appli IC-7300 Scope ouverte sur ${e.hote}", "la radio est pilotée en USB direct", JAUNE)
        else -> Triple("Radio libre", "aucun PC ne la pilote", VERT)
    }
    Carte(Modifier.fillMaxWidth()) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Pastille(couleur, 12)
            Spacer(Modifier.width(10.dp))
            Column {
                Text(texte, color = TEXTE, fontSize = 18.sp, fontWeight = FontWeight.SemiBold)
                if (detail.isNotEmpty()) Text(detail, color = GRIS, fontSize = 13.sp)
            }
        }
    }
}

@Composable
fun Indicateurs(e: Etat) {
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
        Indicateur("Serveur CAT", e.serviceCat && e.portCat,
            if (e.serviceCat && e.portCat) "port 7300" else "arrêté", Modifier.weight(1f))
        Indicateur("Radio USB", e.radioUsb, if (e.radioUsb) "branchée" else "absente", Modifier.weight(1f))
        Indicateur("Son réseau", e.sonReseau, if (e.sonReseau) "port 4713" else "coupé", Modifier.weight(1f))
        Indicateur("Surveillance", e.surveillance, if (e.surveillance) "active" else "sans cat_server", Modifier.weight(1f))
    }
}

@Composable
fun Indicateur(nom: String, ok: Boolean, detail: String, modifier: Modifier) {
    Carte(modifier) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Pastille(if (ok) VERT else ROUGE)
            Spacer(Modifier.width(8.dp))
            Text(nom, color = TEXTE, fontSize = 14.sp, fontWeight = FontWeight.SemiBold, maxLines = 1)
        }
        Text(detail, color = GRIS, fontSize = 12.sp, maxLines = 1)
    }
}

@Composable
fun PanneauRadio(e: Etat) {
    val age = e.majRadio?.let { e.heure - it }
    val frais = e.client != null && age != null && age < VALEURS_FRAICHES_S
    val couleur = if (frais) TEXTE else GRIS
    Carte(Modifier.fillMaxWidth()) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                e.frequence?.let { texteFrequence(it) } ?: "— fréquence inconnue —",
                color = couleur, fontSize = 34.sp, fontFamily = FontFamily.Monospace, fontWeight = FontWeight.Bold,
            )
            Spacer(Modifier.width(16.dp))
            e.mode?.let { Text(it, color = if (frais) VERT else GRIS, fontSize = 22.sp, fontWeight = FontWeight.Bold) }
            Spacer(Modifier.weight(1f))
            if (frais && e.emission == true) {
                Text("ÉMISSION", color = Color.White, fontWeight = FontWeight.Bold, fontSize = 18.sp,
                    modifier = Modifier.clip(RoundedCornerShape(6.dp)).background(ROUGE).padding(horizontal = 12.dp, vertical = 6.dp))
            } else if (frais) {
                Text("réception", color = GRIS, fontSize = 16.sp)
            }
        }
        SMetre(e.smetreBrut, e.smetre, frais)
        e.puissance?.takeIf { frais && e.emission == true }?.let {
            Text("Puissance émise : $it %", color = TEXTE, fontSize = 14.sp)
        }
        Text(
            when {
                frais -> "Valeurs en direct, lues dans le trafic du PC distant"
                age != null -> "Dernières valeurs connues, il y a ${duree(age)}" +
                    if (e.client == null) " (pas de PC distant en ce moment)" else ""
                e.appliLocale -> "Appli en USB direct sur ${e.hote} : fréquence non visible depuis le réseau"
                else -> "Aucune valeur : la radio n'a pas encore été pilotée à distance"
            },
            color = GRIS, fontSize = 12.sp,
        )
    }
}

@Composable
fun SMetre(brut: Int?, texte: String?, frais: Boolean) {
    val part = ((brut ?: 0) / 241f).coerceIn(0f, 1f)
    val s9 = 120f / 241f
    Row(verticalAlignment = Alignment.CenterVertically) {
        Text("S", color = GRIS, fontSize = 14.sp, modifier = Modifier.width(18.dp))
        Box(Modifier.weight(1f).height(14.dp).clip(RoundedCornerShape(4.dp)).background(Color(0xFF33332F))) {
            if (brut != null) Row(Modifier.fillMaxHeight().fillMaxWidth(part)) {
                Box(Modifier.fillMaxHeight().weight(minOf(part, s9).coerceAtLeast(0.001f)).background(if (frais) VERT else GRIS))
                if (part > s9) Box(Modifier.fillMaxHeight().weight(part - s9).background(if (frais) ROUGE else GRIS))
            }
        }
        Text(texte ?: "—", color = if (frais) TEXTE else GRIS, fontSize = 16.sp,
            fontFamily = FontFamily.Monospace, modifier = Modifier.width(80.dp).padding(start = 10.dp))
    }
}

@Composable
fun Commandes(e: Etat, modifier: Modifier) {
    val heure = remember { SimpleDateFormat("HH:mm:ss", Locale.FRANCE) }
    Carte(modifier.fillMaxWidth()) {
        Text("Dernières commandes", color = TEXTE, fontSize = 15.sp, fontWeight = FontWeight.SemiBold)
        if (e.commandes.isEmpty()) {
            Text("Rien depuis le démarrage du service", color = GRIS, fontSize = 13.sp)
        }
        LazyColumn(verticalArrangement = Arrangement.spacedBy(3.dp)) {
            items(e.commandes) { c ->
                val couleur = when {
                    c.nom == "PTT" && c.valeur == "ÉMISSION" -> ROUGE
                    c.sens == "→ radio" -> JAUNE
                    c.sens == "← radio" -> BLEU
                    else -> GRIS
                }
                Row {
                    Text(heure.format(Date((c.t * 1000).toLong())), color = GRIS, fontSize = 13.sp,
                        fontFamily = FontFamily.Monospace, modifier = Modifier.width(76.dp))
                    Text(c.sens, color = couleur, fontSize = 13.sp, modifier = Modifier.width(70.dp))
                    Text(c.nom, color = TEXTE, fontSize = 13.sp, modifier = Modifier.weight(1f))
                    Text(c.valeur, color = couleur, fontSize = 13.sp, fontFamily = FontFamily.Monospace)
                }
            }
        }
    }
}

@Composable
fun Reglages(adresse: String, valider: (String) -> Unit) {
    var texte by remember { mutableStateOf(adresse) }
    Carte(Modifier.fillMaxWidth()) {
        Text("Adresse du service d'état (PC de la radio)", color = TEXTE, fontSize = 14.sp)
        Row(verticalAlignment = Alignment.CenterVertically) {
            OutlinedTextField(
                texte, { texte = it.trim() }, singleLine = true, modifier = Modifier.weight(1f),
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri),
            )
            Spacer(Modifier.width(10.dp))
            Button(onClick = { if (texte.isNotBlank()) valider(texte) }) { Text("Valider") }
        }
        Text("Par défaut : $ADRESSE_DEFAUT (etat_serveur.py)", color = GRIS, fontSize = 12.sp)
    }
}
