package se.tvhub.box

import android.content.Context

/** Sparade inställningar: serveradress, boxens nyckel, dolt läge och val av ljud/undertexter. */
class Prefs(context: Context) {
    val appContext: Context = context.applicationContext
    private val sp = appContext.getSharedPreferences("tvhub", Context.MODE_PRIVATE)

    companion object {
        /** Vuxenläget stängs av efter så här lång tid utan knapptryck. */
        const val ADULT_IDLE_MS = 15 * 60 * 1000L

        /** … och senast så här länge efter att det slogs på. */
        const val ADULT_MAX_MS = 3 * 60 * 60 * 1000L
    }

    var server: String
        get() = sp.getString("server", "") ?: ""
        set(value) {
            sp.edit().putString("server", value).apply()
        }

    var token: String
        get() = sp.getString("token", "") ?: ""
        set(value) {
            sp.edit().putString("token", value).apply()
        }

    var deviceName: String
        get() = sp.getString("device_name", "") ?: ""
        set(value) {
            sp.edit().putString("device_name", value).apply()
        }

    /**
     * Dolt läge: visar bara vuxeninnehåll. Växlas med tre tryck vänster på Inställningar.
     * Stängs av automatiskt efter 15 minuter utan aktivitet, efter högst 3 timmar
     * och när appen startas om (se MainActivity).
     */
    var adultMode: Boolean
        get() {
            if (!sp.getBoolean("adult_mode", false)) return false
            val now = System.currentTimeMillis()
            val idle = now - sp.getLong("adult_last_active", 0L)
            val age = now - sp.getLong("adult_since", 0L)
            if (idle > ADULT_IDLE_MS || age > ADULT_MAX_MS) {
                sp.edit().putBoolean("adult_mode", false).apply()
                return false
            }
            return true
        }
        set(value) {
            val now = System.currentTimeMillis()
            sp.edit()
                .putBoolean("adult_mode", value)
                .putLong("adult_since", now)
                .putLong("adult_last_active", now)
                .putBoolean("adult_history", false)  // vuxenläget startar alltid utan historik
                .apply()
        }

    /**
     * Vuxenläget: visa favoriter, Fortsätt titta och sedda (dolt val: höger tre gånger på Inställningar).
     * Tittandet sparas alltid; det här styr bara vad som visas.
     */
    var adultHistory: Boolean
        get() = sp.getBoolean("adult_history", false)
        set(value) {
            sp.edit().putBoolean("adult_history", value).apply()
        }

    /** Anropas vid knapptryck och under uppspelning så att vuxenläget inte stängs av mitt i. */
    fun touch() {
        if (sp.getBoolean("adult_mode", false)) {
            sp.edit().putLong("adult_last_active", System.currentTimeMillis()).apply()
        }
    }

    var audioLanguage: String
        get() = sp.getString("audio_lang", "") ?: ""
        set(value) {
            sp.edit().putString("audio_lang", value).apply()
        }

    /** "" = automatiskt, "off" = av, annars språkkod. */
    var subtitleLanguage: String
        get() = sp.getString("sub_lang", "") ?: ""
        set(value) {
            sp.edit().putString("sub_lang", value).apply()
        }

    /** Var inspelningar sparas: sökvägen till en lagringsenhet. Tom = första USB-minnet. */
    var recordDir: String
        get() = sp.getString("record_dir", "") ?: ""
        set(value) {
            sp.edit().putString("record_dir", value).apply()
        }

    /** Inspelningar som har ett alarm hos Android, så att borttagna kan avregistreras. */
    var scheduledRecordings: Set<Int>
        get() = (sp.getStringSet("rec_alarms", emptySet()) ?: emptySet()).mapNotNull { it.toIntOrNull() }.toSet()
        set(value) {
            sp.edit().putStringSet("rec_alarms", value.map { it.toString() }.toSet()).apply()
        }

    /** Var man slutade titta på en inspelning. */
    fun recordingPosition(path: String): Long = sp.getLong("pos:$path", 0L)

    fun setRecordingPosition(path: String, positionMs: Long) {
        sp.edit().putLong("pos:$path", positionMs).apply()
    }

    /** Utseende: modernt (standard) eller klassiskt (tre kolumner som tidigare). Mobilen är alltid modern. */
    /** Egna val för fjärrkontrollens knappar (JSON), se [Keys]. */
    var keyMap: String
        get() = sp.getString("key_map", "") ?: ""
        set(value) {
            sp.edit().putString("key_map", value).apply()
        }

    var modern: Boolean
        get() = sp.getBoolean("modern_ui", true) || FormFactor.isMobile(appContext)
        set(value) {
            sp.edit().putBoolean("modern_ui", value).apply()
        }

    /** Telefonen: Filmer och Serier som lista i stället för rutnät. */
    var mobileList: Boolean
        get() = sp.getBoolean("mobile_list", false)
        set(value) {
            sp.edit().putBoolean("mobile_list", value).apply()
        }

    /** Senaste sökningar i det moderna utseendet. */
    var recentSearches: List<String>
        get() = (sp.getString("recent_searches", "") ?: "").split("\n").filter { it.isNotBlank() }
        set(value) {
            sp.edit().putString("recent_searches", value.take(8).joinToString("\n")).apply()
        }

    /** "auto" (standard), "mobile" eller "tv". Se [FormFactor]. */
    var layoutMode: String
        get() = sp.getString(FormFactor.KEY, "auto") ?: "auto"
        set(value) {
            sp.edit().putString(FormFactor.KEY, value).apply()
        }

    // Utanför hemmet (Nabu Casa). Hämtas från TV Hub när telefonen är hemma, se Remote.
    var remoteUrl: String
        get() = sp.getString("remote_url", "") ?: ""
        set(value) {
            sp.edit().putString("remote_url", value).apply()
        }

    var remoteIngress: String
        get() = sp.getString("remote_ingress", "") ?: ""
        set(value) {
            sp.edit().putString("remote_ingress", value).apply()
        }

    var remoteHaToken: String
        get() = sp.getString("remote_ha_token", "") ?: ""
        set(value) {
            sp.edit().putString("remote_ha_token", value).apply()
        }

    val remoteReady: Boolean
        get() = remoteUrl.startsWith("https://") && remoteIngress.startsWith("/") && remoteHaToken.isNotEmpty()

    /**
     * Filmkväll: vem som tittar. Gäller till kl 05 nästa morgon, sedan måste det väljas igen.
     * Skickas med i anropen så att tummar och "sett klart" hamnar på rätt tittarläge.
     */
    val activeViewer: Int?
        get() {
            val id = sp.getInt("viewer_id", 0)
            return if (id > 0 && System.currentTimeMillis() < sp.getLong("viewer_until", 0L)) id else null
        }

    val activeViewerName: String
        get() = if (activeViewer != null) sp.getString("viewer_name", "") ?: "" else ""

    fun setActiveViewer(id: Int?, name: String = "") {
        if (id == null) {
            sp.edit().remove("viewer_id").remove("viewer_until").apply()
            return
        }
        val cal = java.util.Calendar.getInstance()
        if (cal.get(java.util.Calendar.HOUR_OF_DAY) >= 5) cal.add(java.util.Calendar.DAY_OF_YEAR, 1)
        cal.set(java.util.Calendar.HOUR_OF_DAY, 5)
        cal.set(java.util.Calendar.MINUTE, 0)
        cal.set(java.util.Calendar.SECOND, 0)
        sp.edit().putInt("viewer_id", id).putString("viewer_name", name).putLong("viewer_until", cal.timeInMillis).apply()
    }

    val paired: Boolean
        get() = server.isNotEmpty() && token.isNotEmpty()

    fun clearPairing() {
        sp.edit().remove("token").apply()
    }
}

/** Tillstånd som bara gäller medan appen körs. */
object Session {
    /** Sätts första gången MainActivity startar i en ny process (då slås vuxenläget av). */
    var started = false

    /** Föregående kanal för knappen "tillbaka till förra kanalen". */
    var previousChannel: Pair<String, String>? = null
    var currentChannel: Pair<String, String>? = null

    /** Uppdateringskontroll görs en gång per start. */
    var updateChecked = false

    /** Inspelningsschemat hämtas när appen startar. */
    var recordingsSynced = false

    /** En film har setts klart: fråga "Vad tyckte ni?" när man kommer tillbaka från spelaren. */
    var pendingRating: Triple<String, String, String>? = null
}
