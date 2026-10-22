package se.tvhub.box

import android.app.Activity
import android.widget.Toast
import org.json.JSONArray
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * Mina sporter och "När går …?". Sporterna och lagen ställs in i webbadminen (fliken Sport); TV Hub letar i
 * programguiden och svarar med sändningar: just nu (spela kanalen), kommande (påminn mig, spela in) och
 * i efterhand (spela ur arkivet).
 */
object Sports {
    data class Event(
        val id: String,
        val title: String,
        val description: String,
        val start: Long,
        val end: Long,
        val state: String,
        val channelId: String,
        val channel: String,
        val icon: String?,
        val also: List<String>,
        val interests: List<String>,
        val whenText: String,
        val command: JSONObject?,
    ) {
        val live: Boolean get() = state == "now"
        val past: Boolean get() = state == "past"
    }

    data class Interest(val id: Int, val name: String, val count: Int)

    data class Overview(val now: List<Event>, val upcoming: List<Event>, val past: List<Event>, val interests: List<Interest>)

    /** Senaste översikten (för raden på Hem). */
    var cached: Overview? = null
        private set

    private val timeFormat = SimpleDateFormat("HH:mm", Locale("sv", "SE"))

    private fun strings(arr: JSONArray?): List<String> =
        if (arr == null) emptyList() else (0 until arr.length()).map { arr.optString(it) }

    fun parseEvents(arr: JSONArray?): List<Event> {
        if (arr == null) return emptyList()
        return (0 until arr.length()).mapNotNull { i ->
            val o = arr.optJSONObject(i) ?: return@mapNotNull null
            Event(
                o.optString("id"), o.optString("title"), o.optString("description"), o.optLong("start"), o.optLong("end"),
                o.optString("state"), o.optString("channel_id"), o.optString("channel"),
                o.optString("icon").takeIf { it.isNotEmpty() && it != "null" }, strings(o.optJSONArray("also")),
                strings(o.optJSONArray("interests")), o.optString("when"), o.optJSONObject("command"),
            )
        }
    }

    fun parse(data: JSONObject): Overview {
        val ints = data.optJSONArray("interests")
        val interests = if (ints == null) emptyList() else (0 until ints.length()).mapNotNull { i ->
            ints.optJSONObject(i)?.let { Interest(it.optInt("id"), it.optString("name"), it.optInt("count")) }
        }
        return Overview(parseEvents(data.optJSONArray("now")), parseEvents(data.optJSONArray("upcoming")),
            parseEvents(data.optJSONArray("past")), interests)
    }

    /** Hämtar översikten. changed anropas bara när den ändrats. */
    fun load(interest: Int? = null, changed: (Overview) -> Unit) {
        Api.obj("GET", "/device/sports" + (interest?.let { "?interest=$it" } ?: "")) { result ->
            result.onSuccess { data ->
                val o = parse(data)
                if (interest != null) {
                    changed(o)
                } else if (o != cached) {
                    cached = o
                    changed(o)
                }
            }
        }
    }

    fun clear() {
        cached = null
    }

    /** Raden på Hem: det som sänds nu och det som börjar inom ett dygn. */
    fun homeCards(o: Overview?): List<Card> {
        if (o == null) return emptyList()
        val soon = o.upcoming.filter { it.start * 1000 < System.currentTimeMillis() + 24 * 3600 * 1000L }
        return (o.now + soon).take(20).map { card(it) }
    }

    fun card(e: Event): Card {
        val kicker = e.interests.joinToString(" · ").ifEmpty { "Sport" }
        val meta = listOf(e.whenText, e.channel).joinToString("   ·   ") + if (e.also.isNotEmpty()) "   ·   även ${e.also.joinToString(", ")}" else ""
        val progress = if (e.live && e.end > e.start) ((System.currentTimeMillis() / 1000 - e.start) * 1000 / (e.end - e.start)).toInt() else -1
        return Card(
            kind = Card.CHANNEL, key = "sport:${e.id}", title = e.title, subtitle = e.whenText, image = e.icon,
            progress = progress, payload = e,
            hero = Hero(kicker, e.title, meta, e.description.ifEmpty { null }, null, progress),
        )
    }

    /** OK på en sändning: spela (nu eller ur arkivet), annars val för kommande. */
    fun open(activity: Activity, e: Event) {
        val cmd = e.command
        if (cmd != null) {
            Control.execute(activity, cmd)
            return
        }
        options(activity, e)
    }

    fun options(activity: Activity, e: Event) {
        val opts = ArrayList<Pair<String, () -> Unit>>()
        e.command?.let { cmd -> opts.add((if (e.live) "▶ Titta nu" else "▶ Se i efterhand") to { Control.execute(activity, cmd) }) }
        if (!e.live && !e.past) {
            opts.add("🔔 Påminn mig när det börjar" to { remind(activity, e) })
            if (!FormFactor.isMobile(activity)) opts.add("● Spela in" to { record(activity, e) })
        }
        opts.add("Programguide för ${e.channel}" to { GuideActivity.start(activity, e.channelId, e.channel) })
        val sub = listOf(e.whenText, e.channel).joinToString(" · ") + if (e.also.isNotEmpty()) "\nÄven på ${e.also.joinToString(", ")}" else ""
        Look.choose(activity, e.title, opts.map { it.first }, subtitle = if (Prefs(activity).modern) sub else null) { which -> opts[which].second() }
    }

    private fun remind(activity: Activity, e: Event) {
        val body = JSONObject().put("stream_id", e.channelId).put("title", e.title).put("start", e.start).put("end", e.end)
        Api.obj("POST", "/device/reminders", body) { result ->
            result.onSuccess {
                val clock = timeFormat.format(Date(e.start * 1000))
                Toast.makeText(activity, "🔔 Boxen frågar om den ska byta kanal kl $clock.", Toast.LENGTH_LONG).show()
            }.onFailure { Toast.makeText(activity, Api.friendly(it), Toast.LENGTH_LONG).show() }
        }
    }

    private fun record(activity: Activity, e: Event) {
        // Lite marginal: matcher drar ofta över.
        val body = JSONObject().put("stream_id", e.channelId).put("title", e.title)
            .put("start", e.start - 120).put("end", e.end + 15 * 60)
        Api.obj("POST", "/device/recordings", body) { result ->
            result.onSuccess {
                RecScheduler.sync(activity)
                Toast.makeText(activity, "● ${e.title}: planerad. Syns under Inspelningar.", Toast.LENGTH_LONG).show()
            }.onFailure { Toast.makeText(activity, Api.friendly(it), Toast.LENGTH_LONG).show() }
        }
    }
}
