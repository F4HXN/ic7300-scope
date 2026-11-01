package fr.f4hxn.ic7300lite

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.*
import java.net.Socket
import java.util.concurrent.atomic.AtomicBoolean

private val rigAddr = 0x94
private val cyan = Color(0xFF4FC3F7)
private val bg = Color(0xFF101418)
private val modes = listOf("LSB", "LSB-D", "USB", "USB-D", "AM", "CW", "RTTY", "FM", "CW-R", "RTTY-R")
private val modeCode = mapOf("LSB" to 0, "USB" to 1, "AM" to 2, "CW" to 3, "RTTY" to 4, "FM" to 5, "CW-R" to 7, "RTTY-R" to 8)
private data class Memory(val channel:Int, val name:String, val hz:Long, val mode:String)

class MainActivity : ComponentActivity() {
 override fun onCreate(savedInstanceState:Bundle?) { super.onCreate(savedInstanceState); setContent { MaterialTheme(colorScheme=darkColorScheme(primary=cyan,background=bg,surface=Color(0xFF1C252D))) { RadioScreen() } } }
}

@Composable private fun RadioScreen() {
 val scope= rememberCoroutineScope(); var host by remember { mutableStateOf("192.168.1.") }; var port by remember { mutableStateOf("7300") }
 var status by remember { mutableStateOf("Déconnecté") }; var connected by remember { mutableStateOf(false) }; var frequency by remember { mutableLongStateOf(14_074_000L) }; var mode by remember { mutableStateOf("USB-D") }; var meter by remember { mutableIntStateOf(0) }; var powered by remember { mutableStateOf(false) }; var vfo by remember { mutableStateOf("A") }; var mem by remember { mutableStateOf(emptyList<Memory>()) }; var currentMem by remember { mutableIntStateOf(1) }; var freqText by remember { mutableStateOf("14.074.000") }; var showMem by remember { mutableStateOf(false) }; val client= remember { CatClient() }
 DisposableEffect(Unit) { onDispose { client.close() } }
 fun update() { connected=client.connected; status=client.status; frequency=client.frequency; freqText=String.format(java.util.Locale.US,"%d.%03d.%03d",frequency/1_000_000,(frequency/1000)%1000,frequency%1000); mode=client.mode; meter=client.meter; powered=client.powered; vfo=client.vfo; mem=client.memories }
 LaunchedEffect(connected) { while(connected) { delay(400); update() } }
 Surface(Modifier.fillMaxSize(), color=bg) { Row(Modifier.fillMaxSize().padding(20.dp), horizontalArrangement=Arrangement.spacedBy(20.dp)) {
  Column(Modifier.weight(1.6f), verticalArrangement=Arrangement.spacedBy(12.dp)) {
   Row(verticalAlignment=Alignment.CenterVertically) { Text("IC-7300  /  SCOPE LITE", color=cyan, fontWeight=FontWeight.Bold, fontSize=24.sp); Spacer(Modifier.weight(1f)); Text(status,color=if(connected) Color(0xFF61D38B) else Color.LightGray) }
   Row(horizontalArrangement=Arrangement.spacedBy(8.dp), verticalAlignment=Alignment.CenterVertically) { OutlinedTextField(host,{host=it},Modifier.weight(1f),label={Text("IP du serveur IC-7300")},singleLine=true); OutlinedTextField(port,{port=it},Modifier.width(110.dp),label={Text("Port")},singleLine=true); Button(onClick={ if(connected) { client.close(); connected=false; status="Déconnecté" } else { status="Connexion…"; client.connect(host,port.toIntOrNull()?:7300) { update() } } }) { Text(if(connected) "DÉCONNECTER" else "CONNECTER") } }
  Card(Modifier.fillMaxWidth(),shape=RoundedCornerShape(16.dp)) { Column(Modifier.fillMaxWidth().padding(18.dp),horizontalAlignment=Alignment.CenterHorizontally,verticalArrangement=Arrangement.spacedBy(12.dp)) {
   Row(verticalAlignment=Alignment.CenterVertically) { Text("VFO",color=cyan,fontSize=19.sp,fontWeight=FontWeight.Bold); Spacer(Modifier.width(14.dp)); listOf("A" to 0x00,"B" to 0x01).forEach { (name,code)->FilterChip(selected=vfo==name,onClick={client.send(0x07,code)},label={Text(name)}) }; Spacer(Modifier.weight(1f)); Button(onClick={client.send(0x18,if(powered) 0x00 else 0x01)}) { Text(if(powered) "⏻  ÉTEINDRE" else "⏻  ALLUMER") } }
   Text(freqText, fontSize=54.sp, fontWeight=FontWeight.Bold, color=Color(0xFF70D9FF))
   Row(verticalAlignment=Alignment.CenterVertically,horizontalArrangement=Arrangement.spacedBy(8.dp)) { OutlinedTextField(freqText,{freqText=it},Modifier.width(250.dp),label={Text("Fréquence (Hz)")},singleLine=true); Button(onClick={freqText.toLongOrNull()?.let{client.setFrequency(it)}}){Text("RÉGLER")}; Button(onClick={client.setFrequency((frequency-1000).coerceAtLeast(0))}){Text("− 1 kHz")}; Button(onClick={client.setFrequency(frequency+1000)}){Text("+ 1 kHz")} }
   Row(verticalAlignment=Alignment.CenterVertically) { Text("MODE",color=cyan,fontWeight=FontWeight.Bold); Spacer(Modifier.width(10.dp)); modes.forEach { m -> FilterChip(selected=mode==m,onClick={client.setMode(m)},label={Text(m)},modifier=Modifier.padding(horizontal=2.dp)) } }
   Row(Modifier.fillMaxWidth(),verticalAlignment=Alignment.CenterVertically) { Text("S-MÈTRE",color=cyan,fontWeight=FontWeight.Bold); Spacer(Modifier.width(16.dp)); LinearProgressIndicator(progress={meter.coerceIn(0,241)/241f},Modifier.weight(1f).height(18.dp),color=Color(0xFF48D69A),trackColor=Color(0xFF2B3239)); Spacer(Modifier.width(12.dp)); Text(sText(meter),color=Color.White,fontWeight=FontWeight.Bold,fontSize=20.sp) }
  } }
   Row(verticalAlignment=Alignment.CenterVertically) { Text("MÉMOIRES RADIO",color=cyan,fontWeight=FontWeight.Bold,fontSize=19.sp); Spacer(Modifier.weight(1f)); Button(onClick={showMem=!showMem}) {Text(if(showMem) "FERMER" else "CHOISIR MÉMOIRE")}; Button(onClick={client.readMemories()}) {Text("LIRE LA RADIO")} }
   if(showMem) LazyColumn(verticalArrangement=Arrangement.spacedBy(4.dp)) { items(mem){m -> TextButton(onClick={currentMem=m.channel;client.setFrequency(m.hz);client.setMode(m.mode)}) { Text("${m.channel.toString().padStart(2,'0')}  ${m.name.ifBlank{"Canal ${m.channel}"}}    ${m.hz} Hz    ${m.mode}",color=Color.White) } } }
  }
  Column(Modifier.width(210.dp).fillMaxHeight(),verticalArrangement=Arrangement.spacedBy(12.dp),horizontalAlignment=Alignment.CenterHorizontally) { Text("MÉMOIRE",color=cyan,fontWeight=FontWeight.Bold); Text(currentMem.toString().padStart(2,'0'),fontSize=42.sp,color=Color.White,fontWeight=FontWeight.Bold); Row { Button(onClick={currentMem=(currentMem-2).coerceAtLeast(0)+1;client.readMemory(currentMem)}){Text("◀")}; Button(onClick={currentMem=(currentMem%99)+1;client.readMemory(currentMem)}){Text("▶")} }; Text("Pilotage via serveur\nIC-7300 Scope · TCP 7300",color=Color.LightGray,fontSize=14.sp) }
 } }
}

private fun sText(v:Int)=if(v<=0) "S0" else if(v<=120) "S${(v*9/120).coerceIn(0,9)}" else "S9+${((v-120)*60/121).coerceAtMost(60)} dB"

private class CatClient {
 @Volatile var status="Déconnecté"; @Volatile var frequency=14_074_000L; @Volatile var mode="USB"; @Volatile var meter=0; @Volatile var powered=false; @Volatile var vfo="A"; @Volatile var memories=emptyList<Memory>(); @Volatile var connected=false
 private var socket:Socket?=null; private var job:Job?=null; private val lock=Any(); private val running=AtomicBoolean(false); private val buffer=ArrayList<Int>(); private var memoryChannel=0
 fun connect(host:String,port:Int,changed:()->Unit) { close(); job=CoroutineScope(Dispatchers.IO).launch { try { val s=Socket(host,port); s.tcpNoDelay=true; socket=s; running.set(true); connected=true; status="Connecté à $host:$port"; val input=s.getInputStream(); while(running.get()) { if(input.available()>0) parse(input.read()); else { query(); delay(250) } }; } catch(e:Exception){status="Connexion perdue : ${e.message}";connected=false} finally {running.set(false);connected=false;try{socket?.close()}catch(_:Exception){};changed()} } }
 fun send(vararg data:Int) { CoroutineScope(Dispatchers.IO).launch { synchronized(lock){try{socket?.getOutputStream()?.write(byteArrayOf(0xFE.toByte(),0xFE.toByte(),0x94.toByte(),0xE0.toByte(),*data.map{it.toByte()}.toByteArray(),0xFD.toByte()))}catch(e:Exception){status="Erreur réseau : ${e.message}"}}} }
 fun setFrequency(hz:Long){send(0x05,*bcdLe(hz,5))}; fun setMode(m:String){val base=if(m.endsWith("-D"))m.dropLast(2) else m;val c=mapOf("LSB" to 0,"USB" to 1,"AM" to 2,"CW" to 3,"RTTY" to 4,"FM" to 5,"CW-R" to 7,"RTTY-R" to 8)[base]?:1;send(0x06,c,if(m.endsWith("-D"))1 else 0)}
 fun query(){send(0x03);send(0x04);send(0x15,0x02);send(0x07);send(0x1C,0x00);send(0x0F)}
 fun readMemories(){memories=emptyList();memoryChannel=1;readMemory(1)}
 fun readMemory(ch:Int){memoryChannel=ch.coerceIn(1,99);send(0x1A,0x00,0x00,*bcdBe(memoryChannel))}
 private fun parse(b:Int){if(b==0xFE && buffer.isEmpty()){buffer.add(b);return};if(buffer.isEmpty())return;buffer.add(b);if(b!=0xFD)return;val f=buffer.toIntArray();buffer.clear();if(f.size<6||f[2]!=0xE0||f[3]!=0x94||f[4]==0xFA)return;val d=f.slice(5 until f.lastIndex);when(d.firstOrNull()){0x03->{frequency=bcdDecode(d.drop(1));};0x04->{val x=d.getOrNull(1)?:0;mode=mapOf(0 to "LSB",1 to "USB",2 to "AM",3 to "CW",4 to "RTTY",5 to "FM",7 to "CW-R",8 to "RTTY-R")[x]?:mode};0x15->{if(d.getOrNull(1)==2)meter=bcdDecode(d.drop(2)).toInt().coerceIn(0,241)};0x07->{when(d.getOrNull(1)){0x00->vfo="A";0x01->vfo="B"}};0x18->{powered=true};0x1C->{powered=true};0x1A->{if(d.size==1) {if(memoryChannel>0){val content=d.drop(1);if(content.singleOrNull()==0xFF){memories=memories+Memory(memoryChannel,"",0,"USB")}else if(content.size>=39){val hz=bcdDecode(content.subList(1,6));val md=mapOf(content[6] to "LSB",1 to "USB",2 to "AM",3 to "CW",4 to "RTTY",5 to "FM",7 to "CW-R",8 to "RTTY-R")[content[6]]?:"USB";val name=content.takeLast(10).map{it.toChar()}.joinToString("").trim();memories=memories+Memory(memoryChannel,name,hz,md)};if(memoryChannel<99)readMemory(++memoryChannel) else memoryChannel=0}}}} }
 private fun bcdDecode(v:List<Int>):Long { var out=0L;for(b in v.reversed()) out=out*100+((b shr 4)*10)+(b and 15);return out }
 private fun bcdLe(value:Long,n:Int):IntArray {var v=value;return IntArray(n){val lo=(v%10).toInt();v/=10;val hi=(v%10).toInt();v/=10;(hi shl 4) or lo}}
 private fun bcdBe(value:Int):IntArray {var n=value;val d=IntArray(2);for(i in 1 downTo 0){d[i]=((n%10));n/=10;d[i]=((n%10) shl 4) or d[i];n/=10};return d}
 fun close(){running.set(false);job?.cancel();try{socket?.close()}catch(_:Exception){};connected=false}
}
