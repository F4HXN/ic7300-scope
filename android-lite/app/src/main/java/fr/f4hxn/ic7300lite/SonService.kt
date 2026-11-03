package fr.f4hxn.ic7300lite

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.net.wifi.WifiManager
import android.os.Build
import android.os.IBinder
import android.os.PowerManager

/** Le son vit ici plutôt que dans l'écran : il continue quand l'appli passe en arrière-plan. */
object Radio {
    val son = Son()
    val client = CatClient()
    @Volatile var pontActif = false
    @Volatile var etatPont = ""
    @Volatile var niveauPont = -96f
}

/**
 * Service au premier plan : Android exige une notification en échange. Il garde le processeur
 * et le Wi-Fi éveillés écran éteint, tant qu'on écoute.
 */
class SonService : Service() {
    private var veille: PowerManager.WakeLock? = null
    private var wifi: WifiManager.WifiLock? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_ARRET) {
            Radio.son.arreter()
            stopSelf()
            return START_NOT_STICKY
        }
        val notification = notification()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(NOTIFICATION, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PLAYBACK)
        } else {
            startForeground(NOTIFICATION, notification)
        }
        if (veille == null) {
            veille = (getSystemService(POWER_SERVICE) as PowerManager)
                .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "ic7300lite:son").apply { acquire() }
            @Suppress("DEPRECATION")
            wifi = (applicationContext.getSystemService(WIFI_SERVICE) as WifiManager)
                .createWifiLock(WifiManager.WIFI_MODE_FULL_HIGH_PERF, "ic7300lite:son").apply { acquire() }
        }
        intent?.getStringExtra(EXTRA_HOTE)?.let { Radio.son.demarrer(it, intent.getIntExtra(EXTRA_PORT, 7301)) }
        return START_NOT_STICKY
    }

    private fun notification(): Notification {
        val canal = NotificationChannel(CANAL, "Son de la radio", NotificationManager.IMPORTANCE_LOW)
        getSystemService(NotificationManager::class.java).createNotificationChannel(canal)
        val ouvrir = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)
        val arret = PendingIntent.getService(this, 1, Intent(this, SonService::class.java).setAction(ACTION_ARRET), PendingIntent.FLAG_IMMUTABLE)
        return Notification.Builder(this, CANAL)
            .setSmallIcon(android.R.drawable.stat_sys_headset)
            .setContentTitle("IC-7300 : écoute en cours")
            .setContentIntent(ouvrir)
            .setOngoing(true)
            .addAction(Notification.Action.Builder(null, "COUPER", arret).build())
            .build()
    }

    override fun onDestroy() {
        Radio.son.arreter()
        runCatching { veille?.release() }
        runCatching { wifi?.release() }
        super.onDestroy()
    }

    companion object {
        private const val CANAL = "son"
        private const val NOTIFICATION = 1
        private const val ACTION_ARRET = "fr.f4hxn.ic7300lite.ARRET"
        private const val EXTRA_HOTE = "hote"
        private const val EXTRA_PORT = "port"

        fun demarrer(context: Context, hote: String, port: Int) {
            context.startForegroundService(Intent(context, SonService::class.java).putExtra(EXTRA_HOTE, hote).putExtra(EXTRA_PORT, port))
        }

        fun arreter(context: Context) {
            context.stopService(Intent(context, SonService::class.java))
        }
    }
}
