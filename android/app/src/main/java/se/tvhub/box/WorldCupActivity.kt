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

    private fun render(data: JSONObject) {
        content.removeAllViews()
        firstButton = null
        content.addView(text("🏇 Världscupen i hoppning", if (mobile) 24f else 30f, bold = true))
        content.addView(text("Säsongen ${data.optString("season")}", 16f, getColor(R.color.muted)))
        val hints = data.optJSONArray("hints")
        if (hints != null && hints.length() > 0) {
            content.addView(gap(8))
            for (i in 0 until hints.length()) content.addView(text("⚠ ${hints.optString(i)}", 14f, getColor(R.color.star)))
        }
        val legs = data.optJSONArray("legs") ?: JSONArray()
        if (legs.length() == 0) {
            content.addView(gap(16))
            content.addView(text("Inget att visa här.", 16f, getColor(R.color.muted)))
            return
        }
        val next = data.optString("next")
        val upcoming = ArrayList<JSONObject>()
        val done = ArrayList<JSONObject>()
        for (i in 0 until legs.length()) {
            val leg = legs.getJSONObject(i)
            if (leg.optString("state") == "done") done.add(leg) else upcoming.add(leg)
        }
        content.addView(gap(14))
        content.addView(text("PLANERING", 14f, getColor(R.color.accent), true))
        if (upcoming.isEmpty()) content.addView(text("Säsongen är slut.", 16f, getColor(R.color.muted)))
        for (leg in upcoming) content.addView(legView(leg, leg.optString("id") == next || leg.optString("state") == "ongoing"))
        if (done.isNotEmpty()) {
            content.addView(gap(18))
            content.addView(text("HISTORIK", 14f, getColor(R.color.accent), true))
            for (leg in done.reversed()) content.addView(legView(leg, false))
        }
        focusFirst()
    }

    private fun legView(leg: JSONObject, expanded: Boolean): View {
        val box = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundResource(R.drawable.m_card_bg)
            val p = Look.dp(this@WorldCupActivity, 14)
            setPadding(p, p, p, p)
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
                .apply { topMargin = Look.dp(this@WorldCupActivity, 10) }
        }
        val badge = when {
            leg.optString("kind") == "final" -> "  · FINAL"
            leg.optString("kind") == "other" -> "  · ingen etapp"
            leg.optBoolean("preliminary") -> "  · preliminärt datum"
            else -> ""
        }
        box.addView(text("${leg.optString("name")}$badge", 20f, bold = true))
        box.addView(text("${leg.optString("dates")} · ${leg.optString("country")} · Världscupklassen: ${leg.optString("wc_label")}", 14f, getColor(R.color.muted)))
        val tv = leg.optString("tv")
        if (tv.isNotEmpty() && tv != "null") box.addView(text("TV: $tv", 14f, getColor(R.color.muted)))
        val winner = leg.optString("winner").takeIf { it.isNotEmpty() && it != "null" }
        if (winner != null) {
            val horse = leg.optString("horse").takeIf { it.isNotEmpty() && it != "null" }
            box.addView(text("🏆 $winner" + (horse?.let { " på $it" } ?: ""), 16f, getColor(R.color.star)))
        }
        val level = leg.optString("level")
        val headColor = when (level) { "ok" -> getColor(R.color.accent); "warn" -> getColor(R.color.star); else -> getColor(R.color.muted) }
        box.addView(text(leg.optString("headline"), 15f, headColor, true))
        val events = Sports.parseEvents(leg.optJSONArray("events"))
        val raw = leg.optJSONArray("events") ?: JSONArray()
        for (i in events.indices) {
            val e = events[i]
            val o = raw.getJSONObject(i)
            box.addView(gap(8))
            val state = when (e.state) { "now" -> "● SÄNDS NU"; "upcoming" -> "Kommande"; else -> "Finns i arkivet" }
            box.addView(text(e.title + if (o.optBoolean("main")) "  ★" else "", 16f, bold = true))
            box.addView(text("$state · ${e.whenText} · ${e.channel}", 14f, getColor(R.color.muted)))
            if (o.optBoolean("unconfirmed")) box.addView(text("Enligt tablå, ej bekräftad i din programguide", 13f, getColor(R.color.star)))
            val rec = o.optJSONObject("recording")
            if (rec != null) {
                val s = when (rec.optString("status")) {
                    "scheduled" -> "● Inspelning planerad"; "recording" -> "● Spelas in just nu"; "done" -> "✔ Inspelad"
                    "failed" -> "✖ Inspelningen misslyckades"; else -> rec.optString("status")
                }
                box.addView(text(s, 14f, getColor(R.color.accent), true))
            }
            val row = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
            val cmd = e.command
            if (cmd != null) {
                val label = o.optString("play_label").takeIf { it.isNotEmpty() && it != "null" } ?: "Titta"
                row.addView(button("▶ $label", primary = true) { Control.execute(this, cmd) })
            }
            val req = o.optJSONObject("record")
            if (req != null && req.optString("action") == "later" && !mobile) {
                box.addView(text(req.optString("label"), 13f, getColor(R.color.muted)))
            } else if (req != null && !mobile && rec == null) {
                val past = req.optString("action") == "download"
                row.addView(button(if (past) "⬇ Ladda ner" else "● Spela in", primary = false) { record(req, e.title, past) })
            }
            if (row.childCount > 0) box.addView(row)
        }
        val seen = leg.optJSONArray("seen")
        if (seen != null && seen.length() > 0) {
            val parts = (0 until seen.length()).map { val s = seen.getJSONObject(it); "${s.optString("when")} · ${s.optString("channel")}" }
            box.addView(gap(6))
            box.addView(text("Sändes (finns inte längre i arkivet): ${parts.joinToString(", ")}", 13f, getColor(R.color.muted)))
        }
        if (mobile && events.any { it.state != "now" }) {
            box.addView(text("Inspelning och nerladdning görs på boxen.", 13f, getColor(R.color.muted)))
        }
        return box
    }

    private fun button(label: String, primary: Boolean, onClick: () -> Unit): TextView {
        val b = TextView(this)
        b.text = label
        b.textSize = if (mobile) 16f else 18f
        b.setTextColor(getColor(R.color.m_text))
        b.setTypeface(b.typeface, Typeface.BOLD)
        b.gravity = Gravity.CENTER
        b.setBackgroundResource(if (primary) R.drawable.m_button_primary else R.drawable.m_button)
        val h = Look.dp(this, 20)
        b.setPadding(h, Look.dp(this, 12), h, Look.dp(this, 12))
        b.minHeight = Look.dp(this, if (mobile) 48 else 52)
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
