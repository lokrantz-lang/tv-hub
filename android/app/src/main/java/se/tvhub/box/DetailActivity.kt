package se.tvhub.box

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.view.View
import android.widget.Button
import android.widget.ImageView
import android.widget.TextView
import android.widget.Toast
import org.json.JSONObject

/** Detaljsida för en film: affisch, handling, betyg, längd, skådespelare. Fortsätt eller börja om. */
class DetailActivity : Activity() {
    companion object {
        fun start(context: Context, vodId: String, name: String) {
            context.startActivity(Intent(context, DetailActivity::class.java).putExtra("id", vodId).putExtra("name", name))
        }
    }

    private lateinit var prefs: Prefs
    private lateinit var resumeButton: Button
    private lateinit var playButton: Button
    private lateinit var favoriteButton: Button
    private lateinit var status: TextView
    private var vodId = ""
    private var name = ""
    private var ext: String? = null
    private var icon: String? = null
    private var favorite = false
    private var resumeMs = 0L

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_detail)
        prefs = Prefs(this)
        Api.init(prefs)
        vodId = intent.getStringExtra("id") ?: return finish()
        name = intent.getStringExtra("name") ?: ""
        findViewById<TextView>(R.id.title).text = name
        resumeButton = findViewById(R.id.play_resume)
        playButton = findViewById(R.id.play)
        favoriteButton = findViewById(R.id.favorite)
        status = findViewById(R.id.status)
        status.text = "Hämtar information…"
        favoriteButton.visibility = View.INVISIBLE

        resumeButton.setOnClickListener { play(resumeMs) }
        playButton.setOnClickListener { play(0L) }
        favoriteButton.setOnClickListener { toggleFavorite() }
        playButton.requestFocus()
    }

    override fun onResume() {
        super.onResume()
        load()
    }

    private fun load() {
        Api.obj("GET", "/device/vod/${Api.enc(vodId)}") { result ->
            result.onSuccess { show(it) }.onFailure { status.text = Api.friendly(it) }
        }
    }

    private fun show(data: JSONObject) {
        status.text = ""
        name = data.optStringOrNull("name") ?: name
        ext = data.optStringOrNull("ext")
        icon = data.optStringOrNull("icon")
        favorite = data.optBoolean("favorite")
        findViewById<TextView>(R.id.title).text = name
        ImageLoader.load(findViewById<ImageView>(R.id.poster), data.optStringOrNull("poster") ?: icon)
        val item = Item.from(data)
        val meta = listOfNotNull(
            item.facts.ifEmpty { null },
            data.optStringOrNull("genre"),
            data.optStringOrNull("country"),
            if (data.optBoolean("watched")) "✓ Sedd" else null,
        ).joinToString("   ·   ")
        findViewById<TextView>(R.id.meta).text = meta
        findViewById<TextView>(R.id.plot).text = data.optStringOrNull("plot") ?: "Ingen handling tillgänglig."
        findViewById<TextView>(R.id.people).text = listOfNotNull(
            data.optStringOrNull("director")?.let { "Regi: $it" },
            data.optStringOrNull("actors")?.let { "Medverkande: $it" },
        ).joinToString("\n")

        val resume = data.optJSONObject("resume")
        resumeMs = resume?.optLong("position_ms") ?: 0L
        val hadFocus = resumeButton.hasFocus() || playButton.hasFocus()
        if (resumeMs > 0) {
            val duration = resume?.optLong("duration_ms") ?: 0L
            resumeButton.text = "▶  Fortsätt från ${Format.clock(resumeMs)}" +
                if (duration > 0) "  (${Format.remaining(resumeMs, duration)})" else ""
            resumeButton.visibility = View.VISIBLE
            playButton.text = "Börja om"
            if (hadFocus || !favoriteButton.hasFocus()) resumeButton.requestFocus()
        } else {
            resumeButton.visibility = View.GONE
            playButton.text = "▶  Spela"
        }
        favoriteButton.visibility = View.VISIBLE
        favoriteButton.text = if (favorite) "★  Favorit" else "☆  Favorit"
    }

    private fun play(startMs: Long) {
        PlayerActivity.start(this, type = "vod", id = vodId, name = name, ext = ext, startMs = startMs)
    }

    private fun toggleFavorite() {
        val on = !favorite
        Api.obj("POST", "/device/favorites", JSONObject().put("type", "vod").put("id", vodId).put("on", on)) { result ->
            result.onSuccess {
                favorite = on
                favoriteButton.text = if (favorite) "★  Favorit" else "☆  Favorit"
            }.onFailure { Toast.makeText(this, Api.friendly(it), Toast.LENGTH_LONG).show() }
        }
    }
}
