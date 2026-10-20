package se.tvhub.box

import android.app.Activity
import android.app.AlertDialog
import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.widget.TextView
import android.widget.Toast
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * Programguide för en kanal: tidigare program (catch-up) och kommande.
 * OK på ett tidigare program spelar det i efterhand. OK på pågående program visar kanalen live.
 * OK på ett kommande program spelar in det. Håll in OK för alla val, t.ex. spela in ett tidigare program.
 */
class GuideActivity : Activity() {
    companion object {
        fun start(context: Context, streamId: String, name: String) {
            context.startActivity(Intent(context, GuideActivity::class.java).putExtra("id", streamId).putExtra("name", name))
        }
    }

    private data class Program(
        val title: String, val start: Long, val end: Long, val now: Boolean, val playable: Boolean, val recordable: Boolean,
    )

    private lateinit var adapter: RowAdapter
    private var mobile = false  // telefonen: inga inspelningar
    private lateinit var list: RecyclerView
    private lateinit var status: TextView
    private var streamId = ""
    private var channelName = ""
    private val dayFormat = SimpleDateFormat("EEEE d MMMM", Locale("sv", "SE"))
    private val timeFormat = SimpleDateFormat("HH:mm", Locale("sv", "SE"))

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (Prefs(this).modern) Fonts.install(this)
        FormFactor.portraitIfMobile(this)
        mobile = FormFactor.isMobile(this)
        setContentView(R.layout.activity_guide)
        Api.init(Prefs(this))
        streamId = intent.getStringExtra("id") ?: return finish()
        channelName = intent.getStringExtra("name") ?: ""
        findViewById<TextView>(R.id.title).text = channelName
        status = findViewById(R.id.status)
        list = findViewById(R.id.programs)
        adapter = RowAdapter(onClick = { row, _ -> open(row) }, onLongClick = { row, _ -> options(row) })
        adapter.modern = Prefs(this).modern
        list.layoutManager = LinearLayoutManager(this)
        list.adapter = adapter
        list.itemAnimator = null
        Api.obj("GET", "/device/guide/${Api.enc(streamId)}") { result ->
            result.onSuccess { show(it) }.onFailure { status.text = Api.friendly(it) }
        }
    }

    private fun show(data: JSONObject) {
        val programs = data.optJSONArray("programs")
        val archiveDays = data.optInt("archive_days")
        status.text = if (mobile) {
            if (archiveDays > 0) "Tryck på ett program för att se det (direkt eller i efterhand, $archiveDays dagar bakåt)."
            else "Kanalen har inget arkiv. Tryck på programmet som sänds nu för att titta."
        } else if (archiveDays > 0) {
            "Tidigare program kan ses i efterhand ($archiveDays dagar bakåt). OK spelar, kommande spelas in. Håll in OK för fler val."
        } else {
            "Kanalen har inget arkiv. OK på ett kommande program spelar in det. Håll in OK för fler val."
        }
        if (programs == null || programs.length() == 0) {
            status.text = "Ingen programguide för den här kanalen."
            return
        }
        val rows = ArrayList<Row>()
        var lastDay = ""
        var nowIndex = 0
        for (i in 0 until programs.length()) {
            val o = programs.optJSONObject(i) ?: continue
            val p = Program(
                o.optString("title"), o.optLong("start"), o.optLong("end"), o.optBoolean("now"),
                o.optBoolean("playable"), o.optBoolean("recordable") && !mobile,
            )
            val day = dayFormat.format(Date(p.start * 1000)).replaceFirstChar { it.uppercase() }
            if (day != lastDay) {
                rows.add(Row(day, key = "day:$day", header = true))
                lastDay = day
            }
            if (p.now) nowIndex = rows.size
            val time = "${timeFormat.format(Date(p.start * 1000))}–${timeFormat.format(Date(p.end * 1000))}"
            val label = when {
                p.now -> if (mobile) "●  Nu   ·   tryck för att titta" else "●  Nu   ·   OK: titta live"
                p.playable -> "▶  Se i efterhand"
                p.end * 1000 < System.currentTimeMillis() -> "Har sänts"
                p.recordable -> "OK: spela in"
                else -> null
            }
            rows.add(Row("$time   ${p.title}", subtitle = label, key = "p:${p.start}", payload = p))
        }
        adapter.submit(rows)
        Focus.position(list, nowIndex)
    }

    private fun open(row: Row) {
        val p = row.payload as? Program ?: return
        when {
            p.now -> PlayerActivity.start(this, type = "live", id = streamId, name = channelName, ext = null)
            p.playable -> play(p)
            p.start * 1000 > System.currentTimeMillis() && p.recordable -> confirmRecord(p)
            else -> Toast.makeText(this, "Det här programmet finns inte att se i efterhand.", Toast.LENGTH_SHORT).show()
        }
    }

    private fun play(p: Program) {
        val minutes = ((p.end - p.start) / 60).toInt().coerceIn(1, 600)
        PlayerActivity.start(
            this, type = "catchup", id = streamId, name = "${channelName}: ${p.title}", ext = null,
            catchupStart = p.start, catchupMinutes = minutes,
        )
    }

    private fun options(row: Row) {
        val p = row.payload as? Program ?: return
        val options = ArrayList<Pair<String, () -> Unit>>()
        if (p.now) options.add("Titta live" to { open(row) })
        if (p.playable) options.add("Se i efterhand" to { play(p) })
        if (p.recordable) {
            val label = when {
                p.now -> "● Spela in resten av programmet"
                p.end * 1000 <= System.currentTimeMillis() -> "● Spela in från arkivet till USB-minnet"
                else -> "● Spela in"
            }
            options.add(label to { record(p) })
        }
        if (options.isEmpty()) {
            Toast.makeText(this, "Inga val för det här programmet.", Toast.LENGTH_SHORT).show()
            return
        }
        Look.choose(this, p.title, options.map { it.first }) { which -> options[which].second() }
    }

    private fun confirmRecord(p: Program) {
        Look.confirm(this, "Spela in?",
            "${p.title}\n${dayFormat.format(Date(p.start * 1000))} ${timeFormat.format(Date(p.start * 1000))}–${timeFormat.format(Date(p.end * 1000))}\n\n" +
                "Sparas på USB-minnet. Boxen måste vara påslagen eller i standby när programmet sänds.", "Spela in") { record(p) }
    }

    private fun record(p: Program) {
        val body = JSONObject().put("stream_id", streamId).put("title", p.title).put("start", p.start).put("end", p.end)
        Api.obj("POST", "/device/recordings", body) { result ->
            result.onSuccess { rec ->
                RecScheduler.sync(this)
                val text = if (rec.optString("source") == "archive") "Hämtas från arkivet till USB-minnet." else "Planerad. Syns under Inspelningar."
                Toast.makeText(this, "● ${p.title}: $text", Toast.LENGTH_LONG).show()
            }.onFailure { Toast.makeText(this, Api.friendly(it), Toast.LENGTH_LONG).show() }
        }
    }
}
