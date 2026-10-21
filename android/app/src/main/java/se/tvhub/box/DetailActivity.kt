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
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import org.json.JSONObject

/** Detaljsida för en film: affisch, handling, betyg, längd, skådespelare. Fortsätt eller börja om. */
class DetailActivity : Activity(), CardListener {
    companion object {
        fun start(context: Context, vodId: String, name: String, autoPlay: Boolean = false) {
            context.startActivity(Intent(context, DetailActivity::class.java).putExtra("id", vodId).putExtra("name", name)
                .putExtra("auto_play", autoPlay))
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
    private var modern = false
    private var moreLoaded = false
    private var autoPlay = false
    private var myRating = 0
    private var wish = false
    private var versions: List<Triple<String, String, String>> = emptyList()  // id, namn, kategori

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        prefs = Prefs(this)
        Look.init(this)
        FormFactor.portraitIfMobile(this)
        modern = prefs.modern
        if (modern) Fonts.install(this)
        setContentView(if (modern) R.layout.activity_detail_modern else R.layout.activity_detail)
        Api.init(prefs)
        vodId = intent.getStringExtra("id") ?: return finish()
        autoPlay = intent.getBooleanExtra("auto_play", false)
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
        findViewById<Button?>(R.id.record)?.let { button ->
            button.setOnClickListener { RecordMedia.film(this, vodId) }
            if (modern) Look.focusGrow(button, 1.05f)
        }
        if (modern) listOf(resumeButton, playButton, favoriteButton).forEach { Look.focusGrow(it, 1.05f) }
        findViewById<Button?>(R.id.rate)?.let { button ->
            button.setOnClickListener { Taste.rate(this, "vod", vodId, name, askWho = true) { load() } }
            Look.focusGrow(button, 1.05f)
        }
        findViewById<Button?>(R.id.wish)?.let { button ->
            button.setOnClickListener {
                val on = !wish
                Taste.send(this, "vod", vodId, if (on) "wish" else "unwish",
                    toast = if (on) "Tillagd på Vill se" else "Borttagen från Vill se") { load() }
            }
            Look.focusGrow(button, 1.05f)
        }
        findViewById<Button?>(R.id.play_on_tv)?.let { button ->
            // Telefonen: starta filmen på en TV-box.
            if (FormFactor.isMobile(this) && !prefs.adultMode) {
                button.visibility = View.VISIBLE
                button.setOnClickListener { BoxSend.choose(this, JSONObject().put("type", "vod").put("id", vodId)) }
            }
        }
        findViewById<Button?>(R.id.versions)?.let { button ->
            button.setOnClickListener { chooseVersion() }
            Look.focusGrow(button, 1.05f)
        }
        playButton.requestFocus()
    }

    override fun onResume() {
        super.onResume()
        load()
        Taste.promptPending(this)
    }

    /** Samma film i flera versioner (t.ex. 4K, svensk text). */
    private fun chooseVersion() {
        if (versions.size < 2) return
        val current = versions.indexOfFirst { it.first == vodId }
        val labels = versions.map { v -> v.second + (v.third.takeIf { it.isNotEmpty() }?.let { "  ·  $it" } ?: "") }
        Look.choose(this, "Välj version", labels, current) { which ->
            val v = versions[which]
            if (v.first != vodId) {
                start(this, v.first, v.second)
                finish()
            }
        }
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
        findViewById<ImageView?>(R.id.backdrop)?.let {
            if (it.drawable == null) ImageLoader.load(it, data.optStringOrNull("backdrop") ?: data.optStringOrNull("poster") ?: icon, ImageLoader.BACKDROP, fade = true)
        }
        if (modern && !moreLoaded) loadMore(data.optStringOrNull("genre"))
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

        // Filmkväll (finns inte i vuxenläget: då saknas fälten).
        val tasteOn = data.has("my_rating")
        myRating = data.optInt("my_rating")
        wish = data.optBoolean("wish")
        val arr = data.optJSONArray("versions")
        val list = ArrayList<Triple<String, String, String>>()
        if (arr != null) for (i in 0 until arr.length()) {
            val v = arr.optJSONObject(i) ?: continue
            list.add(Triple(v.optString("id"), v.optString("name"), v.optString("category_name")))
        }
        versions = list
        findViewById<Button?>(R.id.rate)?.let {
            it.text = if (myRating == 0) "Betygsätt" else Taste.label(myRating)
            it.visibility = if (tasteOn) View.VISIBLE else View.GONE
        }
        findViewById<Button?>(R.id.wish)?.let {
            it.text = if (wish) "★  Vill se" else "☆  Vill se"
            it.visibility = if (tasteOn) View.VISIBLE else View.GONE
        }
        findViewById<Button?>(R.id.versions)?.let {
            it.text = "Versioner (${versions.size})"
            it.visibility = if (versions.size > 1) View.VISIBLE else View.GONE
        }
        if (autoPlay) {
            autoPlay = false
            play(resumeMs)
        }
    }

    /** "Mer som detta": högst betygsatta i samma genre. */
    private fun loadMore(genre: String?) {
        val list = findViewById<RecyclerView?>(R.id.more) ?: return
        val first = genre?.split(",", "/", "|", "&")?.map { it.trim() }?.firstOrNull { it.length >= 2 } ?: return
        moreLoaded = true
        Api.obj("GET", "/device/browse?type=vod&genre=${Api.enc(first)}&sort=rating&limit=24") { result ->
            result.onSuccess { data ->
                val cards = Item.list(data.optJSONArray("items")).filter { it.id != vodId }.map { Card.poster(it) }
                if (cards.isEmpty()) return@onSuccess
                val adapter = CardAdapter(this)
                list.layoutManager = LinearLayoutManager(this, LinearLayoutManager.HORIZONTAL, false)
                list.adapter = adapter
                adapter.submit(cards)
                findViewById<TextView?>(R.id.more_title)?.visibility = View.VISIBLE
            }
        }
    }

    override fun onCardClick(card: Card, shelf: Shelf?) {
        val item = card.payload as? Item ?: return
        start(this, item.id, item.name)
    }

    override fun onCardFocus(card: Card, shelf: Shelf?, shelfIndex: Int, cardIndex: Int) {}

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
