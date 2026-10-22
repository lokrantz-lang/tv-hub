package se.tvhub.box

import android.app.Activity
import android.widget.Toast
import org.json.JSONArray
import org.json.JSONObject

/**
 * Filmkväll i appen: tittarlägen, tummar, Redan sett och Vill se.
 * Allt sparas i TV Hub; appen frågar och skickar bara.
 */
object Taste {
    data class Viewer(val id: Int, val name: String, val kids: Boolean)

    @Volatile var viewers: List<Viewer> = emptyList()
        private set

    val RATINGS = listOf(2 to "👍👍  Älskade", 1 to "👍  Bra", -1 to "👎  Inte för oss")

    fun label(value: Int): String = when (value) {
        2 -> "👍👍 Älskade"
        1 -> "👍 Bra"
        -1 -> "👎 Inte för oss"
        else -> "Inget betyg"
    }

    fun parseViewers(array: JSONArray?): List<Viewer> {
        val out = ArrayList<Viewer>()
        if (array == null) return out
        for (i in 0 until array.length()) {
            val o = array.optJSONObject(i) ?: continue
            out.add(Viewer(o.optInt("id"), o.optString("name"), o.optJSONObject("rules")?.optBoolean("kids") ?: false))
        }
        viewers = out
        return out
    }

    fun loadViewers(done: (List<Viewer>) -> Unit) {
        if (viewers.isNotEmpty()) {
            done(viewers)
            return
        }
        Api.arr("/device/viewers") { result -> result.onSuccess { done(parseViewers(it)) }.onFailure { done(emptyList()) } }
    }

    /** rate (value), seen, tonight, never, wish, unwish. */
    fun send(activity: Activity, kind: String, id: String, action: String, value: Int? = null, viewer: Int? = null,
             seen: Boolean = false, toast: String? = null, done: (() -> Unit)? = null) {
        val body = JSONObject().put("type", kind).put("id", id).put("action", action)
        if (value != null) body.put("value", value)
        if (viewer != null) body.put("viewer", viewer)
        if (seen) body.put("seen", true)
        Api.obj("POST", "/device/taste", body) { result ->
            if (activity.isFinishing) return@obj
            result.onSuccess {
                if (toast != null) Toast.makeText(activity, toast, Toast.LENGTH_SHORT).show()
                done?.invoke()
            }.onFailure { Toast.makeText(activity, Api.friendly(it), Toast.LENGTH_LONG).show() }
        }
    }

    /**
     * Tummar för en titel. askWho: fråga "Vem tittade?" om inget tittarläge är valt
     * (efter en film). Tummen räknas som att ni har sett den.
     */
    fun rate(activity: Activity, kind: String, id: String, name: String, askWho: Boolean, done: (() -> Unit)? = null) {
        val prefs = Prefs(activity)
        val chosen = prefs.activeViewer
        if (chosen != null || !askWho) {
            ratePanel(activity, kind, id, name, chosen, done)
            return
        }
        loadViewers { list ->
            if (activity.isFinishing) return@loadViewers
            if (list.size <= 1) {
                ratePanel(activity, kind, id, name, list.firstOrNull()?.id, done)
                return@loadViewers
            }
            Look.choose(activity, "Vem tittade på $name?", list.map { it.name } + "Hoppa över") { which ->
                if (which < list.size) ratePanel(activity, kind, id, name, list[which].id, done, claimSeen = true)
            }
        }
    }

    private fun ratePanel(activity: Activity, kind: String, id: String, name: String, viewer: Int?,
                          done: (() -> Unit)?, claimSeen: Boolean = false) {
        val labels = RATINGS.map { it.second } + "Hoppa över"
        Look.choose(activity, "Vad tyckte ni om $name?", labels) { which ->
            if (which < RATINGS.size) {
                send(activity, kind, id, "rate", value = RATINGS[which].first, viewer = viewer, seen = true,
                    toast = "Sparat: ${RATINGS[which].second.replace("  ", " ")}", done = done)
            } else if (claimSeen) {
                send(activity, kind, id, "seen", viewer = viewer, done = done)
            }
        }
    }

    /** Efter spelaren: en film som setts klart. Anropas i onResume på sidorna man kommer tillbaka till. */
    fun promptPending(activity: Activity) {
        val pending = Session.pendingRating ?: return
        Session.pendingRating = null
        if (Prefs(activity).adultMode) return
        if (Profiles.current(activity)?.guest == true) return  // Gästen sparar inga tummar
        activity.window.decorView.postDelayed({
            if (!activity.isFinishing) rate(activity, pending.first, pending.second, pending.third, askWho = true)
        }, 400)
    }

    /** Extra val när man håller in OK på en film eller serie (inte i vuxenläget). */
    fun options(activity: Activity, item: Item, after: () -> Unit): List<Pair<String, () -> Unit>> {
        if (item.type != "vod" && item.type != "series") return emptyList()
        if (Prefs(activity).adultMode) return emptyList()
        if (Profiles.current(activity)?.guest == true) return emptyList()
        return listOf(
            "Betygsätt (👍👍 👍 👎)" to { rate(activity, item.type, item.id, item.name, askWho = true, done = after) },
            "Redan sett" to { send(activity, item.type, item.id, "seen", toast = "Markerad som sedd", done = after) },
            "☆ Vill se" to { send(activity, item.type, item.id, "wish", toast = "Tillagd på Vill se", done = after) },
        )
    }
}
