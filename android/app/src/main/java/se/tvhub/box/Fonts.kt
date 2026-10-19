package se.tvhub.box

import android.app.Activity
import android.content.Context
import android.graphics.Typeface
import android.util.AttributeSet
import android.view.LayoutInflater
import android.view.View
import android.widget.Button
import android.widget.TextView

/**
 * Typsnittet Inter i det moderna utseendet (fungerar även på Android 7, där teckensnitt
 * som resurser inte stöds). Rubriker med fontFamily "sans-serif-medium" får Inter SemiBold.
 */
object Fonts {
    private var regular: Typeface? = null
    private var semibold: Typeface? = null

    private fun load(context: Context) {
        if (regular != null) return
        try {
            regular = Typeface.createFromAsset(context.assets, "fonts/Inter-Regular.otf")
            semibold = Typeface.createFromAsset(context.assets, "fonts/Inter-SemiBold.otf")
        } catch (_: Exception) {
            // Saknas typsnittet används boxens eget.
        }
    }

    /** Anropas före setContentView. Gäller alla vyer som skapas i aktiviteten, även listor och paneler. */
    fun install(activity: Activity) {
        load(activity)
        if (regular == null) return
        val inflater = LayoutInflater.from(activity)
        if (inflater.factory2 != null) return
        inflater.factory2 = object : LayoutInflater.Factory2 {
            override fun onCreateView(parent: View?, name: String, context: Context, attrs: AttributeSet): View? {
                val view: TextView = when (name) {
                    "TextView" -> TextView(context, attrs)
                    "Button" -> Button(context, attrs)
                    else -> return null
                }
                val family = attrs.getAttributeValue("http://schemas.android.com/apk/res/android", "fontFamily")
                view.typeface = if (family == "sans-serif-medium") semibold else regular
                return view
            }

            override fun onCreateView(name: String, context: Context, attrs: AttributeSet): View? = onCreateView(null, name, context, attrs)
        }
    }
}
