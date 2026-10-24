package se.tvhub.box

import android.content.Context

/** Sparade inställningar: serveradress, boxens nyckel, dolt läge och val av ljud/undertexter. */
class Prefs(context: Context) {
    val appContext: Context = context.applicationContext
    private val sp = appContext.getSharedPreferences("tvhub", Context.MODE_PRIVATE)

    companion object {
        /** Vuxenläget stängs av efter så här lång tid utan knapptryck (om boxen inte har fått en annan tid). */
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
     * Dolt läge: visar bara vuxeninnehåll. Växlas med en dold gest (se interna anteckningar).
     * Stängs av automatiskt efter inaktivitet (15 minuter, ställs in per box i webbadminen), efter högst 3 timmar
     * och när appen startas om (se MainActivity).
     */
    var adultMode: Boolean
        get() {
            if (!sp.getBoolean("adult_mode", false)) return false
            val now = System.currentTimeMillis()
            val idle = now - sp.getLong("adult_last_active", 0L)
            val age = now - sp.getLong("adult_since", 0L)
            if (idle > adultIdleMs || age > ADULT_MAX_MS) {
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
                .also { if (!value) it.remove("adult_unlock") }
                .apply()
        }

    /**
     * Vuxenläget: visa favoriter, Fortsätt titta och sedda (dolt val, se interna anteckningar).
     * Tittandet sparas alltid; det här styr bara vad som visas.
     */
    var adultHistory: Boolean
        get() = sp.getBoolean("adult_history", false)
        set(value) {
            sp.edit().putBoolean("adult_history", value).apply()
        }

    /** Anropas vid knapptryck och under uppspelning så att vuxenläget inte stängs av mitt i. */
    fun touch() {
        val now = System.currentTimeMillis()
        if (sp.getBoolean("adult_mode", false)) {
            sp.edit().putLong("adult_last_active", now).apply()
        }
        // Profilen: senaste användning (för återgång till startprofilen). Skrivs högst en gång i minuten.
        if (now - sp.getLong("profile_active", 0L) > 60_000L) sp.edit().putLong("profile_active", now).apply()
    }

    /** TV-guiden: senast valda grupp ("fav", "recent" eller "cat:<id>"). */
    var guideGroup: String
        get() = sp.getString("guide_group", "fav") ?: "fav"
        set(value) {
            sp.edit().putString("guide_group", value).apply()
        }

    // ---------------------------------------------------------------- profiler

    /** Vald profil på den här enheten. 0 = boxens startprofil (bestäms i webbadminen). */
    var profileId: Int
        get() = sp.getInt("profile_id", 0)
        set(value) {
            sp.edit().putInt("profile_id", value).putLong("profile_active", System.currentTimeMillis()).apply()
        }

    /** Senaste användning, för återgång till startprofilen. */
    val profileActiveAt: Long
        get() = sp.getLong("profile_active", 0L)

    /** Den profil som TV Hub senast sa var aktiv (namn, färg …), för menyn. */
    var shownProfile: String
        get() = sp.getString("profile_json", "") ?: ""
        set(value) {
            sp.edit().putString("profile_json", value).apply()
        }

    var boxDefaultProfile: Int
        get() = sp.getInt("box_default_profile", 0)
        set(value) {
            sp.edit().putInt("box_default_profile", value).apply()
        }

    /** Minuter utan knapptryck innan vuxenläget stängs (webbadminen, per box). 1–180, annars 15. */
    var boxAdultIdleMinutes: Int
        get() = sp.getInt("box_adult_idle_min", 15).takeIf { it in 1..180 } ?: 15
        set(value) {
            sp.edit().putInt("box_adult_idle_min", if (value in 1..180) value else 15).apply()
        }

    private val adultIdleMs: Long get() = boxAdultIdleMinutes * 60_000L

    var boxRevertHours: Int
        get() = sp.getInt("box_revert_hours", 0)
        set(value) {
            sp.edit().putInt("box_revert_hours", value).apply()
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
    /** Mina media: visa storlek och längd vid titeln. */
    var mediaMeta: Boolean
        get() = sp.getBoolean("media_meta", false)
        set(value) {
            sp.edit().putBoolean("media_meta", value).apply()
        }

    /** TV: visningsläge per sida ("vod", "series", "favorites", "new"), se [ViewStyle]. */
    fun viewStyle(page: String): Int = sp.getInt("view_$page", ViewStyle.TITLE)

    fun setViewStyle(page: String, style: Int) {
        sp.edit().putInt("view_$page", style).apply()
    }

    /** TV: starta på senaste kanalen när appen öppnas. */
    var startOnLastChannel: Boolean
        get() = sp.getBoolean("start_last_channel", false)
        set(value) {
            sp.edit().putBoolean("start_last_channel", value).apply()
        }

    /** Senast sedda kanal (id, namn, logga), för "Starta på senaste kanalen". */
    var lastChannel: Triple<String, String, String?>?
        get() {
            val id = sp.getString("last_channel_id", "") ?: ""
            if (id.isEmpty()) return null
            return Triple(id, sp.getString("last_channel_name", "") ?: "", sp.getString("last_channel_icon", null))
        }
        set(value) {
            sp.edit().putString("last_channel_id", value?.first ?: "").putString("last_channel_name", value?.second ?: "")
                .putString("last_channel_icon", value?.third).apply()
        }

    /** Kanallistan som den senaste kanalen spelades från (för att kunna zappa efter start). */
    var lastChannelQueue: String
        get() = sp.getString("last_channel_queue", "") ?: ""
        set(value) {
            sp.edit().putString("last_channel_queue", value).apply()
        }

    /** Upplåsning av vuxenläget när PIN-kod används (skickas som X-TVHub-Unlock). */
    var adultUnlock: String
        get() = sp.getString("adult_unlock", "") ?: ""
        set(value) {
            sp.edit().putString("adult_unlock", value).apply()
        }

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

    /** Filmkväll: vem som tittar = vald profil (tittarlägena är profilerna). */
    val activeViewer: Int?
        get() = profileId.takeIf { it > 0 } ?: boxDefaultProfile.takeIf { it > 0 }

    val activeViewerName: String
        get() = Profiles.current(appContext)?.name ?: ""

    val paired: Boolean
        get() = server.isNotEmpty() && token.isNotEmpty()

    fun clearPairing() {
        // Också nyckeln till Home Assistant (fjärråtkomst), inte bara boxnyckeln.
        sp.edit().remove("token").remove("remote_url").remove("remote_ingress").remove("remote_ha_token").apply()
    }
}

/** Tillstånd som bara gäller medan appen körs. */
object Session {
    /** Sätts första gången MainActivity startar i en ny process (då slås vuxenläget av). */
    var started = false

    /** Föregående kanal för knappen "tillbaka till förra kanalen". */
    var previousChannel: Pair<String, String>? = null
    var currentChannel: Pair<String, String>? = null

    /** "Starta på senaste kanalen" görs bara när appen startar. */
    var lastChannelStarted = false

    /** Uppdateringskontroll görs en gång per start. */
    var updateChecked = false

    /** Inspelningsschemat hämtas när appen startar. */
    var recordingsSynced = false

    /** En film har setts klart: fråga "Vad tyckte ni?" när man kommer tillbaka från spelaren. */
    var pendingRating: Triple<String, String, String>? = null
}
