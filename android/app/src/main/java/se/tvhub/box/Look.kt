package se.tvhub.box

import android.app.Activity
import android.app.ActivityManager
import android.app.AlertDialog
import android.app.Dialog
import android.content.Context
import android.os.Build
import android.view.Gravity
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.view.Window
import android.view.WindowManager
import android.widget.LinearLayout
import android.widget.TextView

/**
 * Det moderna utseendet: gemensamma hjälpare.
 *  - Lätt läge för svagare boxar (Z Alpha): inga skalningsanimationer, mindre bildcache.
 *  - Fokus på kort: skalas upp lite och får vit ram (ramen ligger i m_card_focus).
 *  - Sidopanel från höger i stället för dialogrutor.
 */
object Look {
    /** true på svagare boxar: Android 7 eller lite minne. */
    var lite = false
        private set
    private var initialized = false

    fun init(context: Context) {
        if (initialized) return
        initialized = true
        val am = context.getSystemService(Context.ACTIVITY_SERVICE) as ActivityManager
        lite = Build.VERSION.SDK_INT < 26 || am.isLowRamDevice || am.memoryClass < 160
    }

    fun dp(context: Context, value: Int): Int = (value * context.resources.displayMetrics.density + 0.5f).toInt()

    /** Kortet växer lite när det får fokus (som på Apple TV). Avstängt i lätt läge. */
    fun focusGrow(view: View, scale: Float = 1.07f, extra: ((Boolean) -> Unit)? = null) {
        view.setOnFocusChangeListener { v, hasFocus ->
            extra?.invoke(hasFocus)
            if (lite) return@setOnFocusChangeListener
            val target = if (hasFocus) scale else 1f
            v.animate().scaleX(target).scaleY(target).setDuration(150).start()
            v.elevation = if (hasFocus) 12f else 0f
        }
    }

    /**
     * Val i en lista. Modernt: sidopanel från höger. Klassiskt: Androids dialogruta som tidigare.
     * checked >= 0 markerar det valda alternativet med en bock.
     */
    fun choose(
        activity: Activity,
        title: String,
        labels: List<String>,
        checked: Int = -1,
        subtitle: String? = null,
        onPick: (Int) -> Unit,
    ) {
        if (!Prefs(activity).modern) {
            val builder = AlertDialog.Builder(activity).setTitle(title)
            if (subtitle != null) builder.setMessage(subtitle)
            if (checked >= 0) {
                builder.setSingleChoiceItems(labels.toTypedArray(), checked) { d, which ->
                    d.dismiss()
                    onPick(which)
                }
            } else if (subtitle == null) {
                builder.setItems(labels.toTypedArray()) { _, which -> onPick(which) }
            } else {
                // Meddelande och knappar kan inte kombineras med en lista i AlertDialog.
                labels.forEachIndexed { index, label ->
                    when (index) {
                        0 -> builder.setPositiveButton(label) { _, _ -> onPick(0) }
                        1 -> builder.setNegativeButton(label) { _, _ -> onPick(1) }
                        2 -> builder.setNeutralButton(label) { _, _ -> onPick(2) }
                    }
                }
            }
            builder.show()
            return
        }
        val dialog = Dialog(activity)
        dialog.requestWindowFeature(Window.FEATURE_NO_TITLE)
        val root = LayoutInflater.from(activity).inflate(R.layout.panel_m, null)
        root.findViewById<TextView>(R.id.title).text = title
        root.findViewById<TextView>(R.id.subtitle).let {
            if (subtitle != null) {
                it.text = subtitle
                it.visibility = View.VISIBLE
            }
        }
        val items = root.findViewById<LinearLayout>(R.id.items)
        var first: View? = null
        labels.forEachIndexed { index, label ->
            val item = LayoutInflater.from(activity).inflate(R.layout.panel_item_m, items, false) as TextView
            item.text = if (index == checked) "✓   $label" else label
            item.setOnClickListener {
                dialog.dismiss()
                onPick(index)
            }
            items.addView(item)
            if (first == null || index == checked) first = item
        }
        dialog.setContentView(root)
        val mobile = FormFactor.isMobile(activity)
        dialog.window?.let { w ->
            if (mobile) {
                // Telefonen: panel nerifrån, hela bredden. Tryck utanför stänger.
                w.setGravity(Gravity.BOTTOM)
                w.setLayout(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
                dialog.setCanceledOnTouchOutside(true)
            } else {
                w.setGravity(Gravity.END)
                w.setLayout(dp(activity, 440), ViewGroup.LayoutParams.MATCH_PARENT)
            }
            w.setBackgroundDrawableResource(android.R.color.transparent)
            w.addFlags(WindowManager.LayoutParams.FLAG_DIM_BEHIND)
            w.setDimAmount(0.55f)
        }
        dialog.show()
        if (!mobile) first?.requestFocus()
    }

    /** Fråga med två eller tre svar (Ja/Avbryt). Första alternativet är standard. */
    fun confirm(activity: Activity, title: String, message: String, yes: String, onYes: () -> Unit) {
        choose(activity, title, listOf(yes, "Avbryt"), subtitle = message) { which -> if (which == 0) onYes() }
    }

    /** Bara information, med en OK-knapp. */
    fun message(activity: Activity, title: String, message: String) {
        choose(activity, title, listOf("OK"), subtitle = message) {}
    }
}
