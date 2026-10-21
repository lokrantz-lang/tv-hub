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
import android.widget.TextView
import android.widget.Toast
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import org.json.JSONObject

/**
 * Filmkväll: välj vem som tittar och få tio förslag. Samma sida på TV:n och telefonen.
 *
 * TV:n: rad med förslag längst ner, info och bakgrundsbild för det markerade.
 * Telefonen: lista med bild, fakta och motivering.
 * OK/tryck på ett förslag: Spela, Detaljer, Redan sett, Inte i kväll, Aldrig, Vill se.
 * Listan fylls på direkt efter varje val.
 */
class FilmNightActivity : Activity(), CardListener {
    companion object {
        fun start(context: Context, viewerId: Int = 0) {
            val intent = Intent(context, FilmNightActivity::class.java).putExtra("viewer", viewerId)
            if (context !is Activity) intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            context.startActivity(intent)
        }
    }

    private lateinit var prefs: Prefs
    private var pendingViewer: Int? = null
    private lateinit var viewersList: RecyclerView
    private lateinit var filtersList: RecyclerView
    private lateinit var list: RecyclerView
    private lateinit var status: TextView
    private var backdrop: ImageView? = null
    private var infoTitle: TextView? = null
    private var infoMeta: TextView? = null
    private var infoReason: TextView? = null
    private var infoPlot: TextView? = null
    private var mobile = false

    private val viewerAdapter = Chips { pickViewer(it) }
    private val filterAdapter = Chips { toggleFilter(it) }
    private val cardAdapter = CardAdapter(this)
    private val rowAdapter = Rows()

    private var viewerId: Int? = null
    private var kind = "vod"
    private var anim = ""          // "" = som tittarläget, any, only, none
    private var shortOnly = false
    private var newOnly = false
    private var high = false
    private var swedish = false
    private var kids: Boolean? = null  // null = som tittarläget
    private var items: List<JSONObject> = emptyList()
    private var token = 0
    private var focusIndex = 0
    private var loaded = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        prefs = Prefs(this)
        Api.init(prefs)
        Look.init(this)
        FormFactor.portraitIfMobile(this)
        mobile = FormFactor.isMobile(this)
        Fonts.install(this)
        setContentView(R.layout.activity_night)
        viewersList = findViewById(R.id.viewers)
        filtersList = findViewById(R.id.filters)
        list = findViewById(R.id.list)
        status = findViewById(R.id.status)
        backdrop = findViewById(R.id.backdrop)
        infoTitle = findViewById(R.id.info_title)
        infoMeta = findViewById(R.id.info_meta)
        infoReason = findViewById(R.id.info_reason)
        infoPlot = findViewById(R.id.info_plot)

        viewersList.layoutManager = LinearLayoutManager(this, LinearLayoutManager.HORIZONTAL, false)
        viewersList.adapter = viewerAdapter
        viewersList.itemAnimator = null
        filtersList.layoutManager = LinearLayoutManager(this, LinearLayoutManager.HORIZONTAL, false)
        filtersList.adapter = filterAdapter
        filtersList.itemAnimator = null
        list.itemAnimator = null
        if (mobile) {
            list.layoutManager = LinearLayoutManager(this)
            list.adapter = rowAdapter
        } else {
            list.layoutManager = LinearLayoutManager(this, LinearLayoutManager.HORIZONTAL, false)
            list.adapter = cardAdapter
        }
        viewerId = prefs.activeViewer
        // Röststyrning: "Filmkväll med döttrarna" väljer tittarläget direkt.
        intent.getIntExtra("viewer", 0).takeIf { it > 0 }?.let {
            viewerId = it
            pendingViewer = it
        }
        status.text = "Hämtar förslag…"
        renderFilters()
    }

    override fun onResume() {
        super.onResume()
        load(keepFocus = loaded)
        Taste.promptPending(this)
    }

    override fun dispatchTouchEvent(ev: MotionEvent): Boolean {
        if (ev.action == MotionEvent.ACTION_DOWN) prefs.touch()
        return super.dispatchTouchEvent(ev)
    }

    // ---------------------------------------------------------------- data

    private fun query(): String {
        val q = StringBuilder("kind=$kind&limit=10")
        viewerId?.let { q.append("&viewer=").append(it) }
        if (anim.isNotEmpty()) q.append("&anim=").append(anim)
        if (shortOnly) q.append("&max=120")
        if (newOnly) q.append("&new=1")
        if (high) q.append("&high=1")
        if (swedish) q.append("&swe=1")
        kids?.let { q.append("&kids=").append(if (it) 1 else 0) }
        return q.toString()
    }

    private fun load(keepFocus: Boolean = false) {
        val t = ++token
        Api.obj("GET", "/device/night?${query()}") { result ->
            if (t != token || isFinishing) return@obj
            result.onSuccess { data -> show(data, keepFocus) }.onFailure {
                status.text = Api.friendly(it)
                if (!loaded) viewersList.requestFocus()
            }
        }
    }

    private fun show(data: JSONObject, keepFocus: Boolean) {
        val viewers = Taste.parseViewers(data.optJSONArray("viewers"))
        pendingViewer?.let { id ->
            pendingViewer = null
            viewers.firstOrNull { it.id == id }?.let { prefs.setActiveViewer(it.id, it.name) }
        }
        val v = data.optJSONObject("viewer")
        viewerAdapter.submit(viewers.map { Chip("v:${it.id}", it.name, it.id == (viewerId ?: -1)) })
        val filters = data.optJSONObject("filters")
        if (kids == null && filters != null) kidsDefault = filters.optBoolean("kids")
        renderFilters()
        val arr = data.optJSONArray("items")
        val out = ArrayList<JSONObject>()
        if (arr != null) for (i in 0 until arr.length()) arr.optJSONObject(i)?.let { out.add(it) }
        items = out
        val who = v?.optString("name") ?: ""
        val basis = data.optInt("basis")
        status.text = when {
            viewerId == null -> "Vem tittar? Välj ovan, så sparas tummar och sett på rätt person." +
                (if (out.isEmpty()) "" else "  Visar förslag för $who.")
            out.isEmpty() -> "Inga förslag med de här valen. Prova att slå av något."
            basis < 20 -> "Förslag för $who. De blir bättre ju fler tummar ni ger ($basis hittills)."
            data.optBoolean("ai") -> "Förslag för $who, med AI."
            else -> "Förslag för $who."
        }
        if (mobile) {
            rowAdapter.notifyDataSetChanged()
        } else {
            cardAdapter.submit(out.map { Card.poster(Item.from(it), it.optString("reason")) })
        }
        val first = !loaded
        loaded = true
        if (out.isNotEmpty()) {
            val target = if (keepFocus) focusIndex.coerceAtMost(out.size - 1) else 0
            if (!mobile && first && viewerId == null) {
                // Första gången i kväll: börja på "vem tittar".
                viewersList.post { viewersList.getChildAt(0)?.requestFocus() }
            } else if (!mobile && (first || keepFocus)) {
                Focus.position(list, target)
            }
            showInfo(out[target])
        } else {
            showInfo(null)
            if (first && !mobile) viewersList.post { viewersList.getChildAt(0)?.requestFocus() }
        }
    }

    private var kidsDefault = false

    private fun showInfo(item: JSONObject?) {
        infoTitle?.text = item?.optString("name") ?: ""
        infoMeta?.text = item?.let { listOfNotNull(it.optString("facts").ifEmpty { null },
            if (it.optInt("versions") > 1) "${it.optInt("versions")} versioner" else null).joinToString("   ·   ") } ?: ""
        infoReason?.text = item?.optString("reason") ?: ""
        infoPlot?.text = item?.optStringOrNull("plot") ?: ""
        backdrop?.let { view ->
            val url = item?.optStringOrNull("backdrop") ?: item?.optStringOrNull("icon")
            if (url == null) view.setImageDrawable(null) else ImageLoader.load(view, url, ImageLoader.BACKDROP, fade = true)
        }
    }

    // ---------------------------------------------------------------- chips

    private data class Chip(val key: String, val label: String, val on: Boolean)

    private inner class Chips(private val onPick: (Chip) -> Unit) : RecyclerView.Adapter<Chips.Holder>() {
        var chips: List<Chip> = emptyList()
            private set

        inner class Holder(val text: TextView) : RecyclerView.ViewHolder(text)

        fun submit(list: List<Chip>) {
            chips = list
            notifyDataSetChanged()
        }

        override fun getItemCount() = chips.size

        override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): Holder {
            val layout = if (mobile) R.layout.chip_mobile else R.layout.chip_m
            return Holder(LayoutInflater.from(parent.context).inflate(layout, parent, false) as TextView)
        }

        override fun onBindViewHolder(holder: Holder, position: Int) {
            val chip = chips[position]
            holder.text.text = if (chip.on && !mobile) "✓  ${chip.label}" else chip.label
            holder.text.isSelected = chip.on
            holder.text.isActivated = chip.on
            holder.text.setOnClickListener {
                val p = holder.bindingAdapterPosition
                if (p != RecyclerView.NO_POSITION) onPick(chips[p])
            }
        }
    }

    private fun renderFilters() {
        val kidsOn = kids ?: kidsDefault
        val animLabel = when (anim) {
            "only" -> "Bara animerat"
            "none" -> "Inget animerat"
            else -> "Animerat: med"
        }
        filterAdapter.submit(listOf(
            Chip("kind:vod", "Film", kind == "vod"),
            Chip("kind:series", "Serie", kind == "series"),
            Chip("kids", "Barnvänligt", kidsOn),
            Chip("anim", animLabel, anim == "only" || anim == "none"),
            Chip("short", "Kortare än 2 h", shortOnly),
            Chip("new", "Bara nytt", newOnly),
            Chip("high", "Högt betyg", high),
            Chip("swe", "Svenskt", swedish),
        ))
    }

    private fun pickViewer(chip: Chip) {
        val id = chip.key.removePrefix("v:").toIntOrNull() ?: return
        viewerId = id
        prefs.setActiveViewer(id, chip.label)
        kids = null
        Toast.makeText(this, "${chip.label} tittar i kväll", Toast.LENGTH_SHORT).show()
        viewerAdapter.submit(viewerAdapter.chips.map { it.copy(on = it.key == chip.key) })
        load()
    }

    private fun toggleFilter(chip: Chip) {
        when (chip.key) {
            "kind:vod" -> kind = "vod"
            "kind:series" -> kind = "series"
            "kids" -> kids = !(kids ?: kidsDefault)
            "anim" -> anim = when (anim) {
                "", "any" -> "only"
                "only" -> "none"
                else -> "any"
            }
            "short" -> shortOnly = !shortOnly
            "new" -> newOnly = !newOnly
            "high" -> high = !high
            "swe" -> swedish = !swedish
        }
        renderFilters()
        load()
    }

    // ---------------------------------------------------------------- förslagen

    override fun onCardClick(card: Card, shelf: Shelf?) {
        val item = card.payload as? Item ?: return
        val index = items.indexOfFirst { it.optString("id") == item.id }
        if (index >= 0) choose(index)
    }

    override fun onCardFocus(card: Card, shelf: Shelf?, shelfIndex: Int, cardIndex: Int) {
        focusIndex = cardIndex
        items.getOrNull(cardIndex)?.let { showInfo(it) }
    }

    override fun onCardLongClick(card: Card, shelf: Shelf?) = onCardClick(card, shelf)

    private fun choose(index: Int) {
        val o = items.getOrNull(index) ?: return
        focusIndex = index
        val item = Item.from(o)
        val options = ArrayList<Pair<String, () -> Unit>>()
        if (item.type == "series") {
            options.add("▶  Avsnitt" to { SeriesActivity.start(this, item.id, item.name) })
        } else {
            options.add("▶  Spela" to { DetailActivity.start(this, item.id, item.name, autoPlay = true) })
            options.add("Detaljer" to { DetailActivity.start(this, item.id, item.name) })
        }
        options.add("Redan sett" to { alreadySeen(item) })
        options.add("Inte i kväll" to { Taste.send(this, item.type, item.id, "tonight", viewer = viewerId, done = { load(true) }) })
        options.add("Aldrig" to { Taste.send(this, item.type, item.id, "never", viewer = viewerId,
            toast = "Föreslås inte igen", done = { load(true) }) })
        options.add("☆  Vill se" to { Taste.send(this, item.type, item.id, "wish", toast = "Tillagd på Vill se") })
        Look.choose(this, item.name, options.map { it.first }) { options[it].second() }
    }

    /** Redan sett: fråga också vad ni tyckte, det gör nästa förslag bättre. */
    private fun alreadySeen(item: Item) {
        val labels = Taste.RATINGS.map { it.second } + "Vet inte / minns inte"
        Look.choose(this, "Vad tyckte ni om ${item.name}?", labels) { which ->
            if (which < Taste.RATINGS.size) {
                Taste.send(this, item.type, item.id, "rate", value = Taste.RATINGS[which].first, viewer = viewerId,
                    seen = true, done = { load(true) })
            } else {
                Taste.send(this, item.type, item.id, "seen", viewer = viewerId, done = { load(true) })
            }
        }
    }

    /** Telefonens lista. */
    private inner class Rows : RecyclerView.Adapter<Rows.Holder>() {
        inner class Holder(view: View) : RecyclerView.ViewHolder(view) {
            val art: ImageView = view.findViewById(R.id.art)
            val title: TextView = view.findViewById(R.id.title)
            val subtitle: TextView = view.findViewById(R.id.subtitle)
            val reason: TextView = view.findViewById(R.id.reason)
            val plot: TextView = view.findViewById(R.id.plot)
        }

        override fun getItemCount() = items.size

        override fun onCreateViewHolder(parent: ViewGroup, viewType: Int) =
            Holder(LayoutInflater.from(parent.context).inflate(R.layout.row_night_m, parent, false))

        override fun onBindViewHolder(holder: Holder, position: Int) {
            val o = items[position]
            holder.title.text = o.optString("name")
            holder.subtitle.text = listOfNotNull(o.optString("facts").ifEmpty { null },
                if (o.optInt("versions") > 1) "${o.optInt("versions")} versioner" else null).joinToString(" · ")
            holder.reason.text = o.optString("reason")
            holder.plot.text = o.optStringOrNull("plot") ?: ""
            holder.plot.visibility = if (holder.plot.text.isEmpty()) View.GONE else View.VISIBLE
            ImageLoader.load(holder.art, o.optStringOrNull("icon"))
            holder.itemView.setOnClickListener {
                val p = holder.bindingAdapterPosition
                if (p != RecyclerView.NO_POSITION) choose(p)
            }
        }
    }
}
