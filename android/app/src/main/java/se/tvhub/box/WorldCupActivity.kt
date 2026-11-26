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
    private lateinit var header: LinearLayout
    private lateinit var season: TextView
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
        val side = Look.dp(this, if (mobile) 16 else 56)
        // Rubriken ligger fast överst; resten rullar under den.
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(getColor(R.color.bg))
        }
        header = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.BOTTOM
            setPadding(side, Look.dp(this@WorldCupActivity, if (mobile) 16 else 24), side, Look.dp(this@WorldCupActivity, 10))
        }
        header.addView(text("🏇 Världscupen i hoppning", if (mobile) 22f else 28f, bold = true))
        season = text("", 15f, getColor(R.color.muted))
        header.addView(season)
        root.addView(header, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))
        scroll = ScrollView(this).apply { isFillViewport = true; clipToPadding = false }
        content = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(side, Look.dp(this@WorldCupActivity, 4), side, Look.dp(this@WorldCupActivity, 48))
            clipChildren = false
            clipToPadding = false
        }
        scroll.addView(content, ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))
        root.addView(scroll, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f))
        setContentView(root)
        load()
    }

    override fun dispatchTouchEvent(ev: MotionEvent): Boolean {
        if (ev.action == MotionEvent.ACTION_DOWN) prefs.touch()
        return super.dispatchTouchEvent(ev)
    }

    override fun onResume() {
        super.onResume()
        if (data != null) refresh()
    }

    /** Hämtar på nytt utan att tömma sidan; rullningen och markeringen behålls så gott det går. */
    private fun refresh() {
        val y = scroll.scrollY
        val focusedLeg = currentFocus?.let { legOf(it) }
        Api.obj("GET", "/device/worldcup") { result ->
            if (isFinishing) return@obj
            result.onSuccess {
                render(it, focusAfter = false)
                scroll.post {
                    scroll.scrollTo(0, y)
                    if (!mobile) (focusedLeg?.let { id -> legViews[id]?.let { v -> firstFocusable(v) } } ?: firstButton)?.requestFocus()
                }
            }
        }
    }

    private fun legOf(v: View): String? {
        var x: View? = v
        while (x != null) {
            (x.tag as? String)?.takeIf { it.startsWith("leg:") }?.let { return it.removePrefix("leg:") }
            x = x.parent as? View
        }
        return null
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
        content.addView(text("Hämtar…", 16f, getColor(R.color.muted)))
        content.addView(ProgressBar(this))
        Api.obj("GET", "/device/worldcup") { result ->
            if (isFinishing) return@obj
            result.onSuccess { render(it) }.onFailure {
                content.removeAllViews()
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
    private val tiles = HashMap<String, View>()

    private fun str(o: JSONObject?, key: String): String? = o?.optString(key)?.takeIf { it.isNotEmpty() && it != "null" }

    private val legViews = HashMap<String, View>()

    private fun render(d: JSONObject, focusAfter: Boolean = true) {
        data = d
        content.removeAllViews()
        firstButton = null
        tiles.clear()
        legViews.clear()
        season.text = "   Säsongen ${d.optString("season")}"
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

        // Etapperna i följd: den aktuella och alla kommande, sedan de avgjorda (senaste först). Rutan i raden hoppar dit.
        val startIdx = legList.indexOfFirst { it.optString("id") == start }.coerceAtLeast(0)
        val coming = legList.drop(startIdx).filter { it.optString("state") != "done" || it.optString("id") == start }
        val done = legList.filter { it.optString("state") == "done" && it !in coming }.reversed()
        for (leg in coming) addLeg(leg)
        if (done.isNotEmpty()) {
            content.addView(gap(24))
            content.addView(text("AVGJORDA", 13f, getColor(R.color.muted), true))
            for (leg in done) addLeg(leg)
        }
        for ((k, v) in tiles) v.isActivated = k == start
        selectedLeg = start
        tiles[start]?.let { t -> t.post { (t.parent?.parent as? HorizontalScrollView)?.scrollTo((t.left - Look.dp(this, 40)).coerceAtLeast(0), 0) } }

        str(d, "status_line")?.let {
            content.addView(gap(22))
            content.addView(text("ⓘ $it", 13f, getColor(R.color.muted)))
        }
        if (focusAfter) focusFirst()
    }

    private fun addLeg(leg: JSONObject) {
        val v = legView(leg)
        v.tag = "leg:" + leg.optString("id")
        legViews[leg.optString("id")] = v
        content.addView(v)
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
            if (has && !mobile) select(id, scrollTo = false)  // markerar bara rutan
        }
        tiles[id] = box
        return box
    }

    private fun select(id: String, scrollTo: Boolean, focusDetail: Boolean = false) {
        selectedLeg = id
        for ((k, v) in tiles) v.isActivated = k == id
        if (!focusDetail && !scrollTo) return
        val target = legViews[id] ?: return
        scroll.post {
            scroll.smoothScrollTo(0, (target.top - Look.dp(this, 8)).coerceAtLeast(0))
            if (focusDetail && !mobile) firstFocusable(target)?.requestFocus()
        }
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
            setPadding(p, p, p, p + Look.dp(this@WorldCupActivity, 6))
            clipChildren = false
            clipToPadding = false
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
                .apply { topMargin = Look.dp(this@WorldCupActivity, 16) }
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
        // Pallen: ettan, tvåan och trean.
        val podium = leg.optJSONArray("podium")
        if (podium != null && podium.length() > 0) {
            box.addView(gap(8))
            val medals = listOf("🥇", "🥈", "🥉")
            for (i in 0 until podium.length()) {
                val p = podium.getJSONObject(i)
                val horse = str(p, "horse")?.let { " på $it" } ?: ""
                box.addView(text("${medals.getOrElse(i) { "${i + 1}." }}  ${p.optString("rider")}$horse", if (i == 0) 18f else 16f,
                    getColor(if (i == 0) R.color.star else R.color.text), i == 0))
            }
        }
        val events = Sports.parseEvents(leg.optJSONArray("events"))
        val raw = leg.optJSONArray("events") ?: JSONArray()
        if (events.isEmpty()) {
            box.addView(gap(8))
            box.addView(text(leg.optString("headline"), 15f, getColor(R.color.muted)))
        }
        // Direkt och första sändning först; repriser och sammandrag på en rad som fälls ut.
        val primary = events.indices.filter { raw.getJSONObject(it).optString("kind") in setOf("live", "first", "") }
        val secondary = events.indices.filter { it !in primary }
        for (i in primary) box.addView(eventView(events[i], raw.getJSONObject(i)))
        if (secondary.isNotEmpty()) {
            val more = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; visibility = View.GONE }
            for (i in secondary) more.addView(eventView(events[i], raw.getJSONObject(i)))
            box.addView(gap(10))
            val toggle = button("Repriser och sammandrag (${secondary.size})  ▾", primary = false, small = true) {}
            toggle.setOnClickListener {
                val open = more.visibility != View.VISIBLE
                more.visibility = if (open) View.VISIBLE else View.GONE
                toggle.text = "Repriser och sammandrag (${secondary.size})  " + if (open) "▴" else "▾"
            }
            box.addView(toggle)
            box.addView(more)
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

    private fun eventView(e: Sports.Event, o: JSONObject): View {
        val v = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        v.addView(gap(14))
        val row = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL }
        val kind = o.optString("kind")
        val badgeText = when {
            kind == "live" && e.state == "now" -> "● DIREKT"
            kind == "live" -> "DIREKT"
            kind == "replay" -> "REPRIS"
            kind == "highlights" -> "SAMMANDRAG"
            else -> "FÖRSTA SÄNDNING"
        }
        val badgeColor = when (kind) { "live" -> R.color.m_live; "replay", "highlights" -> R.color.muted; else -> R.color.accent }
        row.addView(text(badgeText, 12f, getColor(badgeColor), true).apply { minWidth = Look.dp(this@WorldCupActivity, 120) })
        row.addView(text(e.title + if (o.optBoolean("main")) "  ★" else "", 17f, bold = true).apply { maxLines = 2 })
        v.addView(row)
        val state = when (e.state) { "now" -> "Sänds nu · "; "past" -> "I arkivet · "; else -> "" }
        v.addView(text("$state${e.whenText} · ${e.channel}" + if (o.optBoolean("unconfirmed")) " · enligt TV4:s tablå" else "", 14f, getColor(R.color.muted)))
        v.addView(actions(e, o))
        return v
    }

    /** Knapparna för en sändning: Titta / Öppna kanalen, Spela in / Ladda ner, eller inspelningens status. */
    private fun actions(e: Sports.Event, o: JSONObject): View {
        val wrap = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; clipChildren = false; clipToPadding = false }
        fillActions(wrap, e, o)
        return wrap
    }

    /** Bygger knapparna. Anropas igen efter Spela in och Avboka, så att sidan inte laddas om. */
    private fun fillActions(wrap: LinearLayout, e: Sports.Event, o: JSONObject): View? {
        wrap.removeAllViews()
        val row = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            clipChildren = false
            clipToPadding = false
            setPadding(0, Look.dp(this@WorldCupActivity, 2), 0, Look.dp(this@WorldCupActivity, 8))
        }
        val cmd = e.command
        if (cmd != null) {
            val label = str(o, "play_label") ?: "Titta"
            row.addView(button("▶ $label", primary = true) { Control.execute(this, cmd) })
        } else if (e.state == "upcoming") {
            o.optJSONObject("open_command")?.let { open -> row.addView(button("▶ Öppna ${e.channel}", primary = false) { Control.execute(this, open) }) }
        }
        var recButton: View? = null
        val rec = o.optJSONObject("recording")
        val req = o.optJSONObject("record")
        if (rec != null) {
            val status = rec.optString("status")
            val s = when (status) {
                "scheduled" -> "● Inspelning planerad"; "recording" -> "● Spelas in just nu"; "done" -> "✔ Inspelad"
                "failed" -> "✖ Inspelningen misslyckades"; else -> status
            }
            if (status == "scheduled" && !mobile && rec.optInt("id") > 0) {
                // Planerad inspelning: tryck för att avboka.
                recButton = button(s, primary = false) { cancelRecording(wrap, e, o, rec.optInt("id")) }
                row.addView(recButton)
            } else {
                row.addView(text("   $s", 15f, getColor(R.color.accent), true))
            }
        } else if (req != null && !mobile && req.optString("action") != "later") {
            val past = req.optString("action") == "download"
            recButton = button(if (past) "⬇ Ladda ner" else "● Spela in", primary = false) { record(wrap, e, o, req, past) }
            row.addView(recButton)
        }
        if (row.childCount > 0) wrap.addView(row)
        if (req != null && req.optString("action") == "later" && !mobile) wrap.addView(text(req.optString("label"), 13f, getColor(R.color.muted)))
        return recButton
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

    private fun record(wrap: LinearLayout, e: Sports.Event, o: JSONObject, req: JSONObject, download: Boolean) {
        val body = req.optJSONObject("body") ?: return
        Api.obj("POST", "/device/recordings", body) { result ->
            if (isFinishing) return@obj
            result.onSuccess { rec ->
                RecScheduler.sync(this)
                val msg = if (download) "⬇ ${e.title}: hämtas från arkivet. Syns under Inspelningar." else "● ${e.title}: planerad. Syns under Inspelningar."
                Toast.makeText(this, msg, Toast.LENGTH_LONG).show()
                // Uppdatera bara knapparna för sändningen; sidan ligger kvar där den var.
                o.put("recording", JSONObject().put("id", rec.optInt("id")).put("status", rec.optString("status").ifEmpty { "scheduled" }))
                fillActions(wrap, e, o)?.let { if (!mobile) it.requestFocus() }
            }.onFailure { Toast.makeText(this, Api.friendly(it), Toast.LENGTH_LONG).show() }
        }
    }

    private fun cancelRecording(wrap: LinearLayout, e: Sports.Event, o: JSONObject, id: Int) {
        Look.confirm(this, "Avboka inspelningen?", e.title, "Avboka") {
            Api.obj("DELETE", "/device/recordings/$id") { result ->
                if (isFinishing) return@obj
                result.onSuccess {
                    RecScheduler.cancel(this, id)
                    RecScheduler.sync(this)
                    Toast.makeText(this, "Inspelningen är avbokad.", Toast.LENGTH_SHORT).show()
                    o.remove("recording")
                    fillActions(wrap, e, o)?.let { if (!mobile) it.requestFocus() }
                }.onFailure { Toast.makeText(this, Api.friendly(it), Toast.LENGTH_LONG).show() }
            }
        }
    }
}
