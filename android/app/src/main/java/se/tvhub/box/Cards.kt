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

    class Holder(view: View) : RecyclerView.ViewHolder(view) {
        val art: ImageView? = view.findViewById(R.id.art)
        val fallback: TextView? = view.findViewById(R.id.fallback)
        val title: TextView? = view.findViewById(R.id.title)
        val subtitle: TextView? = view.findViewById(R.id.subtitle)
        val badge: TextView? = view.findViewById(R.id.badge)
        val progress: ProgressBar? = view.findViewById(R.id.progress)
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

    override fun getItemViewType(position: Int) = cards[position].kind

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): Holder {
        val layout = when (viewType) {
            Card.WIDE -> R.layout.card_wide_m
            Card.CHANNEL -> R.layout.card_channel_m
            else -> R.layout.card_poster_m
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
        holder.title?.text = card.title
        holder.subtitle?.let {
            it.text = card.subtitle ?: ""
            it.visibility = if (card.subtitle.isNullOrEmpty()) View.GONE else View.VISIBLE
        }
        holder.fallback?.text = if (card.kind == Card.CHANNEL) (card.subtitle ?: card.title) else card.title
        holder.fallback?.visibility = View.VISIBLE
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
        Look.focusGrow(view) { hasFocus ->
            if (hasFocus) {
                val p = holder.bindingAdapterPosition
                if (p != RecyclerView.NO_POSITION) listener.onCardFocus(cards[p], shelf, shelfIndex, p)
            }
        }
    }
}

/** Lodrät lista av rader, varje rad en vågrät lista av kort. */
class ShelfAdapter(private val listener: CardListener) : RecyclerView.Adapter<ShelfAdapter.Holder>() {
    var shelves: List<Shelf> = emptyList()
        private set
    private val pool = RecyclerView.RecycledViewPool()
    private val scroll = HashMap<String, Int>()

    /** Telefonen: rubrikerna går att trycka på ("Visa alla"). */
    var titleClicks = false

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
