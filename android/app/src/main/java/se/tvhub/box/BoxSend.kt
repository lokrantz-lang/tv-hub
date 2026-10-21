package se.tvhub.box

import android.app.Activity
import android.widget.Toast
import org.json.JSONObject

/** Telefonen: "Spela på TV:n". Väljer box och skickar titeln dit (boxen startar den direkt). */
object BoxSend {
    fun options(activity: Activity, item: Item): List<Pair<String, () -> Unit>> {
        if (!FormFactor.isMobile(activity) || Prefs(activity).adultMode) return emptyList()
        if (item.type !in setOf("vod", "series", "live", "episode")) return emptyList()
        val body = JSONObject().put("type", item.type).put("id", item.id)
        item.seriesId?.let { body.put("series_id", it) }
        return listOf("📺  Spela på TV…" to { choose(activity, body) })
    }

    fun choose(activity: Activity, body: JSONObject) {
        Api.arr("/device/boxes") { result ->
            if (activity.isFinishing) return@arr
            result.onSuccess { arr ->
                val boxes = ArrayList<Triple<Int, String, Boolean>>()
                for (i in 0 until arr.length()) {
                    val o = arr.optJSONObject(i) ?: continue
                    boxes.add(Triple(o.optInt("id"), o.optString("name"), o.optBoolean("online")))
                }
                if (boxes.isEmpty()) {
                    Toast.makeText(activity, "Inga TV-boxar är parade.", Toast.LENGTH_LONG).show()
                    return@onSuccess
                }
                val labels = boxes.map { it.second + if (it.third) "" else "   (svarar inte)" }
                Look.choose(activity, "Spela på", labels) { which -> send(activity, boxes[which].first, boxes[which].second, body) }
            }.onFailure { Toast.makeText(activity, Api.friendly(it), Toast.LENGTH_LONG).show() }
        }
    }

    private fun send(activity: Activity, deviceId: Int, name: String, body: JSONObject) {
        Api.obj("POST", "/device/send", JSONObject(body.toString()).put("device_id", deviceId)) { result ->
            if (activity.isFinishing) return@obj
            result.onSuccess { r ->
                val text = if (r.optBoolean("online")) "Startar på $name: ${r.optString("description")}"
                else "$name svarar inte. Är boxen på och har TV Hub 2.0?"
                Toast.makeText(activity, text, Toast.LENGTH_LONG).show()
            }.onFailure { Toast.makeText(activity, Api.friendly(it), Toast.LENGTH_LONG).show() }
        }
    }
}
