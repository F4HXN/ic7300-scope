package fr.f4hxn.ic7300lite

import android.annotation.SuppressLint
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import java.net.InetSocketAddress
import java.net.Socket
import kotlin.concurrent.thread
import kotlin.math.log10
import kotlin.math.sqrt

/** Niveau d'un bloc s16le en dBFS. */
fun niveauDbfs(octets: ByteArray, taille: Int): Float {
    var somme = 0.0
    var n = 0
    var i = 0
    while (i + 1 < taille) {
        val v = (octets[i].toInt() and 0xFF) or (octets[i + 1].toInt() shl 8)
        somme += v.toDouble() * v
        n++
        i += 2
    }
    if (n == 0 || somme == 0.0) return -96f
    return (20 * log10(sqrt(somme / n) / 32768.0)).toFloat().coerceAtLeast(-96f)
}

/** VOX : part dès que le niveau passe le seuil, tient encore `tenueMs` après le dernier mot. */
class Vox(private val tenueMs: Long = 800) {
    private var dernierDepassement = Long.MIN_VALUE / 2

    fun actif(niveau: Float, seuil: Float, maintenant: Long): Boolean {
        if (niveau >= seuil) dernierDepassement = maintenant
        return maintenant - dernierDepassement < tenueMs
    }
}

/**
 * Micro de la tablette : lu tant qu'il est ouvert, pour le niveau et le VOX, mais
 * envoyé au relais seulement pendant l'émission.
 */
class Micro {
    @Volatile var niveau = -96f; private set
    @Volatile var etat = ""; private set
    @Volatile var ouvert = false; private set
    @Volatile var envoyer = false
    @Volatile var surBloc: (Float) -> Unit = {}
    @Volatile private var session = 0
    @Volatile private var socket: Socket? = null

    @SuppressLint("MissingPermission")     // l'écran demande la permission avant d'ouvrir
    fun ouvrir(hote: String, port: Int) {
        if (ouvert) return
        ouvert = true
        val moi = ++session
        thread(isDaemon = true) {
            var enregistreur: AudioRecord? = null
            try {
                val taille = maxOf(AudioRecord.getMinBufferSize(16_000, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT), 3200)
                enregistreur = AudioRecord(MediaRecorder.AudioSource.VOICE_COMMUNICATION, 16_000, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, taille)
                enregistreur.startRecording()
                val s = Socket()
                socket = s
                s.connect(InetSocketAddress(hote, port), 5000)
                s.tcpNoDelay = true
                val sortie = s.getOutputStream()
                sortie.write("TX1\n".toByteArray())
                sortie.flush()
                etat = ""
                val bloc = ByteArray(640)   // 20 ms
                while (session == moi) {
                    val lus = enregistreur.read(bloc, 0, bloc.size)
                    if (lus <= 0) break
                    val n = niveauDbfs(bloc, lus)
                    niveau = n
                    surBloc(n)
                    if (envoyer) sortie.write(bloc, 0, lus)
                }
            } catch (e: java.io.IOException) {
                if (session == moi) etat = "Micro : relais injoignable"
            } catch (e: IllegalStateException) {
                if (session == moi) etat = "Micro indisponible"
            } finally {
                runCatching { enregistreur?.stop() }
                enregistreur?.release()
                runCatching { socket?.close() }
                if (session == moi) ouvert = false
                niveau = -96f
            }
        }
    }

    fun fermer() {
        session++
        ouvert = false
        envoyer = false
        runCatching { socket?.close() }
    }
}
