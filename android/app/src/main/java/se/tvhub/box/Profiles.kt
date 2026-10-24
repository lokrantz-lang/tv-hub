package se.tvhub.box

import android.app.Activity
import android.app.AlertDialog
import android.app.Dialog
import android.content.Context
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.text.InputType
import android.view.Gravity
import android.view.KeyEvent
import android.view.View
import android.view.ViewGroup
import android.view.Window
import android.view.WindowManager
import android.widget.EditText
import android.widget.HorizontalScrollView
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import org.json.JSONArray
import org.json.JSONObject

/**
 * Profiler (en per person, Tillsammans, Gäst …). Läggs till och ändras i webbadminen under Profiler.
 *
 *  - Vald profil skickas med i alla anrop (X-TVHub-Profile). Utan vald profil gäller boxens startprofil.
 *  - Varje profil har egna favoriter, Fortsätt titta och snabbval (Mina program).
 *  - Profil med kod: koden frågas när man byter till den. Gäst sparar ingenting.
 *  - Boxen kan gå tillbaka till startprofilen efter några timmar utan användning (webbadminen, Boxar).
 *  - Vid varje byte stängs vuxenläget av.
 */
object Profiles {
    data class Profile(
        val id: Int,
        val name: String,
        val color: Int,
        val emoji: String,
        val hasPin: Boolean,
        val allowAdult: Boolean,
        val guest: Boolean,
        val json: JSONObject,
    ) {
        /** Bokstaven eller emojin i den runda bilden. */
        val badge: String
            get() = emoji.ifEmpty { name.trim().take(1).uppercase().ifEmpty { "?" } }
    }

    /** Profilerna från senaste hemdatan. */
    var all: List<Profile> = emptyList()
        private set

    fun parse(o: JSONObject?): Profile? {
        if (o == null) return null
        val id = o.optInt("id")
        if (id <= 0) return null
        val color = try {
            Color.parseColor(o.optString("color").ifEmpty { "#6B7280" })
        } catch (_: Exception) {
            0xFF6B7280.toInt()
        }
        val emoji = o.optString("emoji").takeIf { it != "null" } ?: ""
        return Profile(id, o.optString("name"), color, emoji, o.optBoolean("has_pin"), o.optBoolean("allow_adult"),
            o.optBoolean("guest"), o)
    }

    fun parseList(arr: JSONArray?): List<Profile> {
        if (arr == null) return emptyList()
        return (0 until arr.length()).mapNotNull { parse(arr.optJSONObject(it)) }
    }

    /** Den profil som är aktiv (enligt TV Hub senast). */
    fun current(context: Context): Profile? = try {
        Prefs(context).shownProfile.takeIf { it.isNotEmpty() }?.let { parse(JSONObject(it)) }
    } catch (_: Exception) {
        null
    }

    /** Ur hemdatan: aktiv profil, alla profiler och boxens inställningar. */
    fun onHome(context: Context, data: JSONObject) {
        val prefs = Prefs(context)
        val list = parseList(data.optJSONArray("profiles"))
        if (list.isNotEmpty()) all = list
        data.optJSONObject("box")?.let {
            prefs.boxDefaultProfile = it.optInt("default_profile_id")
            prefs.boxRevertHours = it.optInt("revert_hours")
            prefs.boxAdultIdleMinutes = it.optInt("adult_idle_minutes", 15)
        }
        // Profilen togs bort i webbadminen: tillbaka till startprofilen.
        if (prefs.profileId > 0 && list.isNotEmpty() && list.none { it.id == prefs.profileId }) prefs.profileId = 0
        data.optJSONObject("active_profile")?.let { prefs.shownProfile = it.toString() }
    }

    /**
     * Har boxen stått oanvänd längre än webbadminens "Tillbaka till startprofilen"? Då byts profilen
     * tillbaka (t.ex. till Tillsammans i vardagsrummet). Returnerar true om den byttes.
     */
    fun checkRevert(context: Context): Boolean {
        val prefs = Prefs(context)
        val hours = prefs.boxRevertHours
        val id = prefs.profileId
        if (hours <= 0 || id <= 0 || id == prefs.boxDefaultProfile) return false
        if (System.currentTimeMillis() - prefs.profileActiveAt < hours * 3_600_000L) return false
        prefs.profileId = 0
        MyPrograms.clear()
        all.firstOrNull { it.id == prefs.boxDefaultProfile }?.let { prefs.shownProfile = it.json.toString() }
        if (prefs.adultMode) prefs.adultMode = false
        return true
    }

    // ---------------------------------------------------------------- byta profil

    /** "Vem tittar?": runda bilder i en rad (TV) eller en lista (telefon). */
    fun choose(activity: Activity, onChanged: () -> Unit) {
        if (all.isEmpty()) {
            Api.obj("GET", "/device/profiles") { result ->
                if (activity.isFinishing) return@obj
                result.onSuccess { data ->
                    all = parseList(data.optJSONArray("profiles"))
                    if (all.isEmpty()) Toast.makeText(activity, "Inga profiler. Lägg till dem i webbadminen under Profiler.", Toast.LENGTH_LONG).show()
                    else choose(activity, onChanged)
                }.onFailure { Toast.makeText(activity, Api.friendly(it), Toast.LENGTH_LONG).show() }
            }
            return
        }
        val active = current(activity)?.id
        if (FormFactor.isMobile(activity) || !Prefs(activity).modern) {
            Look.choose(activity, "Vem tittar?", all.map { (if (it.hasPin) "🔒 " else "") + it.name },
                checked = all.indexOfFirst { it.id == active }) { which -> switchTo(activity, all[which], onChanged) }
            return
        }
        showPicker(activity, active, onChanged)
    }

    private fun showPicker(activity: Activity, active: Int?, onChanged: () -> Unit) {
        val dialog = Dialog(activity)
        dialog.requestWindowFeature(Window.FEATURE_NO_TITLE)
        val dp = { v: Int -> Look.dp(activity, v) }
        val root = LinearLayout(activity)
        root.orientation = LinearLayout.VERTICAL
        root.gravity = Gravity.CENTER
        root.setBackgroundColor(0xF20A0A0C.toInt())
        val title = TextView(activity)
        title.text = "Vem tittar?"
        title.setTextColor(Color.WHITE)
        title.textSize = 34f
        title.typeface = Typeface.create(Typeface.DEFAULT, Typeface.BOLD)
        title.gravity = Gravity.CENTER
        root.addView(title, LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT))
        val scroll = HorizontalScrollView(activity)
        scroll.isHorizontalScrollBarEnabled = false
        scroll.clipChildren = false
        val row = LinearLayout(activity)
        row.orientation = LinearLayout.HORIZONTAL
        row.gravity = Gravity.CENTER
        row.setPadding(dp(40), dp(36), dp(40), dp(24))
        row.clipChildren = false
        var focusTarget: View? = null
        for (p in all) {
            val tile = LinearLayout(activity)
            tile.orientation = LinearLayout.VERTICAL
            tile.gravity = Gravity.CENTER_HORIZONTAL
            tile.isFocusable = true
            tile.isClickable = true
            tile.setPadding(dp(14), dp(10), dp(14), dp(10))
            val circle = TextView(activity)
            circle.gravity = Gravity.CENTER
            circle.setTextColor(Color.WHITE)
            circle.textSize = if (p.emoji.isNotEmpty()) 44f else 40f
            circle.typeface = Typeface.create(Typeface.DEFAULT, Typeface.BOLD)
            circle.text = p.badge
            val ring = GradientDrawable()
            ring.shape = GradientDrawable.OVAL
            ring.setColor(p.color)
            circle.background = ring
            tile.addView(circle, LinearLayout.LayoutParams(dp(112), dp(112)))
            val name = TextView(activity)
            name.text = p.name + if (p.hasPin) "  🔒" else ""
            name.textSize = 20f
            name.setTextColor(0xFFA1A1AA.toInt())
            name.gravity = Gravity.CENTER
            name.setPadding(0, dp(12), 0, 0)
            tile.addView(name)
            val sub = TextView(activity)
            sub.textSize = 14f
            sub.setTextColor(0xFF6E6E78.toInt())
            sub.gravity = Gravity.CENTER
            sub.text = when {
                p.id == active -> "Tittar nu"
                p.guest -> "Sparar inget"
                else -> ""
            }
            tile.addView(sub)
            tile.setOnFocusChangeListener { v, has ->
                ring.setStroke(if (has) dp(4) else 0, Color.WHITE)
                name.setTextColor(if (has) Color.WHITE else 0xFFA1A1AA.toInt())
                if (!Look.lite) v.animate().scaleX(if (has) 1.1f else 1f).scaleY(if (has) 1.1f else 1f).setDuration(150).start()
            }
            tile.setOnClickListener {
                dialog.dismiss()
                if (p.id != active) switchTo(activity, p, onChanged)
            }
            row.addView(tile)
            if (focusTarget == null || p.id == active) focusTarget = tile
        }
        scroll.addView(row)
        root.addView(scroll, LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT))
        val hint = TextView(activity)
        hint.text = "Profilerna läggs till och ändras i webbadminen under Profiler."
        hint.textSize = 14f
        hint.setTextColor(0xFF6E6E78.toInt())
        hint.gravity = Gravity.CENTER
        root.addView(hint)
        dialog.setContentView(root)
        dialog.window?.let { w ->
            w.setLayout(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT)
            w.setBackgroundDrawableResource(android.R.color.transparent)
            w.addFlags(WindowManager.LayoutParams.FLAG_DIM_BEHIND)
            w.setDimAmount(0.6f)
        }
        dialog.setOnKeyListener { d, keyCode, event ->
            if (keyCode == KeyEvent.KEYCODE_BACK && event.action == KeyEvent.ACTION_UP) {
                d.dismiss()
                true
            } else {
                false
            }
        }
        dialog.show()
        focusTarget?.requestFocus()
    }

    /** Byt till profilen (frågar efter koden om profilen har en). */
    fun switchTo(activity: Activity, p: Profile, onChanged: () -> Unit) {
        if (!p.hasPin) {
            apply(activity, p, onChanged)
            return
        }
        if (activity.isFinishing) return
        val input = EditText(activity)
        input.inputType = InputType.TYPE_CLASS_NUMBER or InputType.TYPE_NUMBER_VARIATION_PASSWORD
        input.hint = "Kod"
        val pad = Look.dp(activity, 20)
        input.setPadding(pad, pad / 2, pad, pad / 2)
        AlertDialog.Builder(activity)
            .setTitle("Kod för ${p.name}")
            .setView(input)
            .setPositiveButton("OK") { _, _ ->
                val pin = input.text.toString().trim()
                if (pin.isEmpty()) return@setPositiveButton
                Api.obj("POST", "/device/profile-check", JSONObject().put("profile_id", p.id).put("pin", pin)) { result ->
                    if (activity.isFinishing) return@obj
                    result.onSuccess { apply(activity, p, onChanged) }
                        .onFailure { Toast.makeText(activity, Api.friendly(it), Toast.LENGTH_LONG).show() }
                }
            }
            .setNegativeButton("Avbryt", null)
            .show()
        input.requestFocus()
    }

    private fun apply(activity: Activity, p: Profile, onChanged: () -> Unit) {
        val prefs = Prefs(activity)
        if (prefs.adultMode) {
            prefs.adultMode = false
            Api.fire("/device/adult-lock", JSONObject())
        }
        prefs.profileId = p.id
        prefs.shownProfile = p.json.toString()
        MyPrograms.clear()
        Toast.makeText(activity, if (p.guest) "Gäst: inget sparas" else "Hej ${p.name}!", Toast.LENGTH_SHORT).show()
        onChanged()
    }

    /** Rund bild med profilens färg och bokstav/emoji. */
    fun bindAvatar(view: TextView, p: Profile?) {
        val bg = GradientDrawable()
        bg.shape = GradientDrawable.OVAL
        bg.setColor(p?.color ?: 0xFF3A3A42.toInt())
        view.background = bg
        view.text = p?.badge ?: "?"
        view.setTextColor(Color.WHITE)
        view.gravity = Gravity.CENTER
        view.typeface = Typeface.create(Typeface.DEFAULT, Typeface.BOLD)
    }
}

/**
 * Mina program: snabbval per profil (Rapport, TV4 Nyheterna, Robinson …), läggs till i webbadminen.
 * TV Hub räknar ut vad som ska spelas (direkt, senaste sändningen eller nästa avsnitt ur arkivet).
 */
object MyPrograms {
    data class Program(val id: Int, val title: String, val subtitle: String, val icon: String?, val command: JSONObject?, val error: String?)

    /** Senaste svaret (null = inte hämtat för den här profilen än). */
    var cached: List<Program>? = null
        private set

    fun clear() {
        cached = null
    }

    fun parse(arr: JSONArray?): List<Program> {
        if (arr == null) return emptyList()
        return (0 until arr.length()).mapNotNull { i ->
            val o = arr.optJSONObject(i) ?: return@mapNotNull null
            Program(o.optInt("id"), o.optString("title"), o.optString("subtitle").takeIf { it != "null" } ?: "",
                o.optString("icon").takeIf { it.isNotEmpty() && it != "null" }, o.optJSONObject("command"),
                o.optString("error").takeIf { it.isNotEmpty() && it != "null" })
        }
    }

    /** Hämtar snabbvalen. done anropas bara om de ändrats sedan förra gången. */
    fun load(changed: (List<Program>) -> Unit) {
        Api.obj("GET", "/device/my-programs") { result ->
            result.onSuccess { data ->
                val list = parse(data.optJSONArray("programs"))
                if (list != cached) {
                    cached = list
                    changed(list)
                }
            }
        }
    }

    fun card(p: Program): Card = Card(
        kind = Card.CHANNEL,
        key = "myprog:${p.id}",
        title = p.title,
        subtitle = p.subtitle.ifEmpty { null },
        image = p.icon,
        payload = p,
        hero = Hero("Mina program", p.title, p.subtitle.ifEmpty { null }, if (p.command == null) null else "OK: spela", null),
    )

    fun play(activity: Activity, p: Program) {
        val cmd = p.command
        if (cmd == null) {
            Look.message(activity, p.title, p.error ?: "Går inte att spela just nu.")
            return
        }
        Control.execute(activity, cmd)
    }
}
