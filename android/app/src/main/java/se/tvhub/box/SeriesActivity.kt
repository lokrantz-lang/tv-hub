package se.tvhub.box

import android.app.Activity
import android.app.AlertDialog
import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.view.View
import android.widget.ImageView
import android.widget.TextView
import android.widget.Toast
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import org.json.JSONObject

/** Säsonger och avsnitt för en serie. Visar var du är, sedda avsnitt och spelar i följd. */
class SeriesActivity : Activity() {
    companion object {
        fun start(context: Context, seriesId: String, name: String, resumeEpisodeId: String? = null) {
            context.startActivity(
                Intent(context, SeriesActivity::class.java)
                    .putExtra("id", seriesId)
                    .putExtra("name", name)
                    .putExtra("resume_episode", resumeEpisodeId)
            )
        }
    }

    private data class Episode(
        val id: String,
        val title: String,
        val ext: String,
        val positionMs: Long,
        val durationMs: Long,
        val watched: Boolean,
    )

    private lateinit var adapter: RowAdapter
    private lateinit var list: RecyclerView
    private lateinit var status: TextView
    private val episodes = ArrayList<Episode>()
    private var seriesId = ""
    private var seriesName = ""
    private var cover: String? = null
    private var favorite = false
    private var firstLoad = true
    private var modern = false
    private val seasonAdapter = RowAdapter(onClick = { row, _ -> jumpToSeason(row.key) })

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val prefs = Prefs(this)
        Look.init(this)
        modern = prefs.modern
        if (modern) Fonts.install(this)
        setContentView(if (modern) R.layout.activity_series_modern else R.layout.activity_series)
        Api.init(prefs)
        seriesId = intent.getStringExtra("id") ?: return finish()
        seriesName = intent.getStringExtra("name") ?: ""
        findViewById<TextView>(R.id.title).text = seriesName
        status = findViewById(R.id.status)
        list = findViewById(R.id.episodes)
        adapter = RowAdapter(onClick = { row, _ -> onClick(row) })
        adapter.modern = modern
        list.layoutManager = LinearLayoutManager(this)
        list.adapter = adapter
        list.itemAnimator = null
        findViewById<RecyclerView?>(R.id.seasons)?.let { seasons ->
            seasonAdapter.edgeLock = false
            seasons.layoutManager = LinearLayoutManager(this, LinearLayoutManager.HORIZONTAL, false)
            seasons.adapter = SeasonChips(seasonAdapter)
            seasons.itemAnimator = null
        }
    }

    /** Säsonger som flikar överst (modernt utseende). OK hoppar till säsongens första avsnitt. */
    private class SeasonChips(private val rows: RowAdapter) : RecyclerView.Adapter<RowAdapter.Holder>() {
        init {
            rows.registerAdapterDataObserver(object : RecyclerView.AdapterDataObserver() {
                override fun onChanged() = notifyDataSetChanged()
            })
        }

        override fun getItemCount() = rows.itemCount

        override fun onCreateViewHolder(parent: android.view.ViewGroup, viewType: Int): RowAdapter.Holder =
            RowAdapter.Holder(android.view.LayoutInflater.from(parent.context).inflate(R.layout.chip_m, parent, false))

        override fun onBindViewHolder(holder: RowAdapter.Holder, position: Int) = rows.onBindViewHolder(holder, position)
    }

    private fun jumpToSeason(key: String) {
        val index = adapter.indexOf(key)
        if (index >= 0) Focus.position(list, index + 1)
    }

    override fun onResume() {
        super.onResume()
        // Ladda om efter uppspelning så att "Fortsätt" och sedda avsnitt stämmer.
        Api.obj("GET", "/device/series/${Api.enc(seriesId)}") { result ->
            result.onSuccess { show(it) }.onFailure { status.text = Api.friendly(it) }
        }
    }

    private fun show(data: JSONObject) {
        val focused = Focus.focusedPosition(list)
        data.optStringOrNull("plot")?.let { findViewById<TextView>(R.id.plot).text = it }
        cover = data.optStringOrNull("cover")
        ImageLoader.load(findViewById<ImageView>(R.id.cover), cover)
        findViewById<ImageView?>(R.id.backdrop)?.let {
            if (it.drawable == null) ImageLoader.load(it, data.optStringOrNull("backdrop") ?: cover, ImageLoader.BACKDROP, fade = true)
        }
        favorite = data.optBoolean("favorite")
        val progress = data.optJSONObject("progress") ?: JSONObject()
        val watchedArray = data.optJSONArray("watched")
        val watched = HashSet<String>()
        if (watchedArray != null) for (i in 0 until watchedArray.length()) watched.add(watchedArray.optString(i))

        val rows = ArrayList<Row>()
        episodes.clear()
        val seasons = data.optJSONArray("seasons")
        val episodeRows = ArrayList<Row>()
        if (seasons != null) {
            for (s in 0 until seasons.length()) {
                val season = seasons.optJSONObject(s) ?: continue
                val seasonNo = season.optString("season")
                episodeRows.add(Row("Säsong $seasonNo", key = "season:$seasonNo", header = true))
                val eps = season.optJSONArray("episodes") ?: continue
                for (e in 0 until eps.length()) {
                    val ep = eps.optJSONObject(e) ?: continue
                    val num = ep.optStringOrNull("num") ?: "${e + 1}"
                    val title = ep.optStringOrNull("title") ?: "Avsnitt $num"
                    val id = ep.optString("id")
                    val p = progress.optJSONObject(id)
                    val episode = Episode(
                        id = id,
                        title = "$seriesName S${seasonNo}E$num",
                        ext = ep.optStringOrNull("ext") ?: "mp4",
                        positionMs = p?.optLong("position_ms") ?: 0L,
                        durationMs = p?.optLong("duration_ms") ?: 0L,
                        watched = id in watched,
                    )
                    episodes.add(episode)
                    val state = when {
                        episode.positionMs > 0 -> "Sett ${Format.clock(episode.positionMs)} · ${Format.remaining(episode.positionMs, episode.durationMs)}"
                        episode.watched -> "✓ Sedd"
                        else -> null
                    }
                    val sub = listOfNotNull(state, ep.optStringOrNull("duration"), ep.optStringOrNull("plot")).joinToString(" · ")
                    episodeRows.add(Row("$num. $title", subtitle = sub, key = "ep:$id", payload = episode,
                        icon = if (modern) ep.optStringOrNull("image") ?: cover else null, iconSpace = modern))
                }
            }
        }
        if (episodes.isEmpty()) {
            status.text = "Inga avsnitt hittades."
            return
        }
        status.visibility = View.GONE

        // Åtgärder överst: fortsätt där du var, eller nästa osedda avsnitt.
        val inProgress = episodes.firstOrNull { it.positionMs > 0 }
        val nextUnseen = run {
            val lastWatched = episodes.indexOfLast { it.watched }
            episodes.getOrNull(lastWatched + 1) ?: episodes.first()
        }
        when {
            inProgress != null -> rows.add(
                Row("▶  Fortsätt ${inProgress.title.removePrefix("$seriesName ")} från ${Format.clock(inProgress.positionMs)}",
                    key = "action:resume", payload = "resume" to inProgress)
            )
            else -> rows.add(
                Row("▶  Spela ${nextUnseen.title.removePrefix("$seriesName ")}", key = "action:next", payload = "start" to nextUnseen)
            )
        }
        rows.add(Row(if (favorite) "★  Favorit" else "☆  Lägg till i favoriter", key = "action:favorite", payload = "favorite"))
        rows.addAll(episodeRows)
        adapter.submit(rows)
        seasonAdapter.submit(episodeRows.filter { it.header }.map { Row(it.title, key = it.key) })
        if (firstLoad) {
            firstLoad = false
            Focus.position(list, 0)
        } else if (focused != RecyclerView.NO_POSITION) {
            Focus.position(list, focused)
        }
    }

    private fun onClick(row: Row) {
        when (val payload = row.payload) {
            is Pair<*, *> -> {
                val episode = payload.second as? Episode ?: return
                play(episode, if (payload.first == "resume") episode.positionMs else 0L)
            }
            "favorite" -> toggleFavorite()
            is Episode -> {
                if (payload.positionMs > 0) {
                    Look.choose(this, payload.title, listOf("Fortsätt från ${Format.clock(payload.positionMs)}", "Börja om")) { which ->
                        play(payload, if (which == 0) payload.positionMs else 0L)
                    }
                } else {
                    play(payload, 0L)
                }
            }
        }
    }

    private fun play(episode: Episode, startMs: Long) {
        val index = episodes.indexOf(episode)
        PlayerActivity.start(
            this,
            type = "episode",
            id = episode.id,
            name = episode.title,
            ext = episode.ext,
            queueIds = ArrayList(episodes.map { it.id }),
            queueNames = ArrayList(episodes.map { it.title }),
            queueExts = ArrayList(episodes.map { it.ext }),
            queueIndex = index,
            startMs = startMs,
            seriesId = seriesId,
            icon = cover,
        )
    }

    private fun toggleFavorite() {
        val on = !favorite
        Api.obj("POST", "/device/favorites", JSONObject().put("type", "series").put("id", seriesId).put("on", on)) { result ->
            result.onSuccess {
                favorite = on
                val index = adapter.indexOf("action:favorite")
                if (index >= 0) {
                    adapter.replace(index, Row(if (on) "★  Favorit" else "☆  Lägg till i favoriter",
                        key = "action:favorite", payload = "favorite"))
                }
            }.onFailure { Toast.makeText(this, Api.friendly(it), Toast.LENGTH_LONG).show() }
        }
    }
}
