package se.tvhub.box

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.view.LayoutInflater
import android.view.MotionEvent
import android.view.View
import android.view.ViewGroup
import android.widget.ImageView
import android.widget.ProgressBar
import android.widget.TextView
import android.widget.Toast
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import org.json.JSONObject

/**
 * Telefonen: en lång lista.
 *  - Nytt tillagt: filmer, serier och kanaler som dykt upp i listorna, per dag (Idag, Igår, 18 okt …).
 *  - Visa alla: hela innehållet i en rad på Hem (tryck på radens rubrik).
 */
class MListActivity : Activity() {
    companion object {
        /** Raden som ska visas med "Visa alla" (sätts av Hem precis innan). */
        var shelfItems: List<Item> = emptyList()

        fun startNew(context: Context, type: String = "all") {
            context.startActivity(Intent(context, MListActivity::class.java).putExtra("mode", "new").putExtra("type", type))
        }

        /** Nytt per dag: titlarna i en kategori som dök upp en viss dag. */
        fun startDay(context: Context, title: String, day: Long, type: String, categoryId: String) {
            context.startActivity(Intent(context, MListActivity::class.java).putExtra("mode", "day").putExtra("title", title)
                .putExtra("day", day).putExtra("type", type).putExtra("category_id", categoryId))
        }

        fun startShelf(context: Context, title: String, items: List<Item>) {
            shelfItems = items
            context.startActivity(Intent(context, MListActivity::class.java).putExtra("mode", "shelf").putExtra("title", title))
        }
    }

    private lateinit var prefs: Prefs
    private lateinit var status: TextView
    private lateinit var list: RecyclerView
    private val rows = ArrayList<Any>()  // String = rubrik (dag), Item = titel
    private val adapter = Rows()
    private var type = "all"
    private var mode = "new"
    private var token = 0
    private val chipAdapter = Chips()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        prefs = Prefs(this)
        Api.init(prefs)
        Look.init(this)
        FormFactor.portraitIfMobile(this)
        Fonts.install(this)
        setContentView(R.layout.activity_mlist)
        status = findViewById(R.id.status)
        list = findViewById(R.id.list)
        list.layoutManager = LinearLayoutManager(this)
        list.adapter = adapter
        list.itemAnimator = null
        mode = intent.getStringExtra("mode") ?: "new"
        type = intent.getStringExtra("type") ?: "all"
        val title = findViewById<TextView>(R.id.title)
        if (!FormFactor.isMobile(this)) {
            // TV: luft runt kanterna (TV-apparater skär ofta av lite).
            val pad = Look.dp(this, 40)
            findViewById<View>(android.R.id.content).setPadding(pad, pad / 2, pad, pad / 2)
        }
        if (mode == "shelf") {
            title.text = intent.getStringExtra("title") ?: ""
            rows.addAll(shelfItems)
            status.text = "${shelfItems.size} st"
            adapter.notifyDataSetChanged()
            focusFirst()
        } else if (mode == "day") {
            title.text = intent.getStringExtra("title") ?: "Nytt"
            loadDay()
        } else {
            title.text = "Nytt tillagt"
            val chips = findViewById<RecyclerView>(R.id.chips)
            chips.visibility = View.VISIBLE
            chips.layoutManager = LinearLayoutManager(this, LinearLayoutManager.HORIZONTAL, false)
            chips.adapter = chipAdapter
            loadNew()
        }
    }

    override fun dispatchTouchEvent(ev: MotionEvent): Boolean {
        if (ev.action == MotionEvent.ACTION_DOWN) prefs.touch()
        return super.dispatchTouchEvent(ev)
    }

    override fun onResume() {
        super.onResume()
        Taste.promptPending(this)
    }

    private fun focusFirst() {
        if (!FormFactor.isMobile(this) && rows.isNotEmpty()) Focus.position(list, rows.indexOfFirst { it is Item }.coerceAtLeast(0))
    }

    private fun loadDay() {
        status.text = "Hämtar…"
        val q = "type=${Api.enc(type)}&category_id=${Api.enc(intent.getStringExtra("category_id") ?: "")}" +
            "&day=${intent.getLongExtra("day", 0L)}&limit=500"
        Api.obj("GET", "/device/new?$q") { result ->
            if (isFinishing) return@obj
            result.onSuccess { data ->
                val items = Item.list(data.optJSONArray("items"))
                rows.clear()
                rows.addAll(items)
                adapter.notifyDataSetChanged()
                status.text = if (items.isEmpty()) "Inget här längre (titlarna kan ha tagits bort hos Toucan)." else "${items.size} st"
                focusFirst()
            }.onFailure { status.text = Api.friendly(it) }
        }
    }

    private fun loadNew() {
        chipAdapter.notifyDataSetChanged()
        val t = ++token
        status.text = "Hämtar…"
        Api.obj("GET", "/device/new?type=$type&limit=200") { result ->
            if (t != token || isFinishing) return@obj
            result.onSuccess { data ->
                val items = Item.list(data.optJSONArray("items"))
                rows.clear()
                var lastDay: String? = null
                for (item in items) {
                    val day = item.added?.let { dayLabel(it) } ?: "Okänt datum"
                    if (day != lastDay) {
                        rows.add(day)
                        lastDay = day
                    }
                    rows.add(item)
                }
                adapter.notifyDataSetChanged()
                list.scrollToPosition(0)
                focusFirst()
                status.text = if (items.isEmpty()) {
                    "Inget nytt ännu. Här visas det som dykt upp i listorna sedan TV Hub började hålla koll. Listorna uppdateras var 12:e timme."
                } else {
                    "${items.size} titlar, nyaste först"
                }
            }.onFailure { status.text = Api.friendly(it) }
        }
    }

    /** "Ny idag" -> "Idag", "Ny 18 okt" -> "18 okt". */
    private fun dayLabel(seconds: Long): String =
        Format.addedLabel(seconds).removePrefix("Ny ").replaceFirstChar { it.uppercase() }

    private val chipKeys = listOf("all" to "Allt", "vod" to "Filmer", "series" to "Serier", "live" to "Kanaler")

    private inner class Chips : RecyclerView.Adapter<Chips.Holder>() {
        inner class Holder(val text: TextView) : RecyclerView.ViewHolder(text)

        override fun getItemCount() = chipKeys.size

        override fun onCreateViewHolder(parent: ViewGroup, viewType: Int) =
            Holder(LayoutInflater.from(parent.context).inflate(R.layout.chip_mobile, parent, false) as TextView)

        override fun onBindViewHolder(holder: Holder, position: Int) {
            val (key, label) = chipKeys[position]
            holder.text.text = label
            holder.text.isSelected = key == type
            holder.text.setOnClickListener {
                if (type != key) {
                    type = key
                    loadNew()
                }
            }
        }
    }

    // ---------------------------------------------------------------- listan

    private inner class Rows : RecyclerView.Adapter<RecyclerView.ViewHolder>() {
        inner class Header(view: View) : RecyclerView.ViewHolder(view) {
            val title: TextView = view.findViewById(R.id.title)
        }

        inner class Line(view: View) : RecyclerView.ViewHolder(view) {
            val art: ImageView = view.findViewById(R.id.art)
            val fallback: TextView = view.findViewById(R.id.fallback)
            val title: TextView = view.findViewById(R.id.title)
            val subtitle: TextView = view.findViewById(R.id.subtitle)
            val progress: ProgressBar = view.findViewById(R.id.progress)
            val star: TextView = view.findViewById(R.id.star)
        }

        override fun getItemCount() = rows.size

        override fun getItemViewType(position: Int) = if (rows[position] is String) 1 else 0

        override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): RecyclerView.ViewHolder {
            val inflater = LayoutInflater.from(parent.context)
            return if (viewType == 1) Header(inflater.inflate(R.layout.row_header_m, parent, false))
            else Line(inflater.inflate(R.layout.row_channel_m, parent, false))
        }

        override fun onBindViewHolder(holder: RecyclerView.ViewHolder, position: Int) {
            val row = rows[position]
            if (holder is Header) {
                holder.title.text = row as String
                return
            }
            val item = row as Item
            val h = holder as Line
            h.title.text = item.name
            val added = if (mode == "new" || mode == "day") item.added?.let { Format.addedLabel(it) } else null
            h.subtitle.text = listOfNotNull(item.typeLabel.ifEmpty { null }, item.categoryName, item.facts.ifEmpty { null }, added)
                .joinToString(" · ")
            h.fallback.text = item.name.take(14)
            h.fallback.visibility = View.VISIBLE
            ImageLoader.load(h.art, item.icon) { ok -> if (ok) h.fallback.visibility = View.INVISIBLE }
            h.progress.visibility = View.GONE
            h.star.visibility = if (item.favorite) View.VISIBLE else View.GONE
            h.itemView.setOnClickListener { open(item) }
            h.itemView.setOnLongClickListener {
                options(item)
                true
            }
        }
    }

    private fun open(item: Item) {
        when (item.type) {
            "series" -> SeriesActivity.start(this, item.id, item.name)
            "vod" -> DetailActivity.start(this, item.id, item.name)
            "episode" -> playFavoriteEpisode(this, item)
            else -> {
                val channels = rows.filterIsInstance<Item>().filter { it.type == "live" }
                PlayerActivity.start(
                    this, type = "live", id = item.id, name = item.name, ext = null,
                    queueIds = ArrayList(channels.map { it.id }), queueNames = ArrayList(channels.map { it.name }),
                    queueExts = null, queueIndex = channels.indexOfFirst { it.id == item.id }, icon = item.icon,
                )
            }
        }
    }

    private fun options(item: Item) {
        val options = ArrayList<Pair<String, () -> Unit>>()
        options.add("Öppna" to { open(item) })
        if (item.type == "live") options.add("Programguide" to { GuideActivity.start(this, item.id, item.name) })
        options.add((if (item.favorite) "Ta bort från favoriter" else "Lägg till i favoriter") to {
            val on = !item.favorite
            Api.obj("POST", "/device/favorites", JSONObject().put("type", item.type).put("id", item.id).put("on", on)) { r ->
                r.onSuccess {
                    item.favorite = on
                    adapter.notifyDataSetChanged()
                    Toast.makeText(this, if (on) "★ Favorit" else "Borttagen ur favoriter", Toast.LENGTH_SHORT).show()
                }.onFailure { Toast.makeText(this, Api.friendly(it), Toast.LENGTH_LONG).show() }
            }
        })
        options.addAll(Taste.options(this, item) {})
        options.addAll(BoxSend.options(this, item))
        Look.choose(this, item.name, options.map { it.first }) { options[it].second() }
    }
}
