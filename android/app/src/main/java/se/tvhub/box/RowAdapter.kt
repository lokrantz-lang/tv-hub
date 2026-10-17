package se.tvhub.box

import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.ImageView
import android.widget.TextView
import androidx.recyclerview.widget.RecyclerView

/** Lista för fjärrkontroll: fokus följer markeringen, OK klickar, håll in OK = långt tryck. */
class RowAdapter(
    private val onClick: (Row, Int) -> Unit,
    private val onFocus: ((Row, Int) -> Unit)? = null,
    private val onLongClick: ((Row, Int) -> Unit)? = null,
) : RecyclerView.Adapter<RowAdapter.Holder>() {

    var rows: List<Row> = emptyList()
        private set

    var selectedKey: String? = null
        private set

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

    override fun getItemViewType(position: Int): Int = if (rows[position].header) 1 else 0

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): Holder {
        val layout = if (viewType == 1) R.layout.row_header else R.layout.row_item
        return Holder(LayoutInflater.from(parent.context).inflate(layout, parent, false))
    }

    override fun onBindViewHolder(holder: Holder, position: Int) {
        val row = rows[position]
        holder.title?.text = row.title
        if (row.header) return

        holder.subtitle?.let { view ->
            if (row.subtitle.isNullOrEmpty()) {
                view.visibility = View.GONE
            } else {
                view.visibility = View.VISIBLE
                view.text = row.subtitle
            }
        }
        holder.star?.visibility = if (row.star) View.VISIBLE else View.GONE
        holder.icon?.let { view ->
            if (row.iconSpace || row.icon != null) {
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
        holder.itemView.setOnFocusChangeListener { _, hasFocus ->
            if (hasFocus) {
                val p = holder.bindingAdapterPosition
                if (p != RecyclerView.NO_POSITION) onFocus?.invoke(rows[p], p)
            }
        }
    }
}
