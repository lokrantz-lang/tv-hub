package se.tvhub.box

import android.app.Activity
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.provider.Settings
import android.widget.Toast
import org.json.JSONArray
import org.json.JSONObject

/**
 * Fjärrstart och röststyrning på TV-boxarna.
 *
 * Boxen håller en öppen förfrågan mot TV Hub (/device/commands). När Google/Home Assistant,
 * telefonen ("Spela på TV:n") eller webbadminen skickar ett kommando får boxen det direkt:
 * spela en titel, pausa, fortsätta, stänga spelaren eller öppna Filmkväll.
 *
 * Fjärrstarten slår alltid av vuxenläget först.
 */
object Control {
    fun start(context: Context) {
        Foreground.register(context)
        val prefs = Prefs(context)
        if (FormFactor.isMobile(context) || !prefs.paired) return
        val intent = Intent(context, ControlService::class.java)
        try {
            if (Build.VERSION.SDK_INT >= 26) context.startForegroundService(intent) else context.startService(intent)
        } catch (e: Exception) {
            android.util.Log.w("TVHub", "Fjärrstart: kunde inte starta tjänsten: ${e.message}")
        }
    }

    /**
     * Android 10 och senare (Z10 Pro) låter inte en app starta en skärm från bakgrunden,
     * utom om den fått "Visa ovanpå andra appar". Android 7 (Z Alpha) behöver inget.
     */
    fun needsOverlayPermission(context: Context): Boolean =
        Build.VERSION.SDK_INT >= 29 && !Settings.canDrawOverlays(context)

    /** Öppnar Androids inställning för "Visa ovanpå andra appar". false om boxen saknar den. */
    fun openOverlaySettings(activity: Activity): Boolean {
        val intents = listOf(
            Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION, Uri.parse("package:${activity.packageName}")),
            Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION),
        )
        for (intent in intents) {
            try {
                activity.startActivity(intent)
                return true
            } catch (_: Exception) {
            }
        }
        return false
    }

    /** Inställningen "Röststyrning när appen är stängd" på TV:n. */
    fun showSettings(activity: Activity) {
        if (Build.VERSION.SDK_INT < 29) {
            Look.message(activity, "Röststyrning och fjärrstart",
                "Den här boxen (Android ${Build.VERSION.RELEASE}) behöver ingen extra behörighet. " +
                    "Kommandon fungerar även när appen är stängd, så länge boxen är på.")
            return
        }
        if (!needsOverlayPermission(activity)) {
            Look.message(activity, "Röststyrning och fjärrstart",
                "Klart. TV Hub får starta uppspelning även när appen är stängd, så länge boxen är på.")
            return
        }
        Look.confirm(activity, "Röststyrning när appen är stängd",
            "För att TV Hub ska kunna starta uppspelning när appen är stängd behöver den behörigheten " +
                "\"Visa ovanpå andra appar\".\n\nI nästa skärm: välj TV Hub och slå på den. Tryck sedan Tillbaka.",
            "Öppna inställningen") {
            if (!openOverlaySettings(activity)) {
                Look.message(activity, "Gör så här",
                    "Boxen öppnade inte inställningen direkt. Gå till Androids Inställningar → Appar → " +
                        "Särskild åtkomst (Special app access) → Visa ovanpå andra appar → TV Hub → På.")
            }
        }
    }

    /** Utför ett kommando. Körs på huvudtråden. */
    fun execute(context: Context, cmd: JSONObject) {
        val prefs = Prefs(context)
        if (prefs.adultMode) prefs.adultMode = false
        prefs.touch()
        val name = cmd.optString("name")
        when (cmd.optString("action")) {
            "play" -> {
                PlayerActivity.current?.get()?.finish()
                if (name.isNotEmpty()) Toast.makeText(context, "▶ $name", Toast.LENGTH_SHORT).show()
                PlayerActivity.start(
                    context,
                    type = cmd.optString("type", "vod"),
                    id = cmd.optString("id"),
                    name = name,
                    ext = cmd.optStringOrNull("ext"),
                    queueIds = strings(cmd.optJSONArray("queue_ids")),
                    queueNames = strings(cmd.optJSONArray("queue_names")),
                    queueExts = strings(cmd.optJSONArray("queue_exts")),
                    queueIndex = cmd.optInt("queue_index", -1),
                    seriesId = cmd.optStringOrNull("series_id"),
                    icon = cmd.optStringOrNull("icon"),
                    catchupStart = cmd.optLong("catchup_start", 0L),
                    catchupMinutes = cmd.optInt("catchup_minutes", 0),
                    autoResume = cmd.optBoolean("auto_resume", false),
                )
            }
            "pause", "resume", "stop" -> PlayerActivity.current?.get()?.remote(cmd.optString("action"))
            "night" -> {
                PlayerActivity.current?.get()?.finish()
                FilmNightActivity.start(context, cmd.optInt("viewer", 0))
            }
            "reminder" -> reminder(context, cmd)
        }
    }

    /** Påminnelse: fråga på skärmen om boxen ska byta kanal. Vuxenpåminnelser bara i vuxenläget. */
    private fun reminder(context: Context, cmd: JSONObject) {
        val prefs = Prefs(context)
        if (cmd.optBoolean("adult") && !prefs.adultMode) return
        val activity = Foreground.current()
        if (activity != null) {
            showReminder(activity, cmd)
            return
        }
        // Appen är stängd: öppna den med frågan (Z10 Pro behöver "Visa ovanpå andra appar").
        val target = if (prefs.modern) HomeActivity::class.java else MainActivity::class.java
        try {
            context.startActivity(Intent(context, target).putExtra("reminder", cmd.toString()).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        } catch (_: Exception) {
        }
    }

    fun showReminder(activity: Activity, cmd: JSONObject) {
        if (activity.isFinishing) return
        val channelId = cmd.optString("channel_id")
        val channel = cmd.optString("channel")
        val clock = java.text.SimpleDateFormat("HH:mm", java.util.Locale("sv", "SE")).format(java.util.Date(cmd.optLong("start") * 1000))
        Look.confirm(activity, "🔔 ${cmd.optString("title")}", "Börjar $clock på $channel.", "Byt kanal") {
            PlayerActivity.current?.get()?.let { if (it !== activity) it.finish() }
            PlayerActivity.start(activity, type = "live", id = channelId, name = channel, ext = null)
        }
    }

    private fun strings(array: JSONArray?): ArrayList<String>? {
        if (array == null) return null
        val out = ArrayList<String>(array.length())
        for (i in 0 until array.length()) out.add(array.optString(i))
        return out
    }

    /** Påminnelse som öppnade appen (se [reminder]). */
    fun handleIntent(activity: Activity, intent: Intent?) {
        val raw = intent?.getStringExtra("reminder") ?: return
        intent.removeExtra("reminder")
        try {
            showReminder(activity, JSONObject(raw))
        } catch (_: Exception) {
        }
    }
}

/** Vilken skärm i appen som syns just nu (för påminnelser). */
object Foreground : android.app.Application.ActivityLifecycleCallbacks {
    private var resumed: java.lang.ref.WeakReference<Activity>? = null
    private var registered = false

    fun register(context: Context) {
        if (registered) return
        registered = true
        (context.applicationContext as? android.app.Application)?.registerActivityLifecycleCallbacks(this)
    }

    fun current(): Activity? = resumed?.get()?.takeIf { !it.isFinishing }

    override fun onActivityResumed(activity: Activity) {
        resumed = java.lang.ref.WeakReference(activity)
    }

    override fun onActivityPaused(activity: Activity) {
        if (resumed?.get() === activity) resumed = null
    }

    override fun onActivityCreated(activity: Activity, savedInstanceState: android.os.Bundle?) {}
    override fun onActivityStarted(activity: Activity) {}
    override fun onActivityStopped(activity: Activity) {}
    override fun onActivitySaveInstanceState(activity: Activity, outState: android.os.Bundle) {}
    override fun onActivityDestroyed(activity: Activity) {}
}


/**
 * Lyssnar efter kommandon i bakgrunden, även när appen är stängd. Visar en notis (krav från
 * Android för tjänster som ska få leva vidare).
 */
class ControlService : Service() {
    companion object {
        private const val CHANNEL = "remote_control"
        private const val NOTE_ID = 4712
    }

    private val main = Handler(Looper.getMainLooper())
    @Volatile private var running = false
    private var thread: Thread? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        Api.init(Prefs(this))
        if (Build.VERSION.SDK_INT >= 26) {
            val channel = NotificationChannel(CHANNEL, "Röststyrning och fjärrstart", NotificationManager.IMPORTANCE_MIN)
            (getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager).createNotificationChannel(channel)
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        startForeground(NOTE_ID, notification())
        if (!running) {
            running = true
            thread = Thread({ loop() }, "tvhub-control").also { it.isDaemon = true; it.start() }
        }
        return START_STICKY
    }

    override fun onDestroy() {
        running = false
        thread?.interrupt()
        super.onDestroy()
    }

    private fun loop() {
        var backoff = 2_000L
        while (running) {
            try {
                val response = Api.requestBlocking("GET", "/device/commands?wait=25", readTimeoutMs = 40_000) as? JSONObject
                backoff = 2_000L
                val commands = response?.optJSONArray("commands") ?: continue
                for (i in 0 until commands.length()) {
                    val cmd = commands.optJSONObject(i) ?: continue
                    main.post {
                        try {
                            Control.execute(this, cmd)
                        } catch (e: Exception) {
                            android.util.Log.w("TVHub", "Fjärrstart: ${e.message}")
                        }
                    }
                }
            } catch (e: ApiException) {
                if (e.status == 401 || e.status == 403) {
                    // Boxen är inte längre parad. Startas igen när den paras.
                    running = false
                    main.post { stopSelf() }
                    return
                }
                pause(backoff)
                backoff = (backoff * 2).coerceAtMost(60_000L)
            } catch (_: InterruptedException) {
                return
            } catch (e: Exception) {
                // TV Hub omstartas, nätet borta, boxen vaknar: försök igen lite senare.
                pause(backoff)
                backoff = (backoff * 2).coerceAtMost(60_000L)
            }
        }
    }

    private fun pause(ms: Long) {
        try {
            Thread.sleep(ms)
        } catch (_: InterruptedException) {
            running = false
        }
    }

    private fun notification(): Notification {
        val builder = if (Build.VERSION.SDK_INT >= 26) Notification.Builder(this, CHANNEL) else Notification.Builder(this)
        @Suppress("DEPRECATION")
        if (Build.VERSION.SDK_INT < 26) builder.setPriority(Notification.PRIORITY_MIN)
        return builder
            .setSmallIcon(android.R.drawable.ic_media_play)
            .setContentTitle("TV Hub")
            .setContentText("Lyssnar efter röststyrning och fjärrstart")
            .setOngoing(true)
            .build()
    }
}
