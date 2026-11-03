package fr.f4hxn.ic7300lite

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.media.AudioFormat
import android.media.AudioPlaybackCaptureConfiguration
import android.media.AudioRecord
import android.media.projection.MediaProjection
import android.media.projection.MediaProjectionManager
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.PowerManager

const val PAQUET_QFT8 = "com.ft8.app"
private const val SEUIL_QFT8 = -60f     // qFT8 est soit muet, soit à plein niveau : pas besoin de réglage

/** Une émission FT8 dure 15 s : au-delà, il y a un problème (un son continu ne doit pas tenir la radio en émission). */
class GardePont(private val maxMs: Long = 20_000, tenueMs: Long = 400) {
    private val vox = Vox(tenueMs)
    private var debut = 0L
    private var bloque = false

    fun veutEmettre(niveau: Float, maintenant: Long): Boolean {
        val son = vox.actif(niveau, SEUIL_QFT8, maintenant)
        if (!son) {
            debut = 0
            bloque = false
            return false
        }
        if (debut == 0L) debut = maintenant
        if (maintenant - debut > maxMs) bloque = true
        return !bloque
    }

    val depasse get() = bloque
}

/**
 * Pont qFT8 : capte le son de qFT8 et de lui seul, et passe la radio en émission tant qu'il joue.
 * Vit dans un service car qFT8 est alors au premier plan, et l'écran de cette appli est arrêté.
 */
class PontService : Service() {
    private var projection: MediaProjection? = null
    private var veille: PowerManager.WakeLock? = null
    private val micro = Micro()
    private val garde = GardePont()

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_ARRET) {
            stopSelf()
            return START_NOT_STICKY
        }
        // le service doit être au premier plan avant de recevoir la capture
        startForeground(NOTIFICATION, notification(), ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION)
        val donnees: Intent? = if (Build.VERSION.SDK_INT >= 33) intent?.getParcelableExtra(EXTRA_DONNEES, Intent::class.java)
            else @Suppress("DEPRECATION") intent?.getParcelableExtra(EXTRA_DONNEES)
        if (intent == null || donnees == null) {
            stopSelf()
            return START_NOT_STICKY
        }
        demarrer(intent.getIntExtra(EXTRA_CODE, 0), donnees, intent.getStringExtra(EXTRA_HOTE)!!, intent.getIntExtra(EXTRA_PORT, 7301))
        return START_NOT_STICKY
    }

    private fun demarrer(code: Int, donnees: Intent, hote: String, port: Int) {
        val uid = try {
            packageManager.getApplicationInfo(PAQUET_QFT8, 0).uid
        } catch (_: android.content.pm.PackageManager.NameNotFoundException) {
            Radio.etatPont = "qFT8 n'est pas installé"
            stopSelf()
            return
        }
        val p = getSystemService(MediaProjectionManager::class.java).getMediaProjection(code, donnees)
        projection = p
        p.registerCallback(object : MediaProjection.Callback() {
            override fun onStop() = stopSelf()
        }, Handler(Looper.getMainLooper()))
        veille = getSystemService(PowerManager::class.java).newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "ic7300lite:pont").apply { acquire() }
        micro.fabrique = { taille ->
            val config = AudioPlaybackCaptureConfiguration.Builder(p).addMatchingUid(uid).build()
            val format = AudioFormat.Builder().setEncoding(AudioFormat.ENCODING_PCM_16BIT).setSampleRate(16_000).setChannelMask(AudioFormat.CHANNEL_IN_MONO).build()
            @Suppress("MissingPermission")
            AudioRecord.Builder().setAudioPlaybackCaptureConfig(config).setAudioFormat(format).setBufferSizeInBytes(taille).build()
        }
        micro.surBloc = { n -> traiter(n) }
        Radio.pontActif = true
        Radio.etatPont = "En attente de qFT8"
        micro.ouvrir(hote, port)
    }

    /** Un bloc tous les 20 ms : la radio suit le son de qFT8. */
    private fun traiter(niveau: Float) {
        val client = Radio.client
        Radio.niveauPont = niveau
        val veut = garde.veutEmettre(niveau, System.currentTimeMillis())
        client.emettre(veut)
        val emet = veut && client.enEmission
        micro.envoyer = emet
        Radio.son.muet = emet
        Radio.etatPont = when {
            micro.etat.isNotEmpty() -> micro.etat
            garde.depasse -> "Son de qFT8 trop long : émission coupée"
            emet -> "qFT8 émet"
            niveau >= SEUIL_QFT8 && !client.connecte -> "qFT8 joue, mais la radio n'est pas connectée : pas d'émission"
            !client.connecte -> "Radio non connectée"
            else -> "En attente de qFT8"
        }
    }

    private fun notification(): Notification {
        getSystemService(NotificationManager::class.java).createNotificationChannel(NotificationChannel(CANAL, "Pont qFT8", NotificationManager.IMPORTANCE_LOW))
        val ouvrir = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)
        val arret = PendingIntent.getService(this, 1, Intent(this, PontService::class.java).setAction(ACTION_ARRET), PendingIntent.FLAG_IMMUTABLE)
        return Notification.Builder(this, CANAL)
            .setSmallIcon(android.R.drawable.ic_btn_speak_now)
            .setContentTitle("IC-7300 : qFT8 peut passer la radio en émission")
            .setContentIntent(ouvrir)
            .setOngoing(true)
            .addAction(Notification.Action.Builder(null, "ARRÊTER", arret).build())
            .build()
    }

    override fun onDestroy() {
        micro.surBloc = {}
        micro.fermer()
        Radio.client.emettre(false)
        Radio.son.muet = false
        Radio.pontActif = false
        Radio.niveauPont = -96f
        Radio.etatPont = ""
        runCatching { projection?.stop() }
        runCatching { veille?.release() }
        super.onDestroy()
    }

    companion object {
        private const val CANAL = "pont"
        private const val NOTIFICATION = 2
        private const val ACTION_ARRET = "fr.f4hxn.ic7300lite.PONT_ARRET"
        private const val EXTRA_CODE = "code"
        private const val EXTRA_DONNEES = "donnees"
        private const val EXTRA_HOTE = "hote"
        private const val EXTRA_PORT = "port"

        fun demarrer(context: Context, code: Int, donnees: Intent, hote: String, port: Int) {
            context.startForegroundService(Intent(context, PontService::class.java)
                .putExtra(EXTRA_CODE, code).putExtra(EXTRA_DONNEES, donnees).putExtra(EXTRA_HOTE, hote).putExtra(EXTRA_PORT, port))
        }

        fun arreter(context: Context) {
            context.stopService(Intent(context, PontService::class.java))
        }
    }
}
