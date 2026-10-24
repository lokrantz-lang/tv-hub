package se.tvhub.box

import org.json.JSONArray
import org.json.JSONObject

fun JSONObject.optStringOrNull(key: String): String? {
    if (isNull(key)) return null
    val value = optString(key)
    return if (value.isEmpty() || value == "null") null else value
}

fun JSONObject.optIntOrNull(key: String): Int? = if (isNull(key)) null else optInt(key)

fun JSONObject.optDoubleOrNull(key: String): Double? {
    if (isNull(key)) return null
    val value = optDouble(key)
    return if (value.isNaN()) null else value
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
    val year: Int? = null,
    val rating: Double? = null,
    val durationMin: Int? = null,
    val genre: String? = null,
    val watched: Boolean = false,
    val archiveDays: Int = 0,
    /** När titeln lades till (sekunder), för "Nytt tillagt". */
    val added: Long? = null,
    val backdrop: String? = null,
    val plot: String? = null,
    /** Favoritavsnitt: vilken serie avsnittet hör till. */
    val seriesId: String? = null,
) {
    val typeLabel: String
        get() = when (type) {
            "live" -> "Kanal"
            "vod" -> "Film"
            "series" -> "Serie"
            "episode" -> "Avsnitt"
            else -> ""
        }

    /** "2024 · ★ 8,1 · 2 h 46 min" */
    val facts: String
        get() = listOfNotNull(
            year?.toString(),
            rating?.let { "★ " + Format.rating(it) },
            durationMin?.let { Format.minutes(it) },
        ).joinToString(" · ")

    companion object {
        fun from(o: JSONObject): Item = Item(
            type = o.optString("type"),
            id = o.optString("id"),
            name = o.optStringOrNull("name") ?: "(utan namn)",
            icon = o.optStringOrNull("icon"),
            ext = o.optStringOrNull("ext"),
            favorite = o.optBoolean("favorite"),
            categoryName = o.optStringOrNull("category_name"),
            num = o.optIntOrNull("num"),
            year = o.optIntOrNull("year"),
            rating = o.optDoubleOrNull("rating_num"),
            durationMin = o.optIntOrNull("duration_min"),
            genre = o.optStringOrNull("genre"),
            watched = o.optBoolean("watched"),
            archiveDays = o.optInt("archive_days", 0),
            added = if (o.isNull("added")) null else o.optLong("added").takeIf { it > 0 },
            backdrop = o.optStringOrNull("backdrop"),
            plot = o.optStringOrNull("plot"),
            seriesId = o.optStringOrNull("series_id"),
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

/** En post i "Fortsätt titta". */
data class ContinueItem(
    val type: String,
    val id: String,
    val seriesId: String?,
    val name: String,
    val seriesName: String?,
    val icon: String?,
    val ext: String?,
    val positionMs: Long,
    val durationMs: Long,
    val backdrop: String? = null,
) {
    companion object {
        fun list(array: JSONArray?): List<ContinueItem> {
            if (array == null) return emptyList()
            val out = ArrayList<ContinueItem>()
            for (i in 0 until array.length()) {
                val o = array.optJSONObject(i) ?: continue
                out.add(
                    ContinueItem(
                        type = o.optString("type"),
                        id = o.optString("id"),
                        seriesId = o.optStringOrNull("series_id"),
                        name = o.optStringOrNull("name") ?: "",
                        seriesName = o.optStringOrNull("series_name"),
                        icon = o.optStringOrNull("icon"),
                        ext = o.optStringOrNull("ext"),
                        positionMs = o.optLong("position_ms"),
                        durationMs = o.optLong("duration_ms"),
                        backdrop = o.optStringOrNull("backdrop"),
                    )
                )
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
    val poster: Boolean = false,
)

/** Formatering av tider och betyg. */
object Format {
    fun minutes(total: Int): String {
        if (total < 60) return "$total min"
        val h = total / 60
        val m = total % 60
        return if (m == 0) "$h h" else "$h h $m min"
    }

    /** Filstorlek: "850 MB", "1,4 GB". */
    fun size(bytes: Long): String {
        val sv = java.util.Locale("sv", "SE")
        return when {
            bytes < 1_000_000L -> "${(bytes / 1_000L).coerceAtLeast(1)} kB"
            bytes < 1_000_000_000L -> String.format(sv, "%.0f MB", bytes / 1e6)
            else -> String.format(sv, "%.1f GB", bytes / 1e9)
        }
    }

    fun clock(ms: Long): String {
        val totalSec = ms / 1000
        val h = totalSec / 3600
        val m = (totalSec % 3600) / 60
        val s = totalSec % 60
        return if (h > 0) String.format("%d:%02d:%02d", h, m, s) else String.format("%d:%02d", m, s)
    }

    /** "Ny idag", "Ny igår", "Ny 17 okt" */
    fun addedLabel(seconds: Long): String {
        val cal = java.util.Calendar.getInstance()
        cal.set(java.util.Calendar.HOUR_OF_DAY, 0)
        cal.set(java.util.Calendar.MINUTE, 0)
        cal.set(java.util.Calendar.SECOND, 0)
        cal.set(java.util.Calendar.MILLISECOND, 0)
        val today = cal.timeInMillis
        val ms = seconds * 1000
        val days = if (ms >= today) 0 else ((today - ms) / 86_400_000L + 1)
        return when (days) {
            0L -> "Ny idag"
            1L -> "Ny igår"
            else -> "Ny " + java.text.SimpleDateFormat(
                if (days > 300) "d MMM yyyy" else "d MMM", java.util.Locale("sv", "SE"),
            ).format(java.util.Date(ms)).replace(".", "")
        }
    }

    fun rating(value: Double): String = String.format("%.1f", value).replace('.', ',')

    fun remaining(positionMs: Long, durationMs: Long): String {
        if (durationMs <= 0) return "Påbörjad"
        val left = ((durationMs - positionMs) / 60000).toInt().coerceAtLeast(1)
        return "${minutes(left)} kvar"
    }
}
