package se.tvhub.box

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.graphics.Typeface
import android.os.Bundle
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.view.ViewGroup
import android.widget.HorizontalScrollView
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import org.json.JSONArray
import org.json.JSONObject

/**
 * Världscupen i hoppning: planering (nästa etapper), pågående sändningar och historik, med stora knappar:
 * Titta, Spela in (kommande) och Ladda ner (redan sänt, ur kanalens arkiv). Allt kommer från /device/worldcup,
 * som läser din egen programguide, så det som visas finns på riktigt i din kanallista.
 * Inspelning och nerladdning görs på boxen (telefonen visar bara Titta).
 */
class WorldCupActivity : Activity() {
    companion object {
        fun start(context: Context) {
            context.startActivity(Intent(context, WorldCupActivity::class.java))
        }
    }

    private lateinit var prefs: Prefs
    private lateinit var content: LinearLayout
    private lateinit var scroll: ScrollView
    private var mobile = false
    private var firstButton: View? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        prefs = Prefs(this)
        Api.init(prefs)
        Look.init(this)
        FormFactor.portraitIfMobile(this)
        Fonts.install(this)
        mobile = FormFactor.isMobile(this)
        scroll = ScrollView(this).apply {
            setBackgroundColor(getColor(R.color.bg))
            isFillViewport = true
        }
        content = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            val side = Look.dp(this@WorldCupActivity, if (mobile) 16 else 56)
            setPadding(side, Look.dp(this@WorldCupActivity, if (mobile) 20 else 28), side, Look.dp(this@WorldCupActivity, 32))
        }
        scroll.addView(content, ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))
        setContentView(scroll)
        load()
    }

    override fun dispatchTouchEvent(ev: MotionEvent): Boolean {
        if (ev.action == MotionEvent.ACTION_DOWN) prefs.touch()
        return super.dispatchTouchEvent(ev)
    }

    override fun onResume() {
        super.onResume()
        if (content.childCount > 0) load()
    }

    private fun text(s: String, sp: Float, color: Int = getColor(R.color.text), bold: Boolean = false): TextView =
        TextView(this).apply {
            text = s
            textSize = sp
            setTextColor(color)
            if (bold) setTypeface(typeface, Typeface.BOLD)
        }

    private fun gap(dp: Int): View = View(this).apply { layoutParams = LinearLayout.LayoutParams(1, Look.dp(this@WorldCupActivity, dp)) }

    private fun load() {
        content.removeAllViews()
        content.addView(text("🏇 Världscupen i hoppning", if (mobile) 24f else 30f, bold = true))
        content.addView(text("Hämtar…", 16f, getColor(R.color.muted)))
        content.addView(ProgressBar(this))
        Api.obj("GET", "/device/worldcup") { result ->
            if (isFinishing) return@obj
            result.onSuccess { render(it) }.onFailure {
                content.removeAllViews()
                content.addView(text("🏇 Världscupen i hoppning", if (mobile) 24f else 30f, bold = true))
                content.addView(text("Kunde inte hämta: ${Api.friendly(it)}", 16f, getColor(R.color.bad)))
                content.addView(button("Försök igen", primary = true) { load() })
                firstButton = content.getChildAt(content.childCount - 1)
                focusFirst()
            }
        }
    }

    private fun focusFirst() {
        if (!mobile) firstButton?.requestFocus()
    }

    private var data: JSONObject? = null
    private var selectedLeg: String? = null
    private var detail: LinearLayout? = null
    private val tiles = HashMap<String, View>()

    private fun str(o: JSONObject?, key: String): String? = o?.optString(key)?.takeIf { it.isNotEmpty() && it != "null" }

    private fun render(d: JSONObject) {
        data = d
        content.removeAllViews()
        firstButton = null
        tiles.clear()
        val head = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.BOTTOM }
        head.addView(text("🏇 Världscupen i hoppning", if (mobile) 22f else 28f, bold = true))
        head.addView(text("   Säsongen ${d.optString("season")}", 15f, getColor(R.color.muted)))
        content.addView(head)
        val hints = d.optJSONArray("hints")
        if (hints != null && hints.length() > 0) {
            content.addView(gap(6))
            for (i in 0 until hints.length()) content.addView(text("⚠ ${hints.optString(i)}", 14f, getColor(R.color.star)))
        }
        val legs = d.optJSONArray("legs") ?: JSONArray()
        if (legs.length() == 0) {
            content.addView(gap(16))
            content.addView(text("Inget att visa här.", 16f, getColor(R.color.muted)))
            return
        }
        val hero = d.optJSONObject("hero")
        content.addView(gap(14))
        content.addView(heroView(hero, d.optJSONArray("channels")))

        // Säsongen: alla etapper i en rad. Vald etapp visas under.
        content.addView(gap(20))
        content.addView(text("SÄSONGEN", 13f, getColor(R.color.muted), true))
        val strip = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        val hs = HorizontalScrollView(this).apply { isHorizontalScrollBarEnabled = false; clipToPadding = false }
        hs.addView(strip)
        content.addView(hs)
        val legList = (0 until legs.length()).map { legs.getJSONObject(it) }
        val next = d.optString("next")
        val start = selectedLeg?.takeIf { id -> legList.any { it.optString("id") == id } }
            ?: str(hero, "leg") ?: legList.firstOrNull { it.optString("state") == "ongoing" }?.optString("id")
            ?: next.takeIf { it.isNotEmpty() && it != "null" } ?: legList.last().optString("id")
        for (leg in legList) strip.addView(tile(leg, leg.optString("id") == next))

        detail = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        content.addView(detail)
        select(start, scrollTo = true)

        str(d, "status_line")?.let {
            content.addView(gap(22))
            content.addView(text("ⓘ $it", 13f, getColor(R.color.muted)))
        }
        focusFirst()
    }

    private fun heroView(hero: JSONObject?, chans: JSONArray?): View {
        val box = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundResource(R.drawable.m_wc_hero)
            val p = Look.dp(this@WorldCupActivity, if (mobile) 16 else 24)
            setPadding(p, p, p, p)
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
        }
        if (hero == null) {
            box.addView(text("Säsongen är slut", if (mobile) 22f else 30f, bold = true))
        } else {
            val live = hero.optBoolean("live")
            val kicker = if (live) "● SÄNDS NU" else (str(hero, "countdown") ?: "NÄSTA").uppercase()
            box.addView(text(kicker, 14f, getColor(if (live) R.color.m_live else R.color.accent), true))
            box.addView(text("${hero.optString("leg_name")}  ${flag(hero.optString("country"))}", if (mobile) 28f else 40f, bold = true))
            val ev = hero.optJSONObject("event")
            if (ev != null) {
                val e = Sports.parseEvents(JSONArray().put(ev)).first()
                box.addView(text(e.title, if (mobile) 16f else 19f))
                box.addView(text("${e.whenText} · ${e.channel}", if (mobile) 15f else 17f, getColor(R.color.muted)))
                if (ev.optBoolean("unconfirmed")) box.addView(text("Tid enligt TV4:s tablå", 13f, getColor(R.color.star)))
                box.addView(gap(10))
                box.addView(actions(e, ev))
            } else {
                box.addView(text("Tiderna kommer när TV4 publicerar dem.", 16f, getColor(R.color.muted)))
            }
        }
        if (chans != null && chans.length() > 0) {
            val row = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL }
            row.addView(text("Kanaler  ", 14f, getColor(R.color.muted)))
            for (i in 0 until chans.length()) {
                val c = chans.getJSONObject(i)
                val cmd = c.optJSONObject("command") ?: continue
                row.addView(button("▶ ${c.optString("label")}", primary = false, small = true) { Control.execute(this, cmd) })
            }
            box.addView(gap(6))
            box.addView(row)
        }
        return box
    }

    private fun flag(country: String): String = when (country.uppercase()) {
        "ESP" -> "🇪🇸"; "ITA" -> "🇮🇹"; "GER" -> "🇩🇪"; "FRA" -> "🇫🇷"; "NOR" -> "🇳🇴"; "SWE" -> "🇸🇪"; "SUI" -> "🇨🇭"
        "GBR" -> "🇬🇧"; "NED" -> "🇳🇱"; "BEL" -> "🇧🇪"; "AUT" -> "🇦🇹"; "USA" -> "🇺🇸"; "IRL" -> "🇮🇪"; "POL" -> "🇵🇱"
        "CZE" -> "🇨🇿"; "DEN" -> "🇩🇰"; "FIN" -> "🇫🇮"; "POR" -> "🇵🇹"; else -> ""
    }

    private fun tile(leg: JSONObject, isNext: Boolean): View {
        val id = leg.optString("id")
        val state = leg.optString("state")
        val box = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundResource(R.drawable.m_wc_tile)
            val p = Look.dp(this@WorldCupActivity, 12)
            setPadding(p, p, p, p)
            isFocusable = true
            isClickable = true
            layoutParams = LinearLayout.LayoutParams(Look.dp(this@WorldCupActivity, if (mobile) 132 else 168), ViewGroup.LayoutParams.WRAP_CONTENT)
                .apply { topMargin = Look.dp(this@WorldCupActivity, 8); bottomMargin = Look.dp(this@WorldCupActivity, 8); marginEnd = Look.dp(this@WorldCupActivity, 10) }
        }
        val on = resources.getColorStateList(R.color.m_on_focus, theme)
        val name = text("${flag(leg.optString("country"))} ${leg.optString("name")}", 16f, bold = true).apply { setTextColor(on); isDuplicateParentStateEnabled = true; maxLines = 1 }
        box.addView(name)
        box.addView(text(leg.optString("dates"), 13f).apply { setTextColor(on); isDuplicateParentStateEnabled = true })
        val winner = str(leg, "winner")
        val (label, color) = when {
            leg.optString("kind") == "final" && state != "done" -> "FINAL" to R.color.star
            state == "done" && winner != null -> "🏆 $winner" to R.color.star
            state == "done" -> "Avgjord" to R.color.muted
            state == "ongoing" -> "● Pågår" to R.color.m_live
            isNext -> "Nästa" to R.color.accent
            else -> (if (leg.optString("kind") == "other") "Ingen etapp" else "Kommande") to R.color.muted
        }
        box.addView(text(label, 13f, getColor(color), true).apply { maxLines = 1 })
        if (state == "done") box.alpha = 0.85f
        box.setOnClickListener { select(id, scrollTo = false, focusDetail = true) }
        box.setOnFocusChangeListener { v, has ->
            if (!Look.lite) v.animate().scaleX(if (has) 1.05f else 1f).scaleY(if (has) 1.05f else 1f).setDuration(150).start()
            if (has && !mobile) select(id, scrollTo = false)
        }
        tiles[id] = box
        return box
    }

    private fun select(id: String, scrollTo: Boolean, focusDetail: Boolean = false) {
        val d = data ?: return
        val legs = d.optJSONArray("legs") ?: return
        val leg = (0 until legs.length()).map { legs.getJSONObject(it) }.firstOrNull { it.optString("id") == id } ?: return
        if (selectedLeg == id && (detail?.childCount ?: 0) > 0 && !focusDetail) return
        selectedLeg = id
        for ((k, v) in tiles) v.isActivated = k == id
        if (scrollTo) tiles[id]?.let { t -> t.post { (t.parent?.parent as? HorizontalScrollView)?.smoothScrollTo((t.left - Look.dp(this, 40)).coerceAtLeast(0), 0) } }
        val box = detail ?: return
        box.removeAllViews()
        box.addView(legView(leg))
        if (focusDetail) box.post { firstFocusable(box)?.requestFocus() }
    }

    private fun firstFocusable(v: View): View? {
        if (v.isFocusable && v.visibility == View.VISIBLE) return v
        if (v is ViewGroup) for (i in 0 until v.childCount) firstFocusable(v.getChildAt(i))?.let { return it }
        return null
    }

    private fun legView(leg: JSONObject): View {
        val box = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundResource(R.drawable.m_card_bg)
            val p = Look.dp(this@WorldCupActivity, if (mobile) 14 else 20)
            setPadding(p, p, p, p)
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
                .apply { topMargin = Look.dp(this@WorldCupActivity, 6) }
        }
        val badge = when {
            leg.optString("kind") == "final" -> "  · FINAL"
            leg.optString("kind") == "other" -> "  · ingen etapp"
            leg.optBoolean("preliminary") -> "  · preliminärt datum"
            else -> ""
        }
        box.addView(text("${flag(leg.optString("country"))} ${leg.optString("name")}$badge", if (mobile) 20f else 24f, bold = true))
        box.addView(text("${leg.optString("dates")} · Världscupklassen ${leg.optString("wc_label")}", 15f, getColor(R.color.muted)))
        str(leg, "tv")?.let { box.addView(text("TV: $it", 15f, getColor(R.color.muted))) }
        str(leg, "winner")?.let { w ->
            box.addView(gap(6))
            box.addView(text("🏆 $w" + (str(leg, "horse")?.let { " på $it" } ?: ""), 18f, getColor(R.color.star), true))
        }
        val events = Sports.parseEvents(leg.optJSONArray("events"))
        val raw = leg.optJSONArray("events") ?: JSONArray()
        if (events.isEmpty()) {
            box.addView(gap(8))
            box.addView(text(leg.optString("headline"), 15f, getColor(R.color.muted)))
        }
        for (i in events.indices) {
            val e = events[i]
            val o = raw.getJSONObject(i)
            box.addView(gap(14))
            val row = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL }
            val badgeText = when (e.state) { "now" -> "● NU"; "upcoming" -> "KOMMANDE"; else -> "ARKIV" }
            val badgeColor = when (e.state) { "now" -> R.color.m_live; "upcoming" -> R.color.accent; else -> R.color.muted }
            row.addView(text(badgeText, 12f, getColor(badgeColor), true).apply { minWidth = Look.dp(this@WorldCupActivity, 84) })
            row.addView(text(e.title + if (o.optBoolean("main")) "  ★" else "", 17f, bold = true).apply { maxLines = 2 })
            box.addView(row)
            box.addView(text("${e.whenText} · ${e.channel}" + if (o.optBoolean("unconfirmed")) " · enligt TV4:s tablå" else "", 14f, getColor(R.color.muted)))
            box.addView(actions(e, o))
        }
        val seen = leg.optJSONArray("seen")
        if (seen != null && seen.length() > 0) {
            val parts = (0 until seen.length()).map { val s = seen.getJSONObject(it); "${s.optString("when")} · ${s.optString("channel")}" }
            box.addView(gap(10))
            box.addView(text("Sändes, finns inte längre i arkivet: ${parts.joinToString(", ")}", 13f, getColor(R.color.muted)))
        }
        if (mobile && events.any { it.state != "now" }) {
            box.addView(gap(6))
            box.addView(text("Inspelning och nerladdning görs på boxen.", 13f, getColor(R.color.muted)))
        }
        return box
    }

    /** Knapparna för en sändning: Titta / Öppna kanalen, Spela in / Ladda ner, eller inspelningens status. */
    private fun actions(e: Sports.Event, o: JSONObject): View {
        val wrap = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        val row = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL }
        val cmd = e.command
        if (cmd != null) {
            val label = str(o, "play_label") ?: "Titta"
            row.addView(button("▶ $label", primary = true) { Control.execute(this, cmd) })
        } else if (e.state == "upcoming") {
            o.optJSONObject("open_command")?.let { open -> row.addView(button("▶ Öppna ${e.channel}", primary = false) { Control.execute(this, open) }) }
        }
        val rec = o.optJSONObject("recording")
        val req = o.optJSONObject("record")
        if (rec != null) {
            val s = when (rec.optString("status")) {
                "scheduled" -> "● Inspelning planerad"; "recording" -> "● Spelas in just nu"; "done" -> "✔ Inspelad"
                "failed" -> "✖ Inspelningen misslyckades"; else -> rec.optString("status")
            }
            row.addView(text("   $s", 15f, getColor(R.color.accent), true))
        } else if (req != null && !mobile && req.optString("action") != "later") {
            val past = req.optString("action") == "download"
            row.addView(button(if (past) "⬇ Ladda ner" else "● Spela in", primary = false) { record(req, e.title, past) })
        }
        if (row.childCount > 0) wrap.addView(row)
        if (req != null && req.optString("action") == "later" && !mobile) wrap.addView(text(req.optString("label"), 13f, getColor(R.color.muted)))
        return wrap
    }

    private fun button(label: String, primary: Boolean, small: Boolean = false, onClick: () -> Unit): TextView {
        val b = TextView(this)
        b.text = label
        b.textSize = if (small) 14f else if (mobile) 16f else 18f
        // Primär: vit knapp med mörk text. Vanlig: ljus text som blir mörk när knappen får fokus (vit).
        if (primary) b.setTextColor(0xFF0A0A0C.toInt()) else b.setTextColor(resources.getColorStateList(R.color.m_on_focus, theme))
        b.setTypeface(b.typeface, Typeface.BOLD)
        b.gravity = Gravity.CENTER
        b.setBackgroundResource(if (primary) R.drawable.m_button_primary else R.drawable.m_button)
        val h = Look.dp(this, if (small) 14 else 20)
        val v = Look.dp(this, if (small) 8 else 12)
        b.setPadding(h, v, h, v)
        b.minHeight = Look.dp(this, if (small) 38 else if (mobile) 48 else 52)
        b.isFocusable = true
        b.isClickable = true
        b.layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT)
            .apply { topMargin = Look.dp(this@WorldCupActivity, 8); marginEnd = Look.dp(this@WorldCupActivity, 12) }
        Look.focusGrow(b, 1.05f)
        b.setOnClickListener { onClick() }
        if (firstButton == null) firstButton = b
        return b
    }

    private fun record(req: JSONObject, title: String, download: Boolean) {
        val body = req.optJSONObject("body") ?: return
        Api.obj("POST", "/device/recordings", body) { result ->
            result.onSuccess {
                RecScheduler.sync(this)
                val msg = if (download) "⬇ $title: hämtas från arkivet. Syns under Inspelningar." else "● $title: planerad. Syns under Inspelningar."
                Toast.makeText(this, msg, Toast.LENGTH_LONG).show()
                load()
            }.onFailure { Toast.makeText(this, Api.friendly(it), Toast.LENGTH_LONG).show() }
        }
    }
}
