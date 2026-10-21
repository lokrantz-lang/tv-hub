package se.tvhub.box

import android.app.Activity
import android.widget.Toast
import org.json.JSONObject

/**
 * Nytt per dag (Inställningar): för varje dag de kategorier som fått nya titlar, med antal.
 * Välj dag, sedan kategori, så visas titlarna. Datumet är när TV Hub såg titeln dyka upp.
 */
object NewDays {
    private val WORD = mapOf("vod" to ("film" to "filmer"), "series" to ("serie" to "serier"), "live" to ("kanal" to "kanaler"))

    fun dayLabel(start: Long): String = Format.addedLabel(start).removePrefix("Ny ").replaceFirstChar { it.uppercase() }

    private fun count(type: String, n: Int): String {
        val (one, many) = WORD[type] ?: ("titel" to "titlar")
        return "$n ${if (n == 1) one else many}"
    }

    fun show(activity: Activity) {
        Api.obj("GET", "/device/new-summary?days=14") { result ->
            result.onSuccess { data ->
                val days = data.optJSONArray("days") ?: org.json.JSONArray()
                if (days.length() == 0) {
                    Look.message(activity, "Nytt per dag",
                        "Inget nytt de senaste 14 dagarna. Listorna hämtas var 12:e timme, och TV Hub räknar det som dykt upp sedan dess.")
                    return@onSuccess
                }
                val list = (0 until days.length()).mapNotNull { days.optJSONObject(it) }
                Look.choose(activity, "Nytt per dag", list.map { "${dayLabel(it.optLong("start"))}  ·  ${it.optInt("total")} nya" }) { i ->
                    chooseCategory(activity, list[i])
                }
            }.onFailure { Toast.makeText(activity, Api.friendly(it), Toast.LENGTH_LONG).show() }
        }
    }

    private fun chooseCategory(activity: Activity, day: JSONObject) {
        val cats = day.optJSONArray("categories") ?: return
        val list = (0 until cats.length()).mapNotNull { cats.optJSONObject(it) }
        val label = dayLabel(day.optLong("start"))
        Look.choose(activity, label, list.map { "${it.optString("name")}  ·  ${count(it.optString("type"), it.optInt("count"))}" }) { i ->
            val c = list[i]
            MListActivity.startDay(activity, "$label · ${c.optString("name")}", day.optLong("start"),
                c.optString("type"), c.optString("category_id"))
        }
    }
}
