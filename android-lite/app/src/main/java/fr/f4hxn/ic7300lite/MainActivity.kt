package fr.f4hxn.ic7300lite

import android.content.Context
import android.graphics.Bitmap
import android.media.AudioManager
import android.os.Bundle
import android.view.WindowManager
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.*
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
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import androidx.compose.ui.graphics.FilterQuality
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.drawscope.Stroke
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

/** Choix d'un appareil audio : le menu relit la liste à chaque ouverture (casque branché entre-temps). */
@Composable
private fun ChoixAppareil(titre: String, sortie: Boolean, cleInitiale: String, choisir: (Appareil?) -> Unit) {
    val contexte = LocalContext.current
    var ouvert by remember { mutableStateOf(false) }
    var liste by remember { mutableStateOf(emptyList<Appareil>()) }
    var cle by remember { mutableStateOf(cleInitiale) }
    // appareil mémorisé : repris au démarrage s'il est encore là
    LaunchedEffect(Unit) { if (cle.isNotEmpty()) choisir(appareils(contexte, sortie).firstOrNull { it.cle == cle }) }
    val nom = appareils(contexte, sortie).firstOrNull { it.cle == cle }?.nom ?: "Automatique"
    Box {
        OutlinedButton(onClick = { liste = appareils(contexte, sortie); ouvert = true }) { Text("$titre : $nom", fontSize = 14.sp, softWrap = false) }
        DropdownMenu(expanded = ouvert, onDismissRequest = { ouvert = false }) {
            DropdownMenuItem(text = { Text("Automatique") }, onClick = { cle = ""; choisir(null); ouvert = false })
            liste.forEach { a ->
                DropdownMenuItem(text = { Text(a.nom) }, onClick = { cle = a.cle; choisir(a); ouvert = false })
            }
        }
    }
}

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

private const val DEMANDE_PONT = 7
private const val LIGNES_CASCADE = 70
private const val NIVEAU_MAX = 160       // niveaux du scope de la radio : 0 à 160
private const val SON_ADRESSE = "192.168.1.31:7301"

/** "hôte" ou "hôte:port" du relais de son. */
private fun lireAdresseSon(texte: String): Pair<String, Int> {
    val morceaux = texte.trim().split(":")
    return morceaux[0] to (morceaux.getOrNull(1)?.toIntOrNull()?.takeIf { it in 1..65535 } ?: 7301)
}

class MainActivity : ComponentActivity() {
    private val client = Radio.client
    private val son = Radio.son
    private var aReprendre = false

    private fun reglages() = getSharedPreferences("serveur", Context.MODE_PRIVATE)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        volumeControlStream = AudioManager.STREAM_MUSIC    // les touches de volume règlent le son de la radio
        son.volume = reglages().getInt("volume", 70)
        pleinEcran(reglages().getBoolean("plein_ecran", false))
        setContent {
            // téléphone (écran de moins de 500 dp) : tout réduit de 20 % pour garder la mise en page de la tablette
            val config = androidx.compose.ui.platform.LocalConfiguration.current
            val densite = LocalDensity.current
            val echelle = if (minOf(config.screenWidthDp, config.screenHeightDp) < 500)
                androidx.compose.ui.unit.Density(densite.density * 0.8f, densite.fontScale) else densite
            CompositionLocalProvider(LocalDensity provides echelle) {
            MaterialTheme(colorScheme = darkColorScheme(primary = CYAN, background = FOND, surface = Color(0xFF1C252D))) {
                Ecran(client, son, reglages().getString("hote", "192.168.1.37")!!, reglages().getInt("port", 7300), reglages().getString("son", SON_ADRESSE)!!, reglages().getLong("pas", 1000L), reglages().getFloat("seuil", -30f), reglages().getString("sortie", "")!!, reglages().getString("entree", "")!!,
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
                    },
                    { nom, cle -> reglages().edit().putString(nom, cle).apply() },
                    { demanderPont() },
                    reglages().getBoolean("plein_ecran", false),
                    { actif -> pleinEcran(actif); reglages().edit().putBoolean("plein_ecran", actif).apply() })
            }
            }
        }
    }

    /** Plein écran : barres système masquées (glisser depuis un bord pour les revoir)
     *  et écran gardé allumé. */
    private fun pleinEcran(actif: Boolean) {
        val barres = WindowCompat.getInsetsController(window, window.decorView)
        if (actif) {
            barres.systemBarsBehavior = WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
            barres.hide(WindowInsetsCompat.Type.systemBars())
            window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        } else {
            barres.show(WindowInsetsCompat.Type.systemBars())
            window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        }
    }

    private fun demanderPont() {
        if (checkSelfPermission(android.Manifest.permission.RECORD_AUDIO) != android.content.pm.PackageManager.PERMISSION_GRANTED) {
            requestPermissions(arrayOf(android.Manifest.permission.RECORD_AUDIO), 2)
            return
        }
        if (android.os.Build.VERSION.SDK_INT >= 33) requestPermissions(arrayOf(android.Manifest.permission.POST_NOTIFICATIONS), 1)
        @Suppress("DEPRECATION")
        startActivityForResult(getSystemService(android.media.projection.MediaProjectionManager::class.java).createScreenCaptureIntent(), DEMANDE_PONT)
    }

    @Deprecated("Une seule demande, sans dépendance de plus")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: android.content.Intent?) {
        @Suppress("DEPRECATION")
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode == DEMANDE_PONT && resultCode == RESULT_OK && data != null) {
            lireAdresseSon(reglages().getString("son", SON_ADRESSE)!!).let { (h, p) -> PontService.demarrer(this, resultCode, data, h, p) }
        }
    }

    // Le serveur n'accepte qu'un client : appli en arrière-plan, on lui rend la radio
    override fun onStop() {
        super.onStop()
        // le pont a besoin de la radio pendant que qFT8 est au premier plan
        if (Radio.pontActif) return
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
private fun Ecran(client: CatClient, son: Son, hote: String, port: Int, adresseSon: String, pasInitial: Long, seuilInitial: Float, cleSortie: String, cleEntree: String,
                  memoriserVolume: (Int) -> Unit, memoriserPas: (Long) -> Unit, demanderNotifications: () -> Unit,
                  memoriserSeuil: (Float) -> Unit, autoriserMicro: () -> Boolean, memoriserAppareil: (String, String) -> Unit, demanderPont: () -> Unit,
                  pleinEcranInitial: Boolean, basculerPleinEcran: (Boolean) -> Unit) {
    var pleinEcran by remember { mutableStateOf(pleinEcranInitial) }
    var infos by remember { mutableStateOf(false) }
    var pontActif by remember { mutableStateOf(false) }
    var etatPont by remember { mutableStateOf("") }
    var niveauPont by remember { mutableFloatStateOf(-96f) }
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
    var atu by remember { mutableIntStateOf(0) }
    var voyantTx by remember { mutableStateOf(false) }
    var puissance by remember { mutableIntStateOf(-1) }
    var puissanceGlissee by remember { mutableIntStateOf(-1) }    // doigt sur le curseur : la radio ne le fait pas reculer
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
            pontActif = Radio.pontActif
            etatPont = Radio.etatPont
            niveauPont = Radio.niveauPont
            atu = client.atu
            voyantTx = client.connecte && (client.enEmission || client.radioEmet)
            puissance = client.puissance
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
        Column(Modifier.fillMaxSize().safeDrawingPadding().padding(16.dp).verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            run {
                // en petite fenêtre, l'état et le bouton passent à la ligne au lieu d'être coupés
                FlowRow(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(16.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    Text("IC-7300  /  SCOPE LITE", Modifier.align(Alignment.CenterVertically), color = AMBRE, fontWeight = FontWeight.Bold, fontSize = 24.sp, softWrap = false)
                    Text(etat, Modifier.align(Alignment.CenterVertically), color = if (actif) VERT else Color.LightGray, maxLines = 2)
                    Button(onClick = { if (connecte) client.fermer() else client.connecter(hote, port) }) {
                        Text(if (connecte) "DÉCONNECTER" else "CONNECTER", softWrap = false)
                    }
                    Row(Modifier.align(Alignment.CenterVertically)) {
                        TextButton(onClick = { pleinEcran = !pleinEcran; basculerPleinEcran(pleinEcran) }) {
                            Text(if (pleinEcran) "⛶ Quitter" else "⛶", color = Color.White, fontSize = 18.sp, softWrap = false)
                        }
                        TextButton(onClick = { infos = true }) { Text("ⓘ", color = Color.White, fontSize = 18.sp) }
                    }
                }
                if (infos) InfosLite(hote, port, adresseSon, etat) { infos = false }
                Card(Modifier.fillMaxWidth(), shape = RoundedCornerShape(16.dp)) {
                    Column(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 10.dp), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(14.dp)) {
                            Text("WATERFALL", color = AMBRE, fontWeight = FontWeight.Bold)
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
                            Text("VFO", color = AMBRE, fontSize = 19.sp, fontWeight = FontWeight.Bold)
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
                        // fenêtre étroite : les pas défilent au lieu de repousser « + » hors de l'écran
                        Row(Modifier.horizontalScroll(rememberScrollState()), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            BoutonRepete("−", actif) { client.decaler(pas, -1) }
                            PAS.forEach { (nom, valeur) ->
                                FilterChip(selected = pas == valeur, onClick = { pas = valeur; memoriserPas(valeur) }, label = { Text(nom, softWrap = false) })
                            }
                            BoutonRepete("+", actif) { client.decaler(pas, 1) }
                        }
                        FlowRow(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                            Text("BANDE", Modifier.align(Alignment.CenterVertically), color = AMBRE, fontWeight = FontWeight.Bold)
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
                            Text("MODE", Modifier.align(Alignment.CenterVertically), color = AMBRE, fontWeight = FontWeight.Bold)
                            MODES_ECRAN.forEach { m ->
                                FilterChip(selected = actif && mode == m, enabled = actif, onClick = { client.changerMode(m) }, label = { Text(m) })
                            }
                        }
                        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(14.dp)) {
                            Text("S-MÈTRE", color = AMBRE, fontWeight = FontWeight.Bold)
                            val niveau = if (actif) smetre else 0
                            LinearProgressIndicator(progress = { niveau / 241f }, Modifier.weight(1f).height(18.dp), color = Color(0xFF48D69A), trackColor = Color(0xFF2B3239))
                            Text(texteS(niveau), color = Color.White, fontWeight = FontWeight.Bold, fontSize = 20.sp)
                        }
                    }
                }
                Card(Modifier.fillMaxWidth(), shape = RoundedCornerShape(16.dp)) {
                    Column(Modifier.fillMaxWidth().padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(14.dp)) {
                            Text("SON", color = AMBRE, fontSize = 19.sp, fontWeight = FontWeight.Bold)
                            Button(onClick = {
                                if (ecoute) {
                                    SonService.arreter(contexte)
                                } else {
                                    demanderNotifications()
                                    lireAdresseSon(adresseSon).let { (h, p) -> SonService.demarrer(contexte, h, p) }
                                }
                            }) { Text(if (ecoute) "COUPER" else "ÉCOUTER") }
                            Text("VOLUME", color = AMBRE, fontWeight = FontWeight.Bold)
                            Slider(value = volume.toFloat(), onValueChange = { volume = it.toInt(); son.volume = volume }, Modifier.weight(1f), valueRange = 0f..100f,
                                onValueChangeFinished = { memoriserVolume(volume) })
                            Text("$volume %", Modifier.width(70.dp), color = Color.White, fontWeight = FontWeight.Bold, fontSize = 20.sp)
                        }
                        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(14.dp)) {
                            ChoixAppareil("Sortie", true, cleSortie) { a -> son.sortie = a?.info; memoriserAppareil("sortie", a?.cle.orEmpty()) }
                            ChoixAppareil("Entrée", false, cleEntree) { a -> micro.entree = a?.info; memoriserAppareil("entree", a?.cle.orEmpty()) }
                            Text(etatSon, color = if (etatSon.endsWith("écoute")) VERT else Color.LightGray, fontSize = 14.sp)
                        }
                    }
                }
                Card(Modifier.fillMaxWidth(), shape = RoundedCornerShape(16.dp)) {
                    Column(Modifier.fillMaxWidth().padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(14.dp)) {
                            Text("ÉMISSION", color = AMBRE, fontSize = 19.sp, fontWeight = FontWeight.Bold)
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
                            Button(onClick = {}, enabled = actif && !voxActif && !pontActif, interactionSource = sourcePtt,
                                colors = if (enEmission) ButtonDefaults.buttonColors(containerColor = Color(0xFFE5342B), contentColor = Color.White) else null,
                                modifier = Modifier.height(56.dp).width(170.dp)) {
                                Text(if (enEmission) "ÉMISSION" else "PTT", fontWeight = FontWeight.Bold, fontSize = 20.sp, softWrap = false)
                            }
                            Button(onClick = { client.accorder() }, enabled = actif && !enEmission && atu != 2) { Text(if (atu == 2) "ACCORD…" else "TUNE", softWrap = false) }
                            FilterChip(selected = atu != 0, enabled = actif && !enEmission && atu != 2, onClick = { client.basculerAtu() }, label = { Text("ATU", fontWeight = if (atu != 0) FontWeight.Bold else FontWeight.Normal) },
                                colors = FilterChipDefaults.filterChipColors(selectedContainerColor = CYAN, selectedLabelColor = Color.Black,
                                    disabledSelectedContainerColor = CYAN.copy(alpha = 0.5f), disabledLabelColor = Color.Gray))
                            Text("VOX", color = AMBRE, fontWeight = FontWeight.Bold)
                            Switch(checked = voxActif, enabled = actif && !pontActif, onCheckedChange = { voxActif = it && autoriserMicro() })
                        }
                        // seuil du VOX sur sa propre ligne : la ligne ÉMISSION ne déborde plus
                        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(14.dp)) {
                            Text("SEUIL VOX", color = Color.LightGray)
                            Slider(value = seuil, onValueChange = { seuil = it }, Modifier.weight(1f), valueRange = -60f..-10f,
                                onValueChangeFinished = { memoriserSeuil(seuil) })
                            Text("${seuil.toInt()} dB", Modifier.width(70.dp), color = Color.White, softWrap = false)
                        }
                        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(14.dp)) {
                            Text("PUISSANCE", color = AMBRE, fontWeight = FontWeight.Bold)
                            Slider(value = (if (puissanceGlissee >= 0) puissanceGlissee else puissance).coerceAtLeast(0).toFloat(), enabled = actif && puissance >= 0,
                                onValueChange = { v ->
                                    val p = v.toInt()
                                    if (p != puissanceGlissee) client.reglerPuissance(p)
                                    puissanceGlissee = p
                                },
                                onValueChangeFinished = { puissanceGlissee = -1 }, modifier = Modifier.weight(1f), valueRange = 0f..100f)
                            Text(if (actif && puissance >= 0) "${if (puissanceGlissee >= 0) puissanceGlissee else puissance} %" else "-- %", Modifier.width(70.dp), color = Color.White, fontWeight = FontWeight.Bold, fontSize = 20.sp, softWrap = false)
                        }
                        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(14.dp)) {
                            Text("MICRO", color = Color.LightGray)
                            LinearProgressIndicator(progress = { ((niveauMicro + 60f) / 60f).coerceIn(0f, 1f) }, Modifier.weight(1f).height(10.dp),
                                color = if (niveauMicro >= seuil) Color(0xFFFFB300) else Color(0xFF48D69A), trackColor = Color(0xFF2B3239))
                            Text(texteEmission, color = if (enEmission) Color(0xFFFF5252) else Color.LightGray, fontSize = 14.sp)
                        }
                        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(14.dp)) {
                            Text("PONT qFT8", color = AMBRE, fontWeight = FontWeight.Bold)
                            // Voyant d'émission : allumé dès que la radio émet, quelle qu'en soit l'origine (qFT8, PTT, VOX, TUNE)
                            Canvas(Modifier.size(22.dp)) {
                                val rayon = size.minDimension / 2
                                if (voyantTx) drawCircle(Color(0x66FF1744), rayon)
                                drawCircle(if (voyantTx) Color(0xFFFF1744) else Color(0xFF3A1215), rayon * 0.68f)
                                drawCircle(Color(0xFF5B6770), rayon * 0.68f, style = Stroke(1.5f))
                            }
                            Switch(checked = pontActif, onCheckedChange = { if (it) { voxActif = false; demanderPont() } else PontService.arreter(contexte) })
                            LinearProgressIndicator(progress = { ((niveauPont + 60f) / 60f).coerceIn(0f, 1f) }, Modifier.width(160.dp).height(10.dp),
                                color = Color(0xFF48D69A), trackColor = Color(0xFF2B3239))
                            Text(if (pontActif) etatPont else "qFT8 fait passer la radio en émission", Modifier.weight(1f), color = if (etatPont == "qFT8 émet") Color(0xFFFF5252) else Color.LightGray, fontSize = 14.sp)
                        }
                    }
                }
                Text("Pilotage via le serveur IC-7300 Scope, TCP 7300. Un seul client à la fois.", color = Color.LightGray, fontSize = 14.sp)
            }
            Card(Modifier.fillMaxWidth(), shape = RoundedCornerShape(16.dp)) {
                Column(Modifier.fillMaxWidth().padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                        Text("MÉMOIRES RADIO", color = AMBRE, fontWeight = FontWeight.Bold, fontSize = 19.sp, softWrap = false)
                        fun aller(c: Int) { canal = c; client.lireMemoire(c) }
                        Button(enabled = actif, onClick = { aller(if (canal == 1) 99 else canal - 1) }) { Text("◀") }
                        Text(canal.toString().padStart(2, '0'), fontSize = 30.sp, color = Color.White, fontWeight = FontWeight.Bold)
                        Button(enabled = actif, onClick = { aller(if (canal == 99) 1 else canal + 1) }) { Text("▶") }
                        val choisie = memoires[canal]
                        Text(choisie?.let { "${it.nom.ifBlank { "Sans nom" }}  ${texteFrequence(it.hz)}  ${it.mode}" } ?: "Canal vide ou non lu", Modifier.weight(1f), color = Color.LightGray, fontSize = 14.sp)
                        Button(enabled = actif && choisie != null, onClick = { choisie?.let(client::appliquer) }) { Text("APPLIQUER", softWrap = false) }
                        Button(enabled = actif && lecture == 0, onClick = { client.lireMemoires() }) { Text(if (lecture != 0) "LECTURE $lecture/99" else "LIRE LA RADIO", softWrap = false) }
                    }
                    // Toute la largeur : les canaux lus se rangent sur plusieurs colonnes
                    FlowRow(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                        memoires.values.sortedBy { it.canal }.forEach { m ->
                            TextButton(enabled = actif, onClick = { canal = m.canal; client.appliquer(m) }) {
                                Text("${m.canal.toString().padStart(2, '0')}  ${m.nom.ifBlank { "Sans nom" }}  ${texteFrequence(m.hz)}  ${m.mode}", color = Color.White, fontSize = 14.sp)
                            }
                        }
                    }
                }
            }
        }
    }
}

/** Fenêtre « Informations » : version, serveur, son et rappel d'usage. */
@Composable
private fun InfosLite(hote: String, port: Int, adresseSon: String, etat: String, fermer: () -> Unit) {
    AlertDialog(
        onDismissRequest = fermer,
        confirmButton = { TextButton(onClick = fermer) { Text("Fermer") } },
        title = { Text("IC-7300 Scope Lite ${BuildConfig.VERSION_NAME}") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("Pilote l'IC-7300 branché sur le PC de la radio, par son serveur CAT (cat_server.py) : " +
                    "waterfall, fréquence, mode, son, émission.")
                Text("Serveur CAT : $hote:$port")
                Text("Relais de son : $adresseSon")
                if (etat.isNotBlank()) Text("État : $etat")
                Text("Le serveur n'accepte qu'un client à la fois : fermer l'appli IC-7300 Scope " +
                    "du PC (ou se déconnecter ici) pour laisser la radio à l'autre.", fontSize = 13.sp)
                Text("⛶ plein écran : barres masquées et écran gardé allumé ; " +
                    "glisser depuis un bord pour revoir les barres.", fontSize = 13.sp)
                Text("F4HXN — www.f4hxn.fr — GPL v3", color = Color.Gray, fontSize = 12.sp)
            }
        },
    )
}
