package fr.f4hxn.ic7300lite

import android.content.Context
import android.graphics.Bitmap
import android.media.AudioManager
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.horizontalScroll
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
import androidx.compose.ui.graphics.FilterQuality
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.nativeCanvas
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.unit.IntSize
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.ui.geometry.Offset
import androidx.compose.foundation.gestures.detectHorizontalDragGestures
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.interaction.collectIsPressedAsState
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.style.TextAlign
import kotlinx.coroutines.delay

private val CYAN = Color(0xFF4FC3F7)
private val FOND = Color(0xFF101418)
private val VERT = Color(0xFF61D38B)

private val PAS = listOf("10 Hz" to 10L, "100 Hz" to 100L, "1 kHz" to 1000L, "5 kHz" to 5000L, "10 kHz" to 10_000L, "100 kHz" to 100_000L, "1 MHz" to 1_000_000L)

private data class Bande(val nom: String, val debut: Long, val fin: Long, val defaut: Long, val mode: String)

private val BANDES = listOf(
    Bande("160m", 1_810_000L, 2_000_000L, 1_850_000L, "LSB"),
    Bande("80m", 3_500_000L, 3_800_000L, 3_700_000L, "LSB"),
    Bande("60m", 5_351_500L, 5_366_500L, 5_360_000L, "USB"),
    Bande("40m", 7_000_000L, 7_200_000L, 7_100_000L, "LSB"),
    Bande("30m", 10_100_000L, 10_150_000L, 10_120_000L, "CW"),
    Bande("20m", 14_000_000L, 14_350_000L, 14_200_000L, "USB"),
    Bande("17m", 18_068_000L, 18_168_000L, 18_130_000L, "USB"),
    Bande("15m", 21_000_000L, 21_450_000L, 21_250_000L, "USB"),
    Bande("12m", 24_890_000L, 24_990_000L, 24_950_000L, "USB"),
    Bande("10m", 28_000_000L, 29_700_000L, 28_500_000L, "USB"),
    Bande("6m", 50_000_000L, 52_000_000L, 50_150_000L, "USB"),
    Bande("4m", 70_000_000L, 70_500_000L, 70_200_000L, "USB")
)

/** Un appui fait un pas ; maintenu, le bouton répète après une courte attente. */
@Composable
private fun BoutonRepete(texte: String, actif: Boolean, action: () -> Unit) {
    val source = remember { MutableInteractionSource() }
    val presse by source.collectIsPressedAsState()
    val derniere by rememberUpdatedState(action)
    LaunchedEffect(presse) {
        if (presse) {
            delay(400)
            while (true) {
                derniere()
                delay(90)
            }
        }
    }
    Button(onClick = action, enabled = actif, interactionSource = source) { Text(texte, fontSize = 24.sp, fontWeight = FontWeight.Bold) }
}

/** Largeurs du scope de l'IC-7300 en mode centré : libellé (± kHz) et demi-largeur en Hz. */
private val LARGEURS = listOf("±2,5" to 2_500L, "±5" to 5_000L, "±10" to 10_000L, "±25" to 25_000L, "±50" to 50_000L, "±100" to 100_000L, "±250" to 250_000L, "±500" to 500_000L)

private const val LIGNES_CASCADE = 70
private const val NIVEAU_MAX = 160       // niveaux du scope de la radio : 0 à 160
private const val SON_ADRESSE = "192.168.1.31:7301"

/** "hôte" ou "hôte:port" du relais de son. */
private fun lireAdresseSon(texte: String): Pair<String, Int> {
    val morceaux = texte.trim().split(":")
    return morceaux[0] to (morceaux.getOrNull(1)?.toIntOrNull()?.takeIf { it in 1..65535 } ?: 7301)
}

class MainActivity : ComponentActivity() {
    private val client = CatClient()
    private val son = Radio.son
    private var aReprendre = false

    private fun reglages() = getSharedPreferences("serveur", Context.MODE_PRIVATE)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        volumeControlStream = AudioManager.STREAM_MUSIC    // les touches de volume règlent le son de la radio
        son.volume = reglages().getInt("volume", 70)
        setContent {
            MaterialTheme(colorScheme = darkColorScheme(primary = CYAN, background = FOND, surface = Color(0xFF1C252D))) {
                Ecran(client, son, reglages().getString("hote", "192.168.1.37")!!, reglages().getInt("port", 7300), reglages().getString("son", SON_ADRESSE)!!, reglages().getLong("pas", 1000L), reglages().getFloat("seuil", -30f),
                    { v -> reglages().edit().putInt("volume", v).apply() },
                    { p -> reglages().edit().putLong("pas", p).apply() },
                    {
                        // sans cette permission, le son continue mais sa notification reste cachée
                        if (android.os.Build.VERSION.SDK_INT >= 33) requestPermissions(arrayOf(android.Manifest.permission.POST_NOTIFICATIONS), 1)
                    },
                    { v -> reglages().edit().putFloat("seuil", v).apply() },
                    {
                        val permis = checkSelfPermission(android.Manifest.permission.RECORD_AUDIO) == android.content.pm.PackageManager.PERMISSION_GRANTED
                        if (!permis) requestPermissions(arrayOf(android.Manifest.permission.RECORD_AUDIO), 2)
                        permis
                    })
            }
        }
    }

    // Le serveur n'accepte qu'un client : appli en arrière-plan, on lui rend la radio
    override fun onStop() {
        super.onStop()
        aReprendre = client.connecte
        client.fermer()
    }

    override fun onStart() {
        super.onStart()
        if (aReprendre) client.connecter(reglages().getString("hote", "192.168.1.37")!!, reglages().getInt("port", 7300))
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun Ecran(client: CatClient, son: Son, hote: String, port: Int, adresseSon: String, pasInitial: Long, seuilInitial: Float,
                  memoriserVolume: (Int) -> Unit, memoriserPas: (Long) -> Unit, demanderNotifications: () -> Unit,
                  memoriserSeuil: (Float) -> Unit, autoriserMicro: () -> Boolean) {
    val contexte = LocalContext.current
    var spectre by remember { mutableStateOf<Spectre?>(null) }
    var etatSon by remember { mutableStateOf(son.etat) }
    var ecoute by remember { mutableStateOf(false) }
    var volume by remember { mutableIntStateOf(son.volume) }
    var pas by remember { mutableLongStateOf(pasInitial) }
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
            spectre = client.spectre
            delay(150)
        }
    }
    val actif = connecte && marche
    var ptt by remember { mutableStateOf(false) }
    var voxActif by remember { mutableStateOf(false) }
    var seuil by remember { mutableFloatStateOf(seuilInitial) }
    var niveauMicro by remember { mutableFloatStateOf(-96f) }
    var enEmission by remember { mutableStateOf(false) }
    var texteEmission by remember { mutableStateOf("") }
    val micro = remember { Micro() }
    val vox = remember { Vox() }
    val seuilActuel by rememberUpdatedState(seuil)
    val demandeVox = remember { java.util.concurrent.atomic.AtomicBoolean(false) }
    DisposableEffect(Unit) {
        micro.surBloc = { n -> demandeVox.set(vox.actif(n, seuilActuel, System.currentTimeMillis())) }
        onDispose { micro.surBloc = {}; micro.fermer() }
    }
    if (!actif) { voxActif = false; ptt = false }
    // Le micro reste ouvert tant que le VOX ou le PTT en a besoin ; la radio suit
    LaunchedEffect(actif, voxActif, ptt) {
        val adresse = lireAdresseSon(adresseSon)
        if (voxActif || ptt) micro.ouvrir(adresse.first, adresse.second)
        while (voxActif || ptt) {
            val veut = ptt || demandeVox.get()
            micro.envoyer = veut
            son.muet = veut
            client.emettre(veut)
            niveauMicro = micro.niveau
            delay(40)
        }
        micro.envoyer = false
        son.muet = false
        client.emettre(false)
        micro.fermer()
        demandeVox.set(false)
        niveauMicro = -96f
    }
    LaunchedEffect(Unit) {
        while (true) {
            enEmission = client.enEmission
            val secondes = (System.currentTimeMillis() - client.depuis) / 1000
            texteEmission = when {
                micro.etat.isNotEmpty() -> micro.etat
                client.enEmission -> "En émission : $secondes s (coupure à 180 s)" + if (client.radioEmet) "" else ", attente de la radio"
                client.limiteAtteinte -> "Émission coupée après 3 minutes"
                else -> ""
            }
            if (client.limiteAtteinte) { voxActif = false; ptt = false }
            delay(200)
        }
    }
    // Cascade sous le spectre : une ligne par balayage reçu
    val cascade = remember { Cascade(LIGNES_CASCADE) }
    var image by remember { mutableStateOf<ImageBitmap?>(null) }
    var bitmap by remember { mutableStateOf<Bitmap?>(null) }
    var tourCascade by remember { mutableIntStateOf(0) }
    LaunchedEffect(spectre) {
        val v = spectre ?: return@LaunchedEffect
        cascade.ajouter(v.points, NIVEAU_MAX)
        if (cascade.largeur > 0 && bitmap?.width != cascade.largeur) {
            bitmap = Bitmap.createBitmap(cascade.largeur, LIGNES_CASCADE, Bitmap.Config.ARGB_8888).also { image = it.asImageBitmap() }
        }
        bitmap?.setPixels(cascade.pixels, 0, cascade.largeur, 0, 0, cascade.largeur, LIGNES_CASCADE)
        tourCascade++
    }
    Surface(Modifier.fillMaxSize(), color = FOND) {
        Row(Modifier.fillMaxSize().safeDrawingPadding().padding(16.dp), horizontalArrangement = Arrangement.spacedBy(16.dp)) {
            Column(Modifier.weight(1f).fillMaxHeight().verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                // en petite fenêtre, l'état et le bouton passent à la ligne au lieu d'être coupés
                FlowRow(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(16.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    Text("IC-7300  /  SCOPE LITE", Modifier.align(Alignment.CenterVertically), color = CYAN, fontWeight = FontWeight.Bold, fontSize = 24.sp, softWrap = false)
                    Text(etat, Modifier.align(Alignment.CenterVertically), color = if (actif) VERT else Color.LightGray, maxLines = 2)
                    Button(onClick = { if (connecte) client.fermer() else client.connecter(hote, port) }) {
                        Text(if (connecte) "DÉCONNECTER" else "CONNECTER", softWrap = false)
                    }
                }
                Card(Modifier.fillMaxWidth(), shape = RoundedCornerShape(16.dp)) {
                    Column(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 10.dp), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(14.dp)) {
                            Text("WATERFALL", color = CYAN, fontWeight = FontWeight.Bold)
                            // petite fenêtre : les choix défilent au lieu de s'écraser
                            Row(Modifier.weight(1f).horizontalScroll(rememberScrollState()), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                                LARGEURS.forEach { (nom, demi) ->
                                    FilterChip(selected = actif && spectre?.let { (it.haut - it.bas) / 2 == demi } == true, enabled = actif,
                                        onClick = { client.reglerLargeur(demi) }, label = { Text(nom, fontSize = 13.sp, softWrap = false) })
                                }
                            }
                            Text(spectre?.let { "${texteFrequence(maxOf(it.bas, 0))} – ${texteFrequence(it.haut)}" } ?: "Pas de scope", color = Color.LightGray, fontSize = 14.sp, softWrap = false)
                        }
                        Canvas(Modifier.fillMaxWidth().height(110.dp).pointerInput(actif, pas) {
                            detectTapGestures { p ->      // toucher : la radio va sur ce point du waterfall
                                val v = client.spectre
                                if (actif && v != null) {
                                    val f = v.bas + ((v.haut - v.bas) * (p.x / size.width)).toLong()
                                    client.regler(frequenceDecalee(f, pasTactile(pas, v.bas, v.haut), 0))
                                }
                            }
                        }.pointerInput(actif, pas) {
                            // glisser : le waterfall suit le doigt, donc vers la gauche la fréquence monte
                            var reste = 0f
                            if (actif) detectHorizontalDragGestures(onDragEnd = { reste = 0f }) { _, dx ->
                                val v = client.spectre
                                if (v != null) {
                                    reste -= dx * (v.haut - v.bas) / size.width
                                    val fin = pasTactile(pas, v.bas, v.haut)
                                    val pasEntiers = (reste / fin).toInt()
                                    if (pasEntiers != 0) {
                                        client.regler((client.frequence + pasEntiers * fin).coerceIn(HZ_MIN, HZ_MAX))
                                        reste -= pasEntiers * fin
                                    }
                                }
                            }
                        }) {
                            drawRect(Color(0xFF05080A))
                            val v = spectre
                            if (actif && v != null && v.points.size > 1) {
                                image?.let {
                                    if (tourCascade >= 0) drawImage(it, dstSize = IntSize(size.width.toInt(), size.height.toInt()), filterQuality = FilterQuality.Low)
                                }
                                if (hz in v.bas..v.haut && v.haut > v.bas) {
                                    val x = size.width * (hz - v.bas) / (v.haut - v.bas)
                                    drawLine(Color(0xFFFF5252), Offset(x, 0f), Offset(x, size.height), 3f)
                                }
                            }
                        }
                        // Règle sous le waterfall : mêmes bords, donc les repères tombent sous les signaux
                        val texteRegle = with(LocalDensity.current) { 13.sp.toPx() }
                        Canvas(Modifier.fillMaxWidth().height(24.dp)) {
                            val v = spectre
                            if (actif && v != null && v.haut > v.bas) {
                                val peinture = android.graphics.Paint().apply {
                                    color = android.graphics.Color.LTGRAY
                                    textSize = texteRegle
                                    textAlign = android.graphics.Paint.Align.CENTER
                                    isAntiAlias = true
                                }
                                graduations(v.bas, v.haut).forEach { f ->
                                    val x = size.width * (f - v.bas) / (v.haut - v.bas)
                                    drawLine(Color.LightGray, Offset(x, 0f), Offset(x, 10f), 2f)
                                    val texte = texteGraduation(f)
                                    val demi = peinture.measureText(texte) / 2
                                    drawContext.canvas.nativeCanvas.drawText(texte, x.coerceIn(demi, size.width - demi), size.height - 2f, peinture)
                                }
                            }
                        }
                    }
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
                        // Glisser sur l'affichage : la fréquence tourne comme le bouton de la radio
                        val pasPixels = with(LocalDensity.current) { 14.dp.toPx() }
                        var glisse by remember { mutableFloatStateOf(0f) }
                        Text(if (actif && hz > 0) texteFrequence(hz) else "--.---.---",
                            Modifier.fillMaxWidth().pointerInput(actif, pas) {
                                if (actif) detectHorizontalDragGestures(onDragEnd = { glisse = 0f }) { _, dx ->
                                    glisse += dx
                                    while (kotlin.math.abs(glisse) >= pasPixels) {
                                        val sens = if (glisse > 0) 1 else -1
                                        client.decaler(pas, sens)
                                        glisse -= sens * pasPixels
                                    }
                                }
                            },
                            textAlign = TextAlign.Center, fontSize = 54.sp, fontWeight = FontWeight.Bold, color = Color(0xFF70D9FF))
                        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            BoutonRepete("−", actif) { client.decaler(pas, -1) }
                            PAS.forEach { (nom, valeur) ->
                                FilterChip(selected = pas == valeur, onClick = { pas = valeur; memoriserPas(valeur) }, label = { Text(nom) })
                            }
                            BoutonRepete("+", actif) { client.decaler(pas, 1) }
                        }
                        FlowRow(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                            Text("BANDE", Modifier.align(Alignment.CenterVertically), color = CYAN, fontWeight = FontWeight.Bold)
                            BANDES.forEach { b ->
                                FilterChip(selected = actif && hz in b.debut..b.fin, enabled = actif, onClick = {
                                    client.regler(b.defaut)
                                    client.changerMode(b.mode)
                                }, label = { Text(b.nom) })
                            }
                        }
                        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            OutlinedTextField(saisie, { saisie = it; saisieFausse = false }, Modifier.width(260.dp), label = { Text("Fréquence directe (Hz)") }, singleLine = true,
                                isError = saisieFausse, keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number))
                            Button(enabled = actif, onClick = {
                                val f = lireFrequence(saisie)
                                saisieFausse = f == null
                                f?.let(client::regler)
                            }) { Text("RÉGLER") }
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
                                    SonService.arreter(contexte)
                                } else {
                                    demanderNotifications()
                                    lireAdresseSon(adresseSon).let { (h, p) -> SonService.demarrer(contexte, h, p) }
                                }
                            }) { Text(if (ecoute) "COUPER" else "ÉCOUTER") }
                            Text("VOLUME", color = CYAN, fontWeight = FontWeight.Bold)
                            Slider(value = volume.toFloat(), onValueChange = { volume = it.toInt(); son.volume = volume }, Modifier.weight(1f), valueRange = 0f..100f,
                                onValueChangeFinished = { memoriserVolume(volume) })
                            Text("$volume %", Modifier.width(70.dp), color = Color.White, fontWeight = FontWeight.Bold, fontSize = 20.sp)
                        }
                        Text(etatSon, color = if (etatSon.endsWith("écoute")) VERT else Color.LightGray, fontSize = 14.sp)
                    }
                }
                Card(Modifier.fillMaxWidth(), shape = RoundedCornerShape(16.dp)) {
                    Column(Modifier.fillMaxWidth().padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(14.dp)) {
                            Text("ÉMISSION", color = CYAN, fontSize = 19.sp, fontWeight = FontWeight.Bold)
                            // Maintenu pour émettre : relâché, ou doigt perdu, la radio repasse en réception
                            val sourcePtt = remember { MutableInteractionSource() }
                            val pttPresse by sourcePtt.collectIsPressedAsState()
                            LaunchedEffect(pttPresse) {
                                if (pttPresse && !voxActif) {
                                    if (autoriserMicro()) ptt = true
                                } else {
                                    ptt = false
                                }
                            }
                            Button(onClick = {}, enabled = actif && !voxActif, interactionSource = sourcePtt,
                                colors = ButtonDefaults.buttonColors(containerColor = if (enEmission) Color(0xFFE5342B) else CYAN, contentColor = if (enEmission) Color.White else Color.Black),
                                modifier = Modifier.height(56.dp).width(170.dp)) {
                                Text(if (enEmission) "ÉMISSION" else "PTT", fontWeight = FontWeight.Bold, fontSize = 20.sp, softWrap = false)
                            }
                            Text("VOX", color = CYAN, fontWeight = FontWeight.Bold)
                            Switch(checked = voxActif, enabled = actif, onCheckedChange = { voxActif = it && autoriserMicro() })
                            Text("SEUIL", color = Color.LightGray)
                            Slider(value = seuil, onValueChange = { seuil = it }, Modifier.weight(1f), valueRange = -60f..-10f,
                                onValueChangeFinished = { memoriserSeuil(seuil) })
                            Text("${seuil.toInt()} dB", Modifier.width(70.dp), color = Color.White, softWrap = false)
                        }
                        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(14.dp)) {
                            Text("MICRO", color = Color.LightGray)
                            LinearProgressIndicator(progress = { ((niveauMicro + 60f) / 60f).coerceIn(0f, 1f) }, Modifier.weight(1f).height(10.dp),
                                color = if (niveauMicro >= seuil) Color(0xFFFFB300) else Color(0xFF48D69A), trackColor = Color(0xFF2B3239))
                            Text(texteEmission, color = if (enEmission) Color(0xFFFF5252) else Color.LightGray, fontSize = 14.sp)
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
