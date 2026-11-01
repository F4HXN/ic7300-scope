package fr.f4hxn.ic7300lite

import android.content.Context
import android.media.AudioManager
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.delay

private val CYAN = Color(0xFF4FC3F7)
private val FOND = Color(0xFF101418)
private val VERT = Color(0xFF61D38B)

private const val SON_ADRESSE = "192.168.1.31:7301"

/** "hôte" ou "hôte:port" du relais de son. */
private fun lireAdresseSon(texte: String): Pair<String, Int> {
    val morceaux = texte.trim().split(":")
    return morceaux[0] to (morceaux.getOrNull(1)?.toIntOrNull()?.takeIf { it in 1..65535 } ?: 7301)
}

class MainActivity : ComponentActivity() {
    private val client = CatClient()
    private val son = Son()
    private var aReprendre = false
    private var sonAReprendre = false

    private fun reglages() = getSharedPreferences("serveur", Context.MODE_PRIVATE)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        volumeControlStream = AudioManager.STREAM_MUSIC    // les touches de volume règlent le son de la radio
        son.volume = reglages().getInt("volume", 70)
        setContent {
            MaterialTheme(colorScheme = darkColorScheme(primary = CYAN, background = FOND, surface = Color(0xFF1C252D))) {
                Ecran(client, son, reglages().getString("hote", "192.168.1.37")!!, reglages().getInt("port", 7300), reglages().getString("son", SON_ADRESSE)!!,
                    { hote, port -> reglages().edit().putString("hote", hote).putInt("port", port).apply() },
                    { adresse -> reglages().edit().putString("son", adresse).apply() },
                    { v -> reglages().edit().putInt("volume", v).apply() })
            }
        }
    }

    // Le serveur n'accepte qu'un client : appli en arrière-plan, on lui rend la radio
    override fun onStop() {
        super.onStop()
        aReprendre = client.connecte
        client.fermer()
        sonAReprendre = son.enEcoute
        son.arreter()
    }

    override fun onStart() {
        super.onStart()
        if (aReprendre) client.connecter(reglages().getString("hote", "")!!, reglages().getInt("port", 7300))
        if (sonAReprendre) lireAdresseSon(reglages().getString("son", SON_ADRESSE)!!).let { (h, p) -> son.demarrer(h, p) }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun Ecran(client: CatClient, son: Son, hoteInitial: String, portInitial: Int, sonInitial: String,
                  memoriser: (String, Int) -> Unit, memoriserSon: (String) -> Unit, memoriserVolume: (Int) -> Unit) {
    var adresseSon by remember { mutableStateOf(sonInitial) }
    var etatSon by remember { mutableStateOf(son.etat) }
    var ecoute by remember { mutableStateOf(false) }
    var volume by remember { mutableIntStateOf(son.volume) }
    var hote by remember { mutableStateOf(hoteInitial) }
    var port by remember { mutableStateOf(portInitial.toString()) }
    var saisie by remember { mutableStateOf("") }
    var saisieFausse by remember { mutableStateOf(false) }
    var canal by remember { mutableIntStateOf(1) }

    // État lu dans le client, qui vit sur ses propres fils
    var etat by remember { mutableStateOf(client.etat) }
    var connecte by remember { mutableStateOf(false) }
    var hz by remember { mutableLongStateOf(0L) }
    var mode by remember { mutableStateOf("") }
    var smetre by remember { mutableIntStateOf(0) }
    var marche by remember { mutableStateOf(false) }
    var vfo by remember { mutableStateOf<String?>(null) }
    var memoires by remember { mutableStateOf(emptyMap<Int, Memoire>()) }
    var lecture by remember { mutableIntStateOf(0) }
    LaunchedEffect(Unit) {
        while (true) {
            etat = client.etat; connecte = client.connecte; hz = client.frequence; mode = client.mode
            smetre = client.smetre; marche = client.enMarche; vfo = client.vfo
            memoires = client.memoires; lecture = client.lecture
            etatSon = son.etat; ecoute = son.enEcoute
            delay(150)
        }
    }
    val actif = connecte && marche

    Surface(Modifier.fillMaxSize(), color = FOND) {
        Row(Modifier.fillMaxSize().safeDrawingPadding().padding(16.dp), horizontalArrangement = Arrangement.spacedBy(16.dp)) {
            Column(Modifier.weight(1f).fillMaxHeight().verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("IC-7300  /  SCOPE LITE", color = CYAN, fontWeight = FontWeight.Bold, fontSize = 24.sp)
                    Spacer(Modifier.width(16.dp))
                    Text(etat, Modifier.weight(1f), color = if (actif) VERT else Color.LightGray, maxLines = 2)
                }
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                    OutlinedTextField(hote, { hote = it.trim() }, Modifier.weight(1f), label = { Text("IP du serveur IC-7300") }, singleLine = true,
                        enabled = !connecte, keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri))
                    OutlinedTextField(port, { port = it.filter(Char::isDigit) }, Modifier.width(110.dp), label = { Text("Port") }, singleLine = true,
                        enabled = !connecte, keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number))
                    Button(onClick = {
                        if (connecte) {
                            client.fermer()
                        } else {
                            val numero = port.toIntOrNull()?.takeIf { it in 1..65535 } ?: 7300
                            port = numero.toString()
                            memoriser(hote, numero)
                            client.connecter(hote, numero)
                        }
                    }) { Text(if (connecte) "DÉCONNECTER" else "CONNECTER") }
                }
                Card(Modifier.fillMaxWidth(), shape = RoundedCornerShape(16.dp)) {
                    Column(Modifier.fillMaxWidth().padding(16.dp), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(10.dp)) {
                        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            Text("VFO", color = CYAN, fontSize = 19.sp, fontWeight = FontWeight.Bold)
                            listOf("A", "B").forEach { nom ->
                                FilterChip(selected = vfo == nom, enabled = actif, onClick = { client.choisirVfo(nom) }, label = { Text(nom) })
                            }
                            Spacer(Modifier.weight(1f))
                            Button(enabled = connecte, onClick = { if (marche) client.eteindre() else client.allumer() }) {
                                Text(if (marche) "ÉTEINDRE" else "ALLUMER")
                            }
                        }
                        Text(if (actif && hz > 0) texteFrequence(hz) else "--.---.---", fontSize = 54.sp, fontWeight = FontWeight.Bold, color = Color(0xFF70D9FF))
                        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                            OutlinedTextField(saisie, { saisie = it; saisieFausse = false }, Modifier.width(230.dp), label = { Text("Fréquence (Hz)") }, singleLine = true,
                                isError = saisieFausse, keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number))
                            Button(enabled = actif, modifier = Modifier.align(Alignment.CenterVertically), onClick = {
                                val f = lireFrequence(saisie)
                                saisieFausse = f == null
                                f?.let(client::regler)
                            }) { Text("RÉGLER") }
                            Button(enabled = actif, modifier = Modifier.align(Alignment.CenterVertically), onClick = { client.regler((hz - 1000).coerceAtLeast(30_000)) }) { Text("− 1 kHz") }
                            Button(enabled = actif, modifier = Modifier.align(Alignment.CenterVertically), onClick = { client.regler((hz + 1000).coerceAtMost(74_800_000)) }) { Text("+ 1 kHz") }
                        }
                        FlowRow(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                            Text("MODE", Modifier.align(Alignment.CenterVertically), color = CYAN, fontWeight = FontWeight.Bold)
                            MODES_ECRAN.forEach { m ->
                                FilterChip(selected = actif && mode == m, enabled = actif, onClick = { client.changerMode(m) }, label = { Text(m) })
                            }
                        }
                        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(14.dp)) {
                            Text("S-MÈTRE", color = CYAN, fontWeight = FontWeight.Bold)
                            val niveau = if (actif) smetre else 0
                            LinearProgressIndicator(progress = { niveau / 241f }, Modifier.weight(1f).height(18.dp), color = Color(0xFF48D69A), trackColor = Color(0xFF2B3239))
                            Text(texteS(niveau), color = Color.White, fontWeight = FontWeight.Bold, fontSize = 20.sp)
                        }
                    }
                }
                Card(Modifier.fillMaxWidth(), shape = RoundedCornerShape(16.dp)) {
                    Column(Modifier.fillMaxWidth().padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(14.dp)) {
                            Text("SON", color = CYAN, fontSize = 19.sp, fontWeight = FontWeight.Bold)
                            Button(onClick = {
                                if (ecoute) {
                                    son.arreter()
                                } else {
                                    memoriserSon(adresseSon)
                                    lireAdresseSon(adresseSon).let { (h, p) -> son.demarrer(h, p) }
                                }
                            }) { Text(if (ecoute) "COUPER" else "ÉCOUTER") }
                            Text("VOLUME", color = CYAN, fontWeight = FontWeight.Bold)
                            Slider(value = volume.toFloat(), onValueChange = { volume = it.toInt(); son.volume = volume }, Modifier.weight(1f), valueRange = 0f..100f,
                                onValueChangeFinished = { memoriserVolume(volume) })
                            Text("$volume %", Modifier.width(70.dp), color = Color.White, fontWeight = FontWeight.Bold, fontSize = 20.sp)
                        }
                        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(14.dp)) {
                            OutlinedTextField(adresseSon, { adresseSon = it.trim() }, Modifier.width(300.dp), label = { Text("Relais de son") }, singleLine = true,
                                enabled = !ecoute, keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri))
                            Text(etatSon, Modifier.weight(1f), color = if (etatSon.endsWith("écoute")) VERT else Color.LightGray)
                        }
                    }
                }
                Text("Pilotage via le serveur IC-7300 Scope, TCP 7300. Un seul client à la fois.", color = Color.LightGray, fontSize = 14.sp)
            }
            Column(Modifier.width(320.dp).fillMaxHeight(), verticalArrangement = Arrangement.spacedBy(8.dp), horizontalAlignment = Alignment.CenterHorizontally) {
                Text("MÉMOIRES RADIO", color = CYAN, fontWeight = FontWeight.Bold, fontSize = 19.sp)
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    fun aller(c: Int) { canal = c; client.lireMemoire(c) }
                    Button(enabled = actif, onClick = { aller(if (canal == 1) 99 else canal - 1) }) { Text("◀") }
                    Text(canal.toString().padStart(2, '0'), fontSize = 38.sp, color = Color.White, fontWeight = FontWeight.Bold)
                    Button(enabled = actif, onClick = { aller(if (canal == 99) 1 else canal + 1) }) { Text("▶") }
                }
                val choisie = memoires[canal]
                Text(choisie?.let { "${it.nom.ifBlank { "Sans nom" }}  ${texteFrequence(it.hz)}  ${it.mode}" } ?: "Canal vide ou non lu", color = Color.LightGray, fontSize = 14.sp)
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Button(enabled = actif && choisie != null, onClick = { choisie?.let(client::appliquer) }) { Text("APPLIQUER") }
                    Button(enabled = actif && lecture == 0, onClick = { client.lireMemoires() }) { Text(if (lecture != 0) "LECTURE $lecture/99" else "LIRE LA RADIO") }
                }
                LazyColumn(Modifier.weight(1f).fillMaxWidth()) {
                    items(memoires.values.sortedBy { it.canal }, key = { it.canal }) { m ->
                        TextButton(enabled = actif, onClick = { canal = m.canal; client.appliquer(m) }, modifier = Modifier.fillMaxWidth()) {
                            Text("${m.canal.toString().padStart(2, '0')}  ${m.nom.ifBlank { "Sans nom" }}  ${texteFrequence(m.hz)}  ${m.mode}", Modifier.fillMaxWidth(), color = Color.White, fontSize = 14.sp)
                        }
                    }
                }
            }
        }
    }
}
