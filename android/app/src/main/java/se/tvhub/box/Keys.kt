package se.tvhub.box

import android.app.Activity
import android.app.AlertDialog
import android.content.Context
import android.graphics.Color
import android.os.Handler
import android.os.Looper
import android.view.Gravity
import android.view.KeyEvent
import android.view.View
import android.view.ViewGroup
import android.widget.FrameLayout
import android.widget.TextView
import org.json.JSONObject

/**
 * Fjärrkontrollens knappar: färgknappar, Info, Guide, Inspelning, Meny och siffror.
 *
 * Varje knapp har en funktion i menyerna och en under uppspelning. Standard nedan; under
 * Inställningar → Fjärrkontrollens knappar kan man lära in vilken knapp som helst och välja
 * funktion själv (t.ex. ★- och Grupper-knapparna, som skickar olika koder på olika fjärrar).
 */
object Keys {
    data class Action(val id: String, val label: String)

    val BROWSE = listOf(
        Action("none", "Ingenting"),
        Action("options", "Val för det markerade (som att hålla in OK)"),
        Action("favorite", "★ Favorit på/av för det markerade"),
        Action("groups", "Grupper (välj kategori)"),
        Action("night", "Filmkväll"),
        Action("search", "Sök"),
        Action("favorites", "Favoriter"),
        Action("new", "Nytt tillagt"),
        Action("info", "Info om det markerade"),
        Action("guide", "Programguide för markerad kanal"),
        Action("record", "Spela in det markerade"),
        Action("live", "Live-TV"),
        Action("home", "Hem"),
    )

    val PLAY = listOf(
        Action("none", "Ingenting"),
        Action("options", "Val i spelaren (ljud, undertexter …)"),
        Action("favorite", "★ Favorit på/av"),
        Action("groups", "Kanallista (eller avsnittslista)"),
        Action("record", "● Spela in programmet"),
        Action("prev_channel", "Förra kanalen"),
        Action("program_start", "Från programmets början"),
        Action("live", "Tillbaka till direkt"),
        Action("info", "Programinfo"),
        Action("guide", "Programguide"),
        Action("night", "Filmkväll"),
    )

    /** Standard: knapp -> (i menyerna, under uppspelning). */
    private val DEFAULTS: Map<Int, Pair<String, String>> = mapOf(
        KeyEvent.KEYCODE_MENU to ("options" to "options"),
        KeyEvent.KEYCODE_BOOKMARK to ("favorite" to "favorite"),
        KeyEvent.KEYCODE_PROG_RED to ("night" to "record"),
        KeyEvent.KEYCODE_PROG_GREEN to ("search" to "prev_channel"),
        KeyEvent.KEYCODE_PROG_YELLOW to ("favorites" to "program_start"),
        KeyEvent.KEYCODE_PROG_BLUE to ("new" to "live"),
        KeyEvent.KEYCODE_INFO to ("info" to "info"),
        KeyEvent.KEYCODE_GUIDE to ("guide" to "guide"),
        KeyEvent.KEYCODE_MEDIA_RECORD to ("record" to "record"),
    )

    /** Knappar som redan används till navigering och uppspelning: kan inte läras in. */
    private val RESERVED = setOf(
        KeyEvent.KEYCODE_DPAD_UP, KeyEvent.KEYCODE_DPAD_DOWN, KeyEvent.KEYCODE_DPAD_LEFT, KeyEvent.KEYCODE_DPAD_RIGHT,
        KeyEvent.KEYCODE_DPAD_CENTER, KeyEvent.KEYCODE_ENTER, KeyEvent.KEYCODE_NUMPAD_ENTER, KeyEvent.KEYCODE_BACK,
        KeyEvent.KEYCODE_HOME, KeyEvent.KEYCODE_POWER, KeyEvent.KEYCODE_VOLUME_UP, KeyEvent.KEYCODE_VOLUME_DOWN,
        KeyEvent.KEYCODE_VOLUME_MUTE, KeyEvent.KEYCODE_CHANNEL_UP, KeyEvent.KEYCODE_CHANNEL_DOWN,
        KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE, KeyEvent.KEYCODE_MEDIA_PLAY, KeyEvent.KEYCODE_MEDIA_PAUSE,
        KeyEvent.KEYCODE_MEDIA_FAST_FORWARD, KeyEvent.KEYCODE_MEDIA_REWIND, KeyEvent.KEYCODE_MEDIA_STOP,
        KeyEvent.KEYCODE_SEARCH, KeyEvent.KEYCODE_VOICE_ASSIST, KeyEvent.KEYCODE_ASSIST,
    ) + (KeyEvent.KEYCODE_0..KeyEvent.KEYCODE_9)

    private val NAMES = mapOf(
        KeyEvent.KEYCODE_MENU to "☰ Meny",
        KeyEvent.KEYCODE_BOOKMARK to "★ Favorit",
        KeyEvent.KEYCODE_PROG_RED to "Röd",
        KeyEvent.KEYCODE_PROG_GREEN to "Grön",
        KeyEvent.KEYCODE_PROG_YELLOW to "Gul",
        KeyEvent.KEYCODE_PROG_BLUE to "Blå",
        KeyEvent.KEYCODE_INFO to "Info",
        KeyEvent.KEYCODE_GUIDE to "Guide",
        KeyEvent.KEYCODE_MEDIA_RECORD to "● Inspelning",
    )

    fun name(code: Int): String = NAMES[code]
        ?: KeyEvent.keyCodeToString(code).removePrefix("KEYCODE_").lowercase().replace('_', ' ')
            .replaceFirstChar { it.uppercase() }.ifEmpty { "Knapp $code" }

    private fun custom(prefs: Prefs): JSONObject = try {
        JSONObject(prefs.keyMap.ifEmpty { "{}" })
    } catch (_: Exception) {
        JSONObject()
    }

    private fun mapping(prefs: Prefs, code: Int): Pair<String, String>? {
        custom(prefs).optJSONObject(code.toString())?.let { return it.optString("b", "none") to it.optString("p", "none") }
        return DEFAULTS[code]
    }

    /** Funktionen i menyerna för knappen, eller null om knappen inte har någon. */
    fun browseAction(prefs: Prefs, code: Int): String? = mapping(prefs, code)?.first?.takeIf { it != "none" }

    /** Funktionen under uppspelning för knappen, eller null. */
    fun playAction(prefs: Prefs, code: Int): String? = mapping(prefs, code)?.second?.takeIf { it != "none" }

    private fun label(list: List<Action>, id: String) = list.firstOrNull { it.id == id }?.label ?: "Ingenting"

    // ---------------------------------------------------------------- inställningen

    fun showSettings(activity: Activity) {
        val prefs = Prefs(activity)
        val saved = custom(prefs)
        val codes = LinkedHashSet<Int>()
        codes.addAll(DEFAULTS.keys)
        saved.keys().forEach { k -> k.toIntOrNull()?.let { codes.add(it) } }
        val list = codes.toList()
        val labels = ArrayList<String>()
        labels.add("➕ Lär in en knapp…")
        for (code in list) {
            val (b, p) = mapping(prefs, code) ?: ("none" to "none")
            labels.add("${name(code)}:  ${short(BROWSE, b)}  /  ${short(PLAY, p)}")
        }
        labels.add("Återställ standard")
        Look.choose(activity, "Fjärrkontrollens knappar", labels,
            subtitle = if (!prefs.modern) null else "Varje knapp: funktion i menyerna / under uppspelning. " +
                "Siffror väljer kanal (samma nummer som i kanallistan). Skickar en knapp på din fjärr en annan kod, lär in den här.") { which ->
            when {
                which == 0 -> learn(activity)
                which == labels.size - 1 -> Look.confirm(activity, "Återställa knapparna?", "Alla egna val tas bort.", "Återställ") {
                    prefs.keyMap = ""
                    showSettings(activity)
                }
                else -> assign(activity, list[which - 1])
            }
        }
    }

    private fun short(list: List<Action>, id: String): String =
        if (id == "none") "–" else label(list, id).substringBefore(" (").removePrefix("★ ").removePrefix("● ")

    private fun learn(activity: Activity) {
        val dialog = AlertDialog.Builder(activity)
            .setTitle("Lär in en knapp")
            .setMessage("Tryck på knappen på fjärrkontrollen.\n\nTillbaka avbryter.")
            .create()
        dialog.setOnKeyListener { d, code, event ->
            if (event.action != KeyEvent.ACTION_DOWN) return@setOnKeyListener true
            if (code == KeyEvent.KEYCODE_BACK) {
                d.dismiss()
                showSettings(activity)
                return@setOnKeyListener true
            }
            d.dismiss()
            if (code in RESERVED) {
                Look.message(activity, name(code), "Den knappen används redan till att styra (pilar, OK, siffror, volym, uppspelning). Välj en annan.")
            } else {
                assign(activity, code)
            }
            true
        }
        dialog.show()
    }

    private fun assign(activity: Activity, code: Int) {
        val prefs = Prefs(activity)
        val (b, p) = mapping(prefs, code) ?: ("none" to "none")
        Look.choose(activity, "${name(code)}: i menyerna", BROWSE.map { (if (it.id == b) "✓ " else "") + it.label }) { i ->
            val browse = BROWSE[i].id
            Look.choose(activity, "${name(code)}: under uppspelning", PLAY.map { (if (it.id == p) "✓ " else "") + it.label }) { j ->
                val play = PLAY[j].id
                val map = custom(prefs)
                map.put(code.toString(), JSONObject().put("b", browse).put("p", play))
                prefs.keyMap = map.toString()
                showSettings(activity)
            }
        }
    }
}

/**
 * Sifferknapparna: visar siffrorna uppe till höger och väljer kanal när man slutat trycka
 * (eller direkt vid fyra siffror).
 */
class DigitEntry(private val activity: Activity, private val onDone: (String) -> Unit) {
    private val handler = Handler(Looper.getMainLooper())
    private var digits = ""
    private var view: TextView? = null
    private val finish = Runnable { done() }

    /** true om knappen var en siffra (och har hanterats). */
    fun onKey(code: Int): Boolean {
        val digit = when (code) {
            in KeyEvent.KEYCODE_0..KeyEvent.KEYCODE_9 -> code - KeyEvent.KEYCODE_0
            in KeyEvent.KEYCODE_NUMPAD_0..KeyEvent.KEYCODE_NUMPAD_9 -> code - KeyEvent.KEYCODE_NUMPAD_0
            else -> return false
        }
        digits += digit
        show()
        handler.removeCallbacks(finish)
        if (digits.length >= 5) done() else handler.postDelayed(finish, 1_600)
        return true
    }

    fun active(): Boolean = digits.isNotEmpty()

    private fun show() {
        val v = view ?: TextView(activity).also { tv ->
            tv.setTextColor(Color.WHITE)
            tv.textSize = 34f
            tv.setBackgroundColor(Color.parseColor("#CC101418"))
            val pad = Look.dp(activity, 18)
            tv.setPadding(pad, pad / 2, pad, pad / 2)
            val root = activity.findViewById<ViewGroup>(android.R.id.content)
            val lp = FrameLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.TOP or Gravity.END)
            lp.topMargin = Look.dp(activity, 28)
            lp.rightMargin = Look.dp(activity, 40)
            root.addView(tv, lp)
            view = tv
        }
        v.text = digits
        v.visibility = View.VISIBLE
    }

    private fun done() {
        handler.removeCallbacks(finish)
        val text = digits
        digits = ""
        view?.visibility = View.GONE
        if (text.isNotEmpty()) onDone(text)
    }

    fun cancel() {
        handler.removeCallbacks(finish)
        digits = ""
        view?.visibility = View.GONE
    }

    companion object {
        /** Slår upp kanalen med numret hos TV Hub. */
        fun lookup(context: Context, num: String, done: (Item) -> Unit) {
            Api.obj("GET", "/device/channel-by-num?num=${Api.enc(num)}") { result ->
                result.onSuccess { done(Item.from(it)) }
                    .onFailure { android.widget.Toast.makeText(context, Api.friendly(it), android.widget.Toast.LENGTH_SHORT).show() }
            }
        }
    }
}
