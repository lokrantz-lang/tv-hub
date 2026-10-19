package se.tvhub.box

import android.view.KeyEvent
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.ImageView
import android.widget.TextView
import androidx.recyclerview.widget.RecyclerView

/**
 * Lista eller rutnät för fjärrkontroll: fokus följer markeringen, OK klickar,
 * håll in OK = långt tryck. Fokus stannar i listan vid första och sista raden.
 */
class RowAdapter(
    private val onClick: (Row, Int) -> Unit,
    private val onFocus: ((Row, Int) -> Unit)? = null,
    private val onLongClick: ((Row, Int) -> Unit)? = null,
    /** Om upp på första raden ska stanna i listan. */
    private val stopAtTop: () -> Boolean = { true },
    /** Får se knapptryck först. Returnera true för att stoppa dem. */
    private val onKey: ((Row, Int, Int) -> Boolean)? = null,
) : RecyclerView.Adapter<RowAdapter.Holder>() {

    var rows: List<Row> = emptyList()
        private set

    var selectedKey: String? = null
        private set

    /** Antal kolumner när listan visas som rutnät. */
    var columns = 1

    /** Modernt utseende: större rader med vit markering. */
    var modern = false

    /** false = upp/ner vid kanterna får lämna listan (chips och tangenter). */
    var edgeLock = true

    class Holder(view: View) : RecyclerView.ViewHolder(view) {
        val icon: ImageView? = view.findViewById(R.id.icon)
        val title: TextView? = view.findViewById(R.id.title)
        val subtitle: TextView? = view.findViewById(R.id.subtitle)
        val star: TextView? = view.findViewById(R.id.star)
    }

    fun submit(newRows: List<Row>) {
        rows = newRows
        notifyDataSetChanged()
    }

    fun append(more: List<Row>) {
        if (more.isEmpty()) return
        val start = rows.size
        rows = rows + more
        notifyItemRangeInserted(start, more.size)
    }

    fun replace(position: Int, row: Row) {
        if (position !in rows.indices) return
        rows = rows.toMutableList().also { it[position] = row }
        notifyItemChanged(position)
    }

    fun indexOf(key: String?): Int = rows.indexOfFirst { it.key == key }

    fun select(key: String?) {
        val old = indexOf(selectedKey)
        selectedKey = key
        val new = indexOf(key)
        if (old >= 0) notifyItemChanged(old)
        if (new >= 0 && new != old) notifyItemChanged(new)
    }

    override fun getItemCount(): Int = rows.size

    override fun getItemViewType(position: Int): Int = when {
        rows[position].header -> 1
        rows[position].poster -> 2
        else -> 0
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): Holder {
        val layout = when (viewType) {
            1 -> if (modern) R.layout.row_header_m else R.layout.row_header
            2 -> R.layout.cell_poster
            else -> if (modern) R.layout.row_item_m else R.layout.row_item
        }
        return Holder(LayoutInflater.from(parent.context).inflate(layout, parent, false))
    }

    override fun onBindViewHolder(holder: Holder, position: Int) {
        val row = rows[position]
        holder.title?.text = row.title
        if (row.header) return

        holder.subtitle?.let { view ->
            if (row.subtitle.isNullOrEmpty()) {
                view.visibility = if (row.poster) View.INVISIBLE else View.GONE
            } else {
                view.visibility = View.VISIBLE
                view.text = row.subtitle
            }
        }
        holder.star?.visibility = if (row.star) View.VISIBLE else View.GONE
        holder.icon?.let { view ->
            if (row.poster || row.iconSpace || row.icon != null) {
                view.visibility = View.VISIBLE
                ImageLoader.load(view, row.icon)
            } else {
                view.visibility = View.GONE
            }
        }
        holder.itemView.isActivated = row.key == selectedKey

        holder.itemView.setOnClickListener {
            val p = holder.bindingAdapterPosition
            if (p != RecyclerView.NO_POSITION) onClick(rows[p], p)
        }
        holder.itemView.setOnLongClickListener {
            val p = holder.bindingAdapterPosition
            val handler = onLongClick
            if (p != RecyclerView.NO_POSITION && handler != null) {
                handler(rows[p], p)
                true
            } else {
                false
            }
        }
        holder.itemView.setOnKeyListener { _, keyCode, event ->
            if (event.action != KeyEvent.ACTION_DOWN) return@setOnKeyListener false
            val p = holder.bindingAdapterPosition
            if (p == RecyclerView.NO_POSITION) return@setOnKeyListener false
            val custom = onKey
            if (custom != null && custom(rows[p], p, keyCode)) return@setOnKeyListener true
            if (!edgeLock) return@setOnKeyListener false
            val cols = columns.coerceAtLeast(1)
            val lastRowStart = ((rows.size - 1) / cols) * cols
            when (keyCode) {
                // Ner på sista raden (eller upp på första) ska inte hoppa till en annan kolumn.
                KeyEvent.KEYCODE_DPAD_DOWN -> p >= lastRowStart
                KeyEvent.KEYCODE_DPAD_UP -> {
                    val firstRow = if (cols > 1) p < cols else p == rows.indexOfFirst { !it.header }
                    firstRow && stopAtTop()
                }
                else -> false
            }
        }
        holder.itemView.setOnFocusChangeListener { _, hasFocus ->
            if (hasFocus) {
                val p = holder.bindingAdapterPosition
                if (p != RecyclerView.NO_POSITION) onFocus?.invoke(rows[p], p)
            }
        }
    }
}
