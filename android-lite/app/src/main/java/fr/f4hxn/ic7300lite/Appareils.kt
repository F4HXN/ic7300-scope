package fr.f4hxn.ic7300lite

import android.content.Context
import android.media.AudioDeviceInfo
import android.media.AudioManager

/** Un appareil audio de la tablette, retrouvé d'une session à l'autre par sa clé (l'id Android change). */
class Appareil(val info: AudioDeviceInfo) {
    val cle = "${info.type}:${info.productName}"
    val nom: String
        get() {
            val type = when (info.type) {
                AudioDeviceInfo.TYPE_BUILTIN_SPEAKER -> "Haut-parleur"
                AudioDeviceInfo.TYPE_BUILTIN_MIC -> "Micro de la tablette"
                AudioDeviceInfo.TYPE_WIRED_HEADSET -> "Casque filaire"
                AudioDeviceInfo.TYPE_WIRED_HEADPHONES -> "Écouteurs filaires"
                AudioDeviceInfo.TYPE_BLUETOOTH_A2DP -> "Bluetooth"
                AudioDeviceInfo.TYPE_BLUETOOTH_SCO -> "Bluetooth (appel)"
                AudioDeviceInfo.TYPE_BLE_HEADSET, AudioDeviceInfo.TYPE_BLE_SPEAKER -> "Bluetooth LE"
                AudioDeviceInfo.TYPE_USB_DEVICE, AudioDeviceInfo.TYPE_USB_HEADSET, AudioDeviceInfo.TYPE_USB_ACCESSORY -> "USB"
                AudioDeviceInfo.TYPE_HDMI -> "HDMI"
                else -> "Autre"
            }
            val produit = info.productName?.toString().orEmpty()
            val interne = info.type == AudioDeviceInfo.TYPE_BUILTIN_SPEAKER || info.type == AudioDeviceInfo.TYPE_BUILTIN_MIC
            return if (interne || produit.isBlank()) type else "$type : $produit"
        }
}

private val TYPES_SORTIE = setOf(
    AudioDeviceInfo.TYPE_BUILTIN_SPEAKER, AudioDeviceInfo.TYPE_WIRED_HEADSET, AudioDeviceInfo.TYPE_WIRED_HEADPHONES,
    AudioDeviceInfo.TYPE_BLUETOOTH_A2DP, AudioDeviceInfo.TYPE_BLE_HEADSET, AudioDeviceInfo.TYPE_BLE_SPEAKER,
    AudioDeviceInfo.TYPE_USB_DEVICE, AudioDeviceInfo.TYPE_USB_HEADSET, AudioDeviceInfo.TYPE_USB_ACCESSORY, AudioDeviceInfo.TYPE_HDMI,
)
private val TYPES_ENTREE = setOf(
    AudioDeviceInfo.TYPE_BUILTIN_MIC, AudioDeviceInfo.TYPE_WIRED_HEADSET, AudioDeviceInfo.TYPE_BLUETOOTH_SCO,
    AudioDeviceInfo.TYPE_BLE_HEADSET, AudioDeviceInfo.TYPE_USB_DEVICE, AudioDeviceInfo.TYPE_USB_HEADSET, AudioDeviceInfo.TYPE_USB_ACCESSORY,
)

fun appareils(context: Context, sortie: Boolean): List<Appareil> {
    val am = context.getSystemService(AudioManager::class.java)
    val genre = if (sortie) AudioManager.GET_DEVICES_OUTPUTS else AudioManager.GET_DEVICES_INPUTS
    val types = if (sortie) TYPES_SORTIE else TYPES_ENTREE
    return am.getDevices(genre).filter { it.type in types }.map(::Appareil).distinctBy { it.cle }
}
