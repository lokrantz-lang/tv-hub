package se.tvhub.box

import android.view.KeyEvent
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.ImageView
import android.widget.ProgressBar
import android.widget.TextView
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView

/** Det som visas i den stora ytan överst när ett kort har fokus. */
data class Hero(
    val kicker: String?,
    val title: String,
    val meta: String?,
    val plot: String?,
    val backdrop: String?,
    val progress: Int = -1,
)

/** Ett kort i en rad: affisch, brett kort (Fortsätt titta) eller kanal. */
data class Card(
    val kind: Int,
    val key: String,
    val title: String,
    val subtitle: String? = null,
    val image: String? = null,
    val progress: Int = -1,
    val badge: String? = null,
    val payload: Any? = null,
    val hero: Hero? = null,
) {
    companion object {
        const val POSTER = 0
        const val WIDE = 1
        const val CHANNEL = 2

        fun poster(item: Item, kicker: String? = null): Card = Card(
            kind = POSTER,
            key = "${item.type}:${item.id}",
            title = item.name,
            image = item.icon,
            badge = if (item.watched) "✓" else null,
            payload = item,
            hero = Hero(
                kicker = kicker ?: item.typeLabel,
                title = item.name,
                meta = listOfNotNull(item.facts.ifEmpty { null }, item.genre).joinToString("   ·   ").ifEmpty { item.categoryName },
                plot = item.plot,
                backdrop = item.backdrop ?: item.icon,
            ),
        )

        fun channel(item: Item, now: String? = null, progress: Int = -1): Card = Card(
            kind = CHANNEL,
            key = "live:${item.id}",
            title = now ?: item.name,
            subtitle = item.name,
            image = item.icon,
            progress = progress,
            payload = item,
            hero = Hero(
                kicker = "Kanal" + (item.categoryName?.let { " · $it" } ?: ""),
                title = item.name,
                meta = now,
                plot = null,
                backdrop = null,
                progress = progress,
            ),
        )

        fun resume(c: ContinueItem): Card {
            val progress = if (c.durationMs > 0) (c.positionMs * 1000 / c.durationMs).toInt() else -1
            val title = c.seriesName ?: c.name
            val sub = (if (c.seriesName != null) "${c.name} · " else "") + Format.remaining(c.positionMs, c.durationMs)
            return Card(
                kind = WIDE,
                key = "continue:${c.type}:${c.id}",
                title = title,
                subtitle = sub,
                image = c.backdrop ?: c.icon,
                progress = progress,
                payload = c,
                hero = Hero("Fortsätt titta", title, sub, null, c.backdrop ?: c.icon, progress),
            )
        }
    }
}

data class Shelf(val key: String, val title: String, val cards: List<Card>)

interface CardListener {
    fun onCardClick(card: Card, shelf: Shelf?)
    fun onCardFocus(card: Card, shelf: Shelf?, shelfIndex: Int, cardIndex: Int)
    fun onCardLongClick(card: Card, shelf: Shelf?) {}

    /** Telefonen: tryck på radens rubrik = Visa alla. */
    fun onShelfTitle(shelf: Shelf) {}
}

/** Kort i en vågrät rad eller i ett rutnät. */
class CardAdapter(
    private val listener: CardListener,
    var shelf: Shelf? = null,
    var shelfIndex: Int = 0,
) : RecyclerView.Adapter<CardAdapter.Holder>() {
    var cards: List<Card> = emptyList()
        private set

    /** Upp från första raden i ett rutnät får gå vidare (till kategorierna); annars false. */
    var onEdgeKey: ((Int, Int) -> Boolean)? = null

    /** Telefonens rutnät: korten fyller kolumnen (bredd, höjd i px). Null = layoutens storlek. */
    var itemSize: Pair<Int, Int>? = null

    /** TV: hur affischer visas (se [ViewStyle]). DEFAULT = som förut (telefonen har egna layouter). */
    var style = ViewStyle.DEFAULT

    class Holder(view: View) : RecyclerView.ViewHolder(view) {
        val art: ImageView? = view.findViewById(R.id.art)
        val fallback: TextView? = view.findViewById(R.id.fallback)
        val title: TextView? = view.findViewById(R.id.title)
        val subtitle: TextView? = view.findViewById(R.id.subtitle)
        val badge: TextView? = view.findViewById(R.id.badge)
        val progress: ProgressBar? = view.findViewById(R.id.progress)
        val plot: TextView? = view.findViewById(R.id.plot)
    }

    fun submit(list: List<Card>) {
        cards = list
        notifyDataSetChanged()
    }

    fun append(more: List<Card>) {
        if (more.isEmpty()) return
        val start = cards.size
        cards = cards + more
        notifyItemRangeInserted(start, more.size)
    }

    fun replace(index: Int, card: Card) {
        if (index !in cards.indices) return
        cards = cards.toMutableList().also { it[index] = card }
        notifyItemChanged(index)
    }

    override fun getItemCount() = cards.size

    override fun getItemViewType(position: Int): Int {
        val kind = cards[position].kind
        // Lista: affischer och kanaler blir rader.
        if (style == ViewStyle.LIST && kind != Card.WIDE) return LIST_ROW
        return kind
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): Holder {
        val layout = when (viewType) {
            LIST_ROW -> R.layout.card_list_m
            Card.WIDE -> R.layout.card_wide_m
            Card.CHANNEL -> R.layout.card_channel_m
            else -> if (style == ViewStyle.TITLE) R.layout.card_poster_t else R.layout.card_poster_m
        }
        val view = LayoutInflater.from(parent.context).inflate(layout, parent, false)
        itemSize?.let { (w, h) ->
            val lp = view.layoutParams
            lp.width = w
            lp.height = h
            (lp as? ViewGroup.MarginLayoutParams)?.let { it.marginEnd = 0; it.bottomMargin = 0 }
            view.layoutParams = lp
        }
        return Holder(view)
    }

    override fun onBindViewHolder(holder: Holder, position: Int) {
        val card = cards[position]
        val item = card.payload as? Item
        val row = getItemViewType(position) == LIST_ROW
        val subtitle = when {
            row && item != null -> listOfNotNull(
                if (item.type == "live") item.num?.toString() else null,
                item.facts.ifEmpty { null }, item.genre, item.categoryName,
            ).joinToString("  ·  ")
            style == ViewStyle.TITLE && card.kind == Card.POSTER && item != null -> item.facts.ifEmpty { item.categoryName ?: "" }
            else -> card.subtitle
        }
        holder.title?.text = if (row && card.kind == Card.CHANNEL) (card.subtitle ?: card.title) else card.title
        holder.subtitle?.let {
            it.text = subtitle ?: ""
            it.visibility = if (subtitle.isNullOrEmpty()) (if (style == ViewStyle.TITLE) View.INVISIBLE else View.GONE) else View.VISIBLE
        }
        holder.plot?.let {
            val text = if (card.kind == Card.CHANNEL) card.title.takeIf { t -> t != card.subtitle } else item?.plot
            it.text = text ?: ""
            it.visibility = if (text.isNullOrBlank()) View.GONE else View.VISIBLE
        }
        holder.fallback?.text = if (card.kind == Card.CHANNEL) (card.subtitle ?: card.title) else card.title
        holder.fallback?.visibility = View.VISIBLE
        (card.payload as? MyPrograms.Program)?.let { prog ->
            // Mina program: namnet stort, vad som spelas (t.ex. "Idag 19:30 · fortsätt") under.
            holder.title?.text = if (row) prog.title else listOf(prog.title, prog.subtitle).filter { it.isNotEmpty() }.joinToString("  ·  ")
            holder.subtitle?.let { it.text = prog.subtitle; it.visibility = if (prog.subtitle.isEmpty()) View.GONE else View.VISIBLE }
            holder.fallback?.text = prog.title
            holder.plot?.visibility = View.GONE
        }
        (card.payload as? Sports.Event)?.let { e ->
            // Sport: vad och när (logon visar kanalen).
            val time = if (e.live) "● ${e.whenText}" else e.whenText
            holder.title?.text = if (row) e.title else "$time  ·  ${e.title}"
            holder.subtitle?.let { it.text = "$time  ·  ${e.channel}"; it.visibility = View.VISIBLE }
            holder.fallback?.text = e.channel
            holder.plot?.visibility = View.GONE
        }
        // Platshållare medan listan hämtas.
        holder.itemView.alpha = if (card.key.startsWith("sk:")) 0.35f else 1f
        holder.art?.let { art ->
            val size = if (card.kind == Card.WIDE) 480 else 320
            ImageLoader.load(art, card.image, size, fade = true) { ok ->
                if (ok) holder.fallback?.visibility = View.INVISIBLE
            }
        }
        holder.badge?.let {
            it.text = card.badge ?: ""
            it.visibility = if (card.badge == null) View.GONE else View.VISIBLE
        }
        holder.progress?.let {
            if (card.progress >= 0) {
                it.progress = card.progress
                it.visibility = View.VISIBLE
            } else {
                it.visibility = if (card.kind == Card.CHANNEL) View.INVISIBLE else View.GONE
            }
        }
        val view = holder.itemView
        view.setOnClickListener {
            val p = holder.bindingAdapterPosition
            if (p != RecyclerView.NO_POSITION) listener.onCardClick(cards[p], shelf)
        }
        view.setOnLongClickListener {
            val p = holder.bindingAdapterPosition
            if (p != RecyclerView.NO_POSITION) listener.onCardLongClick(cards[p], shelf)
            true
        }
        view.setOnKeyListener { _, keyCode, event ->
            if (event.action != KeyEvent.ACTION_DOWN) return@setOnKeyListener false
            val p = holder.bindingAdapterPosition
            onEdgeKey?.invoke(p, keyCode) ?: false
        }
        Look.focusGrow(view, if (row) 1.0f else 1.07f) { hasFocus ->
            if (hasFocus) {
                val p = holder.bindingAdapterPosition
                if (p != RecyclerView.NO_POSITION) listener.onCardFocus(cards[p], shelf, shelfIndex, p)
            }
        }
    }
}

/** TV: visningsläge för filmer och serier. Sparas per sida och box. */
object ViewStyle {
    const val DEFAULT = -1
    const val TITLE = 0
    const val IMAGE = 1
    const val LIST = 2

    val LABELS = listOf("Affisch med titel", "Lista", "Bara bild")
    private val ORDER = listOf(TITLE, LIST, IMAGE)

    fun label(style: Int) = LABELS[ORDER.indexOf(style).coerceAtLeast(0)]
    fun fromIndex(index: Int) = ORDER[index]
    fun index(style: Int) = ORDER.indexOf(style).coerceAtLeast(0)
}

private const val LIST_ROW = 10

/** Lodrät lista av rader, varje rad en vågrät lista av kort. */
class ShelfAdapter(private val listener: CardListener) : RecyclerView.Adapter<ShelfAdapter.Holder>() {
    var shelves: List<Shelf> = emptyList()
        private set
    private val pool = RecyclerView.RecycledViewPool()
    private val scroll = HashMap<String, Int>()

    /** Telefonen: rubrikerna går att trycka på ("Visa alla"). */
    var titleClicks = false

    /** TV: affischer med titel under (se [ViewStyle]). */
    var style = ViewStyle.DEFAULT

    class Holder(view: View) : RecyclerView.ViewHolder(view) {
        val title: TextView = view.findViewById(R.id.title)
        val cards: RecyclerView = view.findViewById(R.id.cards)
        var adapter: CardAdapter? = null
    }

    fun submit(list: List<Shelf>) {
        shelves = list
        notifyDataSetChanged()
    }

    fun updateShelf(key: String, shelf: Shelf) {
        val index = shelves.indexOfFirst { it.key == key }
        if (index < 0) return
        shelves = shelves.toMutableList().also { it[index] = shelf }
        notifyItemChanged(index)
    }

    fun insert(shelf: Shelf, at: Int) {
        val index = at.coerceIn(0, shelves.size)
        shelves = shelves.toMutableList().also { it.add(index, shelf) }
        notifyItemInserted(index)
    }

    override fun getItemCount() = shelves.size

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): Holder {
        val holder = Holder(LayoutInflater.from(parent.context).inflate(R.layout.shelf_m, parent, false))
        holder.cards.layoutManager = LinearLayoutManager(parent.context, LinearLayoutManager.HORIZONTAL, false)
        holder.cards.setRecycledViewPool(pool)
        holder.cards.itemAnimator = null
        val adapter = CardAdapter(listener)
        adapter.style = style
        holder.adapter = adapter
        holder.cards.adapter = adapter
        return holder
    }

    override fun onBindViewHolder(holder: Holder, position: Int) {
        val shelf = shelves[position]
        holder.title.text = if (titleClicks && shelf.key != "continue") "${shelf.title}  ›" else shelf.title
        if (titleClicks) holder.title.setOnClickListener {
            val p = holder.bindingAdapterPosition
            if (p != RecyclerView.NO_POSITION) listener.onShelfTitle(shelves[p])
        }
        holder.adapter?.let {
            it.shelf = shelf
            it.shelfIndex = position
            it.submit(shelf.cards)
        }
        // Förhämta bilderna i raden så att de syns direkt när man bläddrar dit.
        shelf.cards.take(12).forEach { ImageLoader.prefetch(it.image, if (it.kind == Card.WIDE) 480 else 320) }
    }
}


/** Spelar ett favoritavsnitt direkt, där man slutade. */
fun playFavoriteEpisode(activity: android.app.Activity, item: Item) {
    PlayerActivity.start(
        activity, type = "episode", id = item.id, name = item.name, ext = item.ext,
        seriesId = item.seriesId, icon = item.icon, autoResume = true,
    )
}

/** Håll in OK på ett kort i Fortsätt titta: fortsätt, eller ta bort det ur raden. */
fun continueOptions(activity: android.app.Activity, c: ContinueItem, open: () -> Unit, removed: () -> Unit) {
    val title = c.seriesName ?: c.name
    val labels = listOf("▶ Fortsätt titta", "Ta bort från Fortsätt titta")
    Look.choose(activity, title, labels) { which ->
        if (which == 0) {
            open()
            return@choose
        }
        val body = org.json.JSONObject().put("type", c.type).put("id", c.id).put("series_id", c.seriesId)
        Api.obj("POST", "/device/continue/remove", body) { result ->
            result.onSuccess {
                android.widget.Toast.makeText(activity, "$title är borttagen ur Fortsätt titta", android.widget.Toast.LENGTH_SHORT).show()
                removed()
            }.onFailure { android.widget.Toast.makeText(activity, Api.friendly(it), android.widget.Toast.LENGTH_LONG).show() }
        }
    }
}
