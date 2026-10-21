package se.tvhub.box

import android.app.Activity
import android.app.Dialog
import android.text.Editable
import android.text.TextWatcher
import android.view.Gravity
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.view.Window
import android.view.WindowManager
import android.widget.EditText
import android.widget.TextView
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView

/**
 * Lista nerifrån med sökruta: välj kategori på telefonen utan att svepa i en lång rad.
 * Raderna överst (pinned) syns alltid; resten filtreras med sökrutan.
 */
object Sheet {
    data class Entry(val key: String, val title: String, val detail: String? = null, val payload: Any? = null)

    fun choose(activity: Activity, title: String, pinned: List<Entry>, entries: List<Entry>, selectedKey: String?,
               onPick: (Entry) -> Unit) {
        val dialog = Dialog(activity)
        dialog.requestWindowFeature(Window.FEATURE_NO_TITLE)
        val root = LayoutInflater.from(activity).inflate(R.layout.sheet_list, null)
        root.findViewById<TextView>(R.id.title).text = title
        val query = root.findViewById<EditText>(R.id.query)
        val list = root.findViewById<RecyclerView>(R.id.list)
        val adapter = Rows(selectedKey) { entry ->
            dialog.dismiss()
            onPick(entry)
        }
        list.layoutManager = LinearLayoutManager(activity)
        list.adapter = adapter
        fun filter(text: String) {
            val q = text.trim().lowercase()
            adapter.submit(if (q.isEmpty()) pinned + entries else entries.filter { it.title.lowercase().contains(q) })
        }
        filter("")
        query.visibility = if (entries.size > 8) View.VISIBLE else View.GONE
        query.addTextChangedListener(object : TextWatcher {
            override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) {}
            override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) {}
            override fun afterTextChanged(s: Editable?) = filter(s?.toString() ?: "")
        })
        dialog.setContentView(root)
        dialog.window?.let { w ->
            w.setGravity(Gravity.BOTTOM)
            w.setLayout(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
            w.setBackgroundDrawableResource(android.R.color.transparent)
            w.addFlags(WindowManager.LayoutParams.FLAG_DIM_BEHIND)
            w.setDimAmount(0.55f)
            w.setSoftInputMode(WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE)
        }
        dialog.setCanceledOnTouchOutside(true)
        dialog.show()
        val index = (pinned + entries).indexOfFirst { it.key == selectedKey }
        if (index > 3) list.scrollToPosition(index)
    }

    private class Rows(private val selected: String?, private val onPick: (Entry) -> Unit) :
        RecyclerView.Adapter<Rows.Holder>() {
        private var items: List<Entry> = emptyList()

        class Holder(view: View) : RecyclerView.ViewHolder(view) {
            val title: TextView = view.findViewById(R.id.title)
            val detail: TextView = view.findViewById(R.id.subtitle)
        }

        fun submit(list: List<Entry>) {
            items = list
            notifyDataSetChanged()
        }

        override fun getItemCount() = items.size

        override fun onCreateViewHolder(parent: ViewGroup, viewType: Int) =
            Holder(LayoutInflater.from(parent.context).inflate(R.layout.sheet_row, parent, false))

        override fun onBindViewHolder(holder: Holder, position: Int) {
            val entry = items[position]
            holder.title.text = if (entry.key == selected) "✓  ${entry.title}" else entry.title
            holder.detail.text = entry.detail ?: ""
            holder.itemView.isActivated = entry.key == selected
            holder.itemView.setOnClickListener {
                val p = holder.bindingAdapterPosition
                if (p != RecyclerView.NO_POSITION) onPick(items[p])
            }
        }
    }
}
