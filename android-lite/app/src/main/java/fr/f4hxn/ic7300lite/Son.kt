package fr.f4hxn.ic7300lite

import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioTrack
import java.net.InetSocketAddress
import java.net.Socket
import kotlin.concurrent.thread
import kotlin.math.pow

private const val TAUX = 16_000
private const val RETARD_MAX = TAUX      // octets en attente : 0,5 s, au-delà on saute pour rester en direct

/** Curseur 0 à 100 → gain : coupé à 0, puis de −20 à +40 dB (le son de la radio arrive vers −40 dBFS). */
fun gainDuVolume(volume: Int): Float =
    if (volume <= 0) 0f else 10f.pow((-20f + 60f * volume.coerceAtMost(100) / 100f) / 20f)

/** Amplifie des échantillons s16le sur place, sans déborder. */
fun amplifier(octets: ByteArray, taille: Int, gain: Float) {
    var i = 0
    while (i + 1 < taille) {
        val brut = (octets[i].toInt() and 0xFF) or (octets[i + 1].toInt() shl 8)
        val v = (brut * gain).toInt().coerceIn(-32768, 32767)
        octets[i] = v.toByte()
        octets[i + 1] = (v shr 8).toByte()
        i += 2
    }
}

/** Écoute le relais son_reseau.py, indépendant du serveur CAT. */
class Son {
    @Volatile var etat = "Son coupé"; private set
    @Volatile var enEcoute = false; private set
    @Volatile var volume = 70
    @Volatile private var socket: Socket? = null
    @Volatile private var session = 0

    fun demarrer(hote: String, port: Int) {
        arreter()
        enEcoute = true
        val moi = ++session
        thread(isDaemon = true) {
            while (session == moi) {
                try {
                    etat = "Son : connexion…"
                    jouer(hote, port, moi)
                    if (session == moi) etat = "Son : liaison coupée, nouvel essai"
                } catch (_: java.io.IOException) {
                    if (session == moi) etat = "Son : relais injoignable ($hote:$port)"
                }
                if (session == moi) Thread.sleep(3000)
            }
        }
    }

    fun arreter() {
        session++
        enEcoute = false
        etat = "Son coupé"
        runCatching { socket?.close() }
    }

    private fun jouer(hote: String, port: Int, moi: Int) {
        val format = AudioFormat.Builder().setEncoding(AudioFormat.ENCODING_PCM_16BIT)
            .setSampleRate(TAUX).setChannelMask(AudioFormat.CHANNEL_OUT_MONO).build()
        val piste = AudioTrack.Builder()
            .setAudioAttributes(AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_MEDIA)
                .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build())
            .setAudioFormat(format)
            .setBufferSizeInBytes(maxOf(AudioTrack.getMinBufferSize(TAUX, AudioFormat.CHANNEL_OUT_MONO, AudioFormat.ENCODING_PCM_16BIT), TAUX / 2))
            .setTransferMode(AudioTrack.MODE_STREAM).build()
        val s = Socket()
        socket = s
        try {
            s.connect(InetSocketAddress(hote, port), 5000)
            s.soTimeout = 5000          // radio ou relais muet : on le dit
            piste.play()
            val entree = s.getInputStream()
            val bloc = ByteArray(1600)
            var bout = 0                // octet impair resté du bloc d'avant
            while (session == moi) {
                val attente = entree.available()
                if (attente > RETARD_MAX) entree.skip(((attente - RETARD_MAX / 2) and 1.inv()).toLong())
                val lus = entree.read(bloc, bout, bloc.size - bout)
                if (lus < 0) return
                val total = bout + lus
                val pair = total and 1.inv()
                etat = "Son : à l'écoute"
                amplifier(bloc, pair, gainDuVolume(volume))
                piste.write(bloc, 0, pair)
                bout = total - pair
                if (bout == 1) bloc[0] = bloc[pair]
            }
        } finally {
            runCatching { s.close() }
            runCatching { piste.stop() }
            piste.release()
        }
    }
}
