package se.tvhub.box

import android.app.AlarmManager
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.Environment
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.PowerManager
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.FileOutputStream
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.Executors

/** En inspelning enligt schemat i TV Hub. Filen ligger på boxens USB-minne. */
data class Rec(
    val id: Int,
    val streamId: String,
    val channel: String,
    val title: String,
    val start: Long,
    val end: Long,
    val source: String,
    val status: String,
    val sizeBytes: Long,
    val file: String?,
    val error: String?,
    val ext: String? = null,
    val seriesId: String? = null,
) {
    /** Film eller avsnitt som laddas ner (inte en tv-sändning). */
    val media: Boolean
        get() = source == "vod" || source == "episode"

    /** Inspelningar som har ett slut av sig själva (arkiv, film, avsnitt) och hämtas en i taget. */
    val finite: Boolean
        get() = source != "live"

    val localFile: File?
        get() = file?.let { File(it) }?.takeIf { it.isFile && it.length() > 0 }

    /** "lör 18 okt 20:00–20:30" */
    val timeLabel: String
        get() {
            if (media) return "Tillagd " + SimpleDateFormat("d MMM", Locale("sv", "SE")).format(Date(start * 1000)).replace(".", "")
            val day = SimpleDateFormat("EEE d MMM HH:mm", Locale("sv", "SE")).format(Date(start * 1000))
            val endClock = SimpleDateFormat("HH:mm", Locale("sv", "SE")).format(Date(end * 1000))
            return "$day–$endClock"
        }

    companion object {
        fun from(o: JSONObject) = Rec(
            id = o.optInt("id"),
            streamId = o.optString("stream_id"),
            channel = o.optStringOrNull("channel") ?: "",
            title = o.optStringOrNull("title") ?: "",
            start = o.optLong("start"),
            end = o.optLong("end"),
            source = o.optString("source", "live"),
            status = o.optString("status"),
            sizeBytes = o.optLong("size_bytes", 0L),
            file = o.optStringOrNull("file"),
            error = o.optStringOrNull("error"),
            ext = o.optStringOrNull("ext"),
            seriesId = o.optStringOrNull("series_id"),
        )

        fun list(array: JSONArray?): List<Rec> {
            val out = ArrayList<Rec>()
            if (array == null) return out
            for (i in 0 until array.length()) array.optJSONObject(i)?.let { out.add(from(it)) }
            return out
        }

        fun size(bytes: Long): String = when {
            bytes >= 1_000_000_000L -> String.format("%.1f GB", bytes / 1e9).replace('.', ',')
            bytes > 0 -> "${bytes / 1_000_000} MB"
            else -> ""
        }
    }
}

/** Var inspelningarna sparas. Appens egen mapp på USB-minnet kräver ingen behörighet. */
object RecStorage {
    data class Volume(val dir: File, val removable: Boolean) {
        val free: Long
            get() = dir.usableSpace

        val label: String
            get() = (if (removable) "USB-minne" else "Boxens interna minne") + " · ${Rec.size(free)} ledigt"
    }

    fun volumes(context: Context): List<Volume> {
        val out = ArrayList<Volume>()
        for (dir in context.getExternalFilesDirs(null)) {
            if (dir == null) continue
            val removable = try {
                Environment.isExternalStorageRemovable(dir)
            } catch (_: Exception) {
                false
            }
            out.add(Volume(dir, removable))
        }
        return out
    }

    /** Vald enhet, annars första USB-minnet. Null om inget USB-minne sitter i. */
    fun chosen(context: Context, prefs: Prefs): Volume? {
        val all = volumes(context)
        val wanted = prefs.recordDir
        if (wanted.isNotEmpty()) all.firstOrNull { it.dir.absolutePath == wanted }?.let { return it }
        return all.firstOrNull { it.removable }
    }

    fun folder(volume: Volume): File = File(volume.dir, "Inspelningar").also { it.mkdirs() }

    fun fileFor(folder: File, rec: Rec): File {
        if (rec.media) {
            val ext = rec.ext?.takeIf { it.matches(Regex("[A-Za-z0-9]{1,8}")) } ?: "mp4"
            val name = rec.title.replace(Regex("[^\\p{L}\\p{N} ._()-]"), "_").replace(Regex("\\s+"), " ").trim().take(120)
            return File(folder, "$name.$ext")
        }
        val stamp = SimpleDateFormat("yyyy-MM-dd HHmm", Locale("sv", "SE")).format(Date(rec.start * 1000))
        val raw = "$stamp ${rec.channel} - ${rec.title}"
        val safe = raw.replace(Regex("[^\\p{L}\\p{N} ._()-]"), "_").replace(Regex("\\s+"), " ").trim().take(120)
        return File(folder, "$safe.ts")
    }
}

/** Alarm hos Android som väcker boxen när en inspelning ska börja. */
object RecScheduler {
    const val ACTION_SYNC = "se.tvhub.box.REC_SYNC"
    const val ACTION_START = "se.tvhub.box.REC_START"
    const val ACTION_STOP = "se.tvhub.box.REC_STOP"
    const val ACTION_DELETE = "se.tvhub.box.REC_DELETE"

    /** Marginaler: programtider stämmer sällan på minuten. */
    const val PRE_S = 60L
    const val POST_S = 180L
    private const val SYNC_INTERVAL_MS = 20 * 60 * 1000L

    fun sync(context: Context) = send(context, ACTION_SYNC, 0)

    fun send(context: Context, action: String, id: Int) {
        val intent = Intent(context, RecordService::class.java).setAction(action).putExtra("id", id)
        if (Build.VERSION.SDK_INT >= 26) context.startForegroundService(intent) else context.startService(intent)
    }

    private fun pending(context: Context, action: String, id: Int): PendingIntent {
        val intent = Intent(context, RecordReceiver::class.java).setAction(action).putExtra("id", id)
        return PendingIntent.getBroadcast(context, id, intent, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
    }

    private fun alarms(context: Context) = context.getSystemService(Context.ALARM_SERVICE) as AlarmManager

    fun schedule(context: Context, rec: Rec) {
        val at = (rec.start - PRE_S) * 1000
        val pi = pending(context, ACTION_START, rec.id)
        try {
            alarms(context).setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, at, pi)
        } catch (_: SecurityException) {
            alarms(context).setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, at, pi)
        }
    }

    fun cancel(context: Context, id: Int) {
        alarms(context).cancel(pending(context, ACTION_START, id))
    }

    /** Hämtar schemat regelbundet, så att inspelningar planerade från mobilen kommer med. */
    fun schedulePeriodicSync(context: Context) {
        val first = System.currentTimeMillis() + SYNC_INTERVAL_MS
        alarms(context).setInexactRepeating(AlarmManager.RTC_WAKEUP, first, SYNC_INTERVAL_MS, pending(context, ACTION_SYNC, 0))
    }
}

/** Tar emot alarm, omstart av boxen och appuppdatering. */
class RecordReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val action = intent.action ?: return
        if (!Prefs(context).paired) return
        when (action) {
            RecScheduler.ACTION_START -> RecScheduler.send(context, RecScheduler.ACTION_START, intent.getIntExtra("id", 0))
            else -> {
                RecScheduler.sync(context)
                // Efter omstart eller uppdatering: lyssna efter röststyrning igen.
                if (action == Intent.ACTION_BOOT_COMPLETED || action == Intent.ACTION_MY_PACKAGE_REPLACED) Control.start(context)
            }
        }
    }
}

/**
 * Spelar in i bakgrunden, även när appen inte är öppen. Visar en notis medan den arbetar
 * (krav från Android) och håller boxen vaken under inspelningen.
 */
class RecordService : Service() {
    companion object {
        private const val CHANNEL = "recordings"
        private const val NOTE_ID = 4711

        /** Inspelningen kräver minst så här mycket ledigt utrymme. */
        const val MIN_FREE_BYTES = 500L * 1024 * 1024

        /** Filer som spelas in just nu (spelaren kan titta medan inspelningen pågår). */
        private val active = ConcurrentHashMap<String, Int>()

        fun isRecording(path: String) = active.containsKey(path)

        fun isRecordingId(id: Int) = active.containsValue(id)
    }

    private val jobs = ConcurrentHashMap<Int, RecordJob>()
    private val worker = Executors.newSingleThreadExecutor()
    private val main = Handler(Looper.getMainLooper())
    private lateinit var prefs: Prefs
    private var wakeLock: PowerManager.WakeLock? = null
    @Volatile private var pendingTasks = 0

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        prefs = Prefs(this)
        Api.init(prefs)
        if (Build.VERSION.SDK_INT >= 26) {
            val channel = NotificationChannel(CHANNEL, "Inspelningar", NotificationManager.IMPORTANCE_LOW)
            (getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager).createNotificationChannel(channel)
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        startForeground(NOTE_ID, notification())
        val action = intent?.action ?: RecScheduler.ACTION_SYNC
        val id = intent?.getIntExtra("id", 0) ?: 0
        pendingTasks++
        worker.execute {
            try {
                when (action) {
                    RecScheduler.ACTION_STOP -> jobs[id]?.cancel(delete = false)
                    RecScheduler.ACTION_DELETE -> delete(id)
                    else -> sync()
                }
            } catch (e: Exception) {
                android.util.Log.w("TVHub", "Inspelningar: ${e.message}")
            } finally {
                main.post {
                    pendingTasks--
                    refresh()
                }
            }
        }
        return START_STICKY
    }

    override fun onDestroy() {
        for (job in jobs.values) job.cancel(delete = false)
        releaseWakeLock()
        super.onDestroy()
    }

    // ---------------------------------------------------------------- schemat

    private fun sync() {
        if (!prefs.paired) return
        val recs = Rec.list(Api.requestBlocking("GET", "/device/recordings?all=1") as? JSONArray)
        val now = System.currentTimeMillis() / 1000
        val alarms = HashSet<Int>()
        val known = HashSet<Int>()
        for (rec in recs) {
            known.add(rec.id)
            try {
                handle(rec, now, alarms)
            } catch (e: Exception) {
                android.util.Log.w("TVHub", "Inspelning ${rec.id}: ${e.message}")
            }
        }
        for (id in prefs.scheduledRecordings) {
            if (id !in alarms) RecScheduler.cancel(this, id)
            if (id !in known) jobs[id]?.cancel(delete = true)
        }
        prefs.scheduledRecordings = alarms
        RecScheduler.schedulePeriodicSync(this)
    }

    private fun handle(rec: Rec, now: Long, alarms: HashSet<Int>) {
        when (rec.status) {
            "deleted" -> delete(rec.id, rec)
            "scheduled" -> when {
                rec.finite -> startJob(rec)
                rec.start - RecScheduler.PRE_S <= now + 5 -> startJob(rec)
                else -> {
                    RecScheduler.schedule(this, rec)
                    alarms.add(rec.id)
                }
            }
            "recording" -> if (!jobs.containsKey(rec.id)) {
                if (rec.finite || rec.end + RecScheduler.POST_S > now) {
                    startJob(rec)  // boxen startades om mitt i
                } else {
                    val size = rec.localFile?.length() ?: 0L
                    report(rec.id, if (size > 1_000_000) "done" else "failed", size, rec.file,
                        if (size > 1_000_000) null else "Inspelningen avbröts.")
                }
            }
        }
    }

    private fun startJob(rec: Rec) {
        if (jobs.containsKey(rec.id)) return
        // Arkivhämtningar en i taget; nästa startar när den här är klar.
        if (rec.finite && jobs.values.any { it.rec.finite }) return
        val volume = RecStorage.chosen(this, prefs)
        if (volume == null) {
            report(rec.id, "failed", 0, null, "Inget USB-minne hittades i boxen.")
            return
        }
        if (volume.free < MIN_FREE_BYTES) {
            report(rec.id, "failed", 0, null, "USB-minnet är fullt.")
            return
        }
        val job = RecordJob(this, rec, RecStorage.folder(volume))
        jobs[rec.id] = job
        active[job.file.absolutePath] = rec.id
        main.post { refresh() }
        job.start()
    }

    private fun delete(id: Int, rec: Rec? = null) {
        val job = jobs[id]
        if (job != null) {
            job.cancel(delete = true)  // jobbet raderar filen och raden när det har stoppat
            return
        }
        val known = rec ?: Rec.list(Api.requestBlocking("GET", "/device/recordings?all=1") as? JSONArray).firstOrNull { it.id == id }
        known?.file?.let { File(it).delete() }
        RecScheduler.cancel(this, id)
        Api.requestBlocking("DELETE", "/device/recordings/$id")
    }

    /** Anropas av jobbet när det är klart. */
    fun finished(job: RecordJob) {
        jobs.remove(job.rec.id)
        active.remove(job.file.absolutePath)
        main.post { refresh() }
        // En väntande arkivhämtning kan starta nu.
        if (job.rec.finite) RecScheduler.sync(this)
    }

    fun report(id: Int, status: String, size: Long, file: String?, error: String?) {
        val body = JSONObject().put("status", status).put("size_bytes", size)
        if (file != null) body.put("file", file)
        if (error != null) body.put("error", error)
        for (attempt in 1..3) {
            try {
                Api.requestBlocking("POST", "/device/recordings/$id/status", body)
                return
            } catch (_: Exception) {
                Thread.sleep(3_000L * attempt)
            }
        }
    }

    // ---------------------------------------------------------------- notis och väckning

    private fun notification(): Notification {
        val titles = jobs.values.map { "${it.rec.channel}: ${it.rec.title}" }
        val text = if (titles.isEmpty()) "Kontrollerar inspelningar…" else titles.joinToString(", ")
        val builder = if (Build.VERSION.SDK_INT >= 26) Notification.Builder(this, CHANNEL) else Notification.Builder(this)
        return builder
            .setSmallIcon(android.R.drawable.ic_media_play)
            .setContentTitle(if (titles.isEmpty()) "TV Hub" else "Spelar in")
            .setContentText(text)
            .setOngoing(true)
            .build()
    }

    private fun refresh() {
        if (jobs.isEmpty() && pendingTasks <= 0) {
            releaseWakeLock()
            stopForeground(true)
            stopSelf()
            return
        }
        (getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager).notify(NOTE_ID, notification())
        if (jobs.isNotEmpty() && wakeLock == null) {
            val pm = getSystemService(Context.POWER_SERVICE) as PowerManager
            wakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "tvhub:recording").also { it.acquire(8 * 3600 * 1000L) }
        }
    }

    private fun releaseWakeLock() {
        wakeLock?.let { if (it.isHeld) it.release() }
        wakeLock = null
    }
}

/**
 * Ett inspelningsjobb. Direktsändning: läser strömmen till sluttiden och ansluter igen
 * om den bryts. Arkiv: laddar ner programmet från Toucans catch-up.
 */
class RecordJob(private val service: RecordService, val rec: Rec, folder: File) : Thread("rec-${rec.id}") {
    private val archive = rec.source == "archive"
    private val media = rec.media
    private val finite = rec.finite
    val file: File = rec.file?.let { File(it) }?.takeIf { it.parentFile?.isDirectory == true } ?: RecStorage.fileFor(folder, rec)
    @Volatile private var stopRequested = false
    @Volatile private var deleteAfter = false
    @Volatile private var connection: HttpURLConnection? = null
    @Volatile private var sessionId: String? = null
    @Volatile private var fatal = false
    @Volatile private var completed = false
    private var error: String? = null

    fun cancel(delete: Boolean) {
        deleteAfter = deleteAfter || delete
        stopRequested = true
        try {
            connection?.disconnect()
        } catch (_: Exception) {
        }
    }

    override fun run() {
        val endAtMs = (rec.end + RecScheduler.POST_S) * 1000
        service.report(rec.id, "recording", file.length(), file.absolutePath, null)
        var failures = 0
        try {
            if (archive && file.exists()) file.delete()  // arkivet hämtas alltid från början
            while (!stopRequested) {
                if (!finite && System.currentTimeMillis() >= endAtMs) break
                if (file.parentFile?.isDirectory != true) {
                    error = "USB-minnet togs bort."
                    break
                }
                val stream = requestStream()
                if (stream == null) {
                    if (stopRequested || fatal) break
                    failures++
                    // Direktsändning: fortsätt försöka till sluttiden. Arkiv: ge upp efter några försök.
                    if (finite && failures > (if (media) 8 else 4)) break
                    sleepQuietly(5_000)
                    continue
                }
                val complete = download(stream.first, stream.second, endAtMs)
                endSession()
                if (complete) break
                failures++
                if (finite && failures >= (if (media) 8 else 4)) {
                    error = error ?: if (media) "Filen gick inte att hämta från källan." else "Programmet gick inte att hämta från arkivet."
                    break
                }
                if (archive && file.exists()) file.delete()
                sleepQuietly(3_000)
            }
        } catch (e: Exception) {
            error = e.message ?: e.javaClass.simpleName
        } finally {
            endSession()
            finish()
        }
    }

    /** Frågar TV Hub efter strömadress. Inspelningar räknas inte av anslutningsvakten. */
    private fun requestStream(): Pair<String, String>? {
        val body = JSONObject().put("id", rec.streamId).put("recording_id", rec.id).put("name", rec.title)
        if (media) {
            body.put("type", rec.source)
            rec.ext?.let { body.put("ext", it) }
            rec.seriesId?.let { body.put("series_id", it) }
        } else if (archive) {
            val start = rec.start - RecScheduler.PRE_S
            val now = System.currentTimeMillis() / 1000
            val stop = minOf(rec.end + RecScheduler.PRE_S, now)
            val minutes = (((stop - start) + 59) / 60).toInt().coerceIn(1, 600)
            body.put("type", "catchup").put("start", start).put("duration", minutes)
        } else {
            body.put("type", "live")
        }
        val response = try {
            Api.requestBlocking("POST", "/device/play", body) as? JSONObject
        } catch (e: Exception) {
            error = Api.friendly(e)
            return null
        } ?: return null
        when (response.optString("status")) {
            "ok" -> {
                error = null
                sessionId = response.optStringOrNull("session_id")
                return response.getString("url") to (response.optStringOrNull("user_agent") ?: "IPTVSmartersPlayer")
            }
            "waiting" -> {
                sleepQuietly(response.optLong("retry_ms", 2_000L))
                return null
            }
            else -> {
                error = response.optString("error", "TV Hub nekade inspelningen.")
                if (error?.contains("finns inte längre") == true) {
                    fatal = true
                    stopRequested = true
                }
                return null
            }
        }
    }

    /** true = klar (arkivet slut, sluttiden nådd eller stoppad). false = försök igen. */
    private fun download(url: String, userAgent: String, endAtMs: Long): Boolean {
        val conn = URL(url).openConnection() as HttpURLConnection
        connection = conn
        var lastBeat = System.currentTimeMillis()
        var lastSpaceCheck = lastBeat
        var bytes = 0L
        var lastReport = lastBeat
        try {
            conn.connectTimeout = 10_000
            conn.readTimeout = 20_000
            conn.instanceFollowRedirects = true
            conn.setRequestProperty("User-Agent", userAgent)
            // Film och avsnitt: fortsätt där nedladdningen avbröts, om källan tillåter det.
            val already = if (media && file.exists()) file.length() else 0L
            if (already > 0) conn.setRequestProperty("Range", "bytes=$already-")
            val code = conn.responseCode
            if (code == 416 && already > 0) {
                completed = true  // redan helt nedladdad
                return true
            }
            if (code >= 400) {
                error = "Källan svarade $code."
                return false
            }
            val append = !media || (already > 0 && code == 206)
            if (media) {
                val remaining = conn.getHeaderField("Content-Length")?.toLongOrNull() ?: -1L
                if (remaining > 0 && (file.parentFile?.usableSpace ?: 0L) < remaining + 200L * 1024 * 1024) {
                    error = "Filen får inte plats på USB-minnet (${Rec.size(remaining)} behövs)."
                    fatal = true
                    stopRequested = true
                    return true
                }
            }
            conn.inputStream.use { input ->
                FileOutputStream(file, append).use { output ->
                    val buffer = ByteArray(64 * 1024)
                    while (!stopRequested) {
                        val n = input.read(buffer)
                        if (n < 0) {
                            // Arkiv, film och avsnitt är slut: klart om något kom. En liveström ska inte ta slut.
                            completed = finite && file.length() > 1_000_000
                            return completed
                        }
                        output.write(buffer, 0, n)
                        bytes += n
                        val now = System.currentTimeMillis()
                        if (!finite && now >= endAtMs) return true
                        if (media && now - lastReport >= 30_000) {
                            lastReport = now
                            service.report(rec.id, "recording", file.length(), file.absolutePath, null)
                        }
                        if (now - lastBeat >= 10_000) {
                            lastBeat = now
                            heartbeat(bytes)
                        }
                        if (now - lastSpaceCheck >= 30_000) {
                            lastSpaceCheck = now
                            if (file.parentFile?.usableSpace ?: 0L < RecordService.MIN_FREE_BYTES / 5) {
                                error = "USB-minnet blev fullt."
                                stopRequested = true
                            }
                        }
                    }
                }
            }
            return true
        } catch (e: IOException) {
            if (stopRequested) return true
            error = "Strömmen bröts: ${e.message ?: e.javaClass.simpleName}"
            return false
        } finally {
            connection = null
            conn.disconnect()
        }
    }

    private fun heartbeat(bytes: Long) {
        val sid = sessionId ?: return
        try {
            val metrics = JSONObject().put("recorded_bytes", bytes)
            val response = Api.requestBlocking("POST", "/device/heartbeat", JSONObject().put("session_id", sid).put("metrics", metrics)) as? JSONObject
            if (response != null && response.optBoolean("stop")) stopRequested = true
        } catch (_: Exception) {
            // Tillfälligt fel mot TV Hub stoppar inte inspelningen.
        }
    }

    private fun endSession() {
        val sid = sessionId ?: return
        sessionId = null
        try {
            Api.requestBlocking("POST", "/device/stop", JSONObject().put("session_id", sid).put("reason", "recording_done"))
        } catch (_: Exception) {
        }
    }

    private fun finish() {
        try {
            if (deleteAfter) {
                file.delete()
                RecScheduler.cancel(service, rec.id)
                try {
                    Api.requestBlocking("DELETE", "/device/recordings/${rec.id}")
                } catch (_: Exception) {
                }
                return
            }
            val size = if (file.exists()) file.length() else 0L
            if (media && !completed) {
                // En halv film är inte till nytta: avbruten eller misslyckad nedladdning raderas.
                if (file.exists()) file.delete()
                service.report(rec.id, "failed", 0, null, error ?: "Nedladdningen avbröts.")
            } else if (size > 1_000_000) {
                // Stoppad i förtid eller med avbrott: filen sparas ändå, felet visas.
                service.report(rec.id, "done", size, file.absolutePath, error)
            } else {
                if (file.exists()) file.delete()
                service.report(rec.id, "failed", 0, null, error ?: "Ingenting spelades in.")
            }
        } finally {
            service.finished(this)
        }
    }

    private fun sleepQuietly(ms: Long) {
        try {
            sleep(ms)
        } catch (_: InterruptedException) {
        }
    }
}

/** Spela in (ladda ner) filmer och avsnitt till USB-minnet. Används från detaljsidor och menyer. */
object RecordMedia {
    private fun post(activity: android.app.Activity, body: JSONObject, done: (JSONObject) -> String) {
        Api.obj("POST", "/device/recordings", body) { result ->
            result.onSuccess {
                RecScheduler.sync(activity)
                android.widget.Toast.makeText(activity, done(it), android.widget.Toast.LENGTH_LONG).show()
            }.onFailure { android.widget.Toast.makeText(activity, Api.friendly(it), android.widget.Toast.LENGTH_LONG).show() }
        }
    }

    fun film(activity: android.app.Activity, vodId: String) {
        post(activity, JSONObject().put("kind", "vod").put("id", vodId)) { rec ->
            if (rec.optString("status") == "done") "Filmen finns redan under Inspelningar." else
                "⬇ Filmen laddas ner till USB-minnet. Följ den under Inspelningar."
        }
    }

    fun episode(activity: android.app.Activity, episodeId: String, title: String, ext: String?, seriesId: String) {
        val body = JSONObject().put("kind", "episode").put("id", episodeId).put("title", title).put("series_id", seriesId)
        ext?.let { body.put("ext", it) }
        post(activity, body) { "⬇ Avsnittet laddas ner till USB-minnet." }
    }

    fun series(activity: android.app.Activity, seriesId: String, season: String? = null) {
        val body = JSONObject().put("kind", "series").put("series_id", seriesId)
        season?.let { body.put("season", it) }
        post(activity, body) { res ->
            val created = res.optInt("created")
            val existing = res.optInt("existing")
            when {
                created == 0 -> "Alla avsnitt finns redan under Inspelningar."
                else -> "⬇ $created avsnitt laddas ner till USB-minnet, ett i taget." +
                    if (existing > 0) " ($existing fanns redan.)" else ""
            }
        }
    }
}
