package se.tvhub.box

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.view.View
import android.widget.ImageView
import android.widget.TextView
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import org.json.JSONObject

/** Säsonger och avsnitt för en serie. Spelar upp avsnitt i följd. */
class SeriesActivity : Activity() {
    companion object {
        fun start(context: Context, seriesId: String, name: String) {
            context.startActivity(
                Intent(context, SeriesActivity::class.java).putExtra("id", seriesId).putExtra("name", name)
            )
        }
    }

    private data class Episode(val id: String, val title: String, val ext: String)

    private lateinit var adapter: RowAdapter
    private lateinit var list: RecyclerView
    private lateinit var status: TextView
    private val episodes = ArrayList<Episode>()
    private var seriesName = ""

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_series)
        Api.init(Prefs(this))
        val seriesId = intent.getStringExtra("id") ?: return finish()
        seriesName = intent.getStringExtra("name") ?: ""
        findViewById<TextView>(R.id.title).text = seriesName
        status = findViewById(R.id.status)
        list = findViewById(R.id.episodes)
        adapter = RowAdapter(onClick = { row, _ -> play(row) })
        list.layoutManager = LinearLayoutManager(this)
        list.adapter = adapter
        list.itemAnimator = null

        Api.obj("GET", "/device/series/${Api.enc(seriesId)}") { result ->
            result.onSuccess { show(it) }.onFailure { status.text = Api.friendly(it) }
        }
    }

    private fun show(data: JSONObject) {
        data.optStringOrNull("plot")?.let { findViewById<TextView>(R.id.plot).text = it }
        ImageLoader.load(findViewById<ImageView>(R.id.cover), data.optStringOrNull("cover"))
        val rows = ArrayList<Row>()
        episodes.clear()
        val seasons = data.optJSONArray("seasons")
        if (seasons != null) {
            for (s in 0 until seasons.length()) {
                val season = seasons.optJSONObject(s) ?: continue
                val seasonNo = season.optString("season")
                rows.add(Row("Säsong $seasonNo", key = "season:$seasonNo", header = true))
                val eps = season.optJSONArray("episodes") ?: continue
                for (e in 0 until eps.length()) {
                    val ep = eps.optJSONObject(e) ?: continue
                    val num = ep.optStringOrNull("num") ?: "${e + 1}"
                    val title = ep.optStringOrNull("title") ?: "Avsnitt $num"
                    val episode = Episode(ep.optString("id"), "$seriesName S${seasonNo}E$num", ep.optStringOrNull("ext") ?: "mp4")
                    episodes.add(episode)
                    val sub = listOfNotNull(ep.optStringOrNull("duration"), ep.optStringOrNull("plot")).joinToString(" · ")
                    rows.add(Row("$num. $title", subtitle = sub, key = "ep:${episode.id}", payload = episode))
                }
            }
        }
        if (episodes.isEmpty()) {
            status.text = "Inga avsnitt hittades."
            return
        }
        status.visibility = View.GONE
        adapter.submit(rows)
        Focus.first(list)
    }

    private fun play(row: Row) {
        val episode = row.payload as? Episode ?: return
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
        )
    }
}
