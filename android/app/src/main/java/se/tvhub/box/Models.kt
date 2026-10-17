package se.tvhub.box

import org.json.JSONArray
import org.json.JSONObject

fun JSONObject.optStringOrNull(key: String): String? {
    if (isNull(key)) return null
    val value = optString(key)
    return if (value.isEmpty() || value == "null") null else value
}

/** En kanal, film, serie eller ett avsnitt. */
data class Item(
    val type: String,
    val id: String,
    val name: String,
    val icon: String?,
    val ext: String?,
    var favorite: Boolean,
    val categoryName: String?,
    val num: Int?,
) {
    val typeLabel: String
        get() = when (type) {
            "live" -> "Kanal"
            "vod" -> "Film"
            "series" -> "Serie"
            else -> ""
        }

    companion object {
        fun from(o: JSONObject): Item = Item(
            type = o.optString("type"),
            id = o.optString("id"),
            name = o.optStringOrNull("name") ?: "(utan namn)",
            icon = o.optStringOrNull("icon"),
            ext = o.optStringOrNull("ext"),
            favorite = o.optBoolean("favorite"),
            categoryName = o.optStringOrNull("category_name"),
            num = if (o.isNull("num")) null else o.optInt("num"),
        )

        fun list(array: JSONArray?): List<Item> {
            if (array == null) return emptyList()
            val out = ArrayList<Item>(array.length())
            for (i in 0 until array.length()) {
                val o = array.optJSONObject(i) ?: continue
                if (o.optBoolean("missing")) continue
                out.add(from(o))
            }
            return out
        }
    }
}

/** En rad i en lista på TV:n. */
data class Row(
    val title: String,
    val subtitle: String? = null,
    val icon: String? = null,
    val key: String = title,
    val header: Boolean = false,
    val payload: Any? = null,
    val iconSpace: Boolean = false,
    val star: Boolean = false,
)
