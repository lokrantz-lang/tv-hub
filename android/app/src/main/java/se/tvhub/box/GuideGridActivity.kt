package se.tvhub.box

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.RectF
import android.graphics.Typeface
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.text.TextPaint
import android.text.TextUtils
import android.view.Gravity
import android.view.KeyEvent
import android.view.View
import android.view.ViewGroup
import android.widget.FrameLayout
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import org.json.JSONArray
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * TV-guiden: kanaler i rader och program på en tidslinje (som i en tidning).
 *
 *  - ▲ ▼ byter kanal, ◀ ▶ går till föregående/nästa program. Kanal +/− bläddrar en sida.
 *  - OK: titta live (pågående), se i efterhand (tidigare, om kanalen har arkiv), påminn mig eller spela in (kommande).
 *  - Håll in OK: alla val, t.ex. kanalens programguide. Upp från första raden: välj grupp (Favoriter, kategorier).
 *  - Programguiden kommer från TV Hub (svenska kanaler och sportkanaler finns redan där, andra hämtas när de visas).
 */
class GuideGridActivity : Activity() {
    companion object {
        /** Hemdatan (favoriter och kategorier), satt av Hem innan start. */
        var homeData: JSONObject? = null

        fun start(context: Context, home: JSONObject? = null) {
            if (home != null) homeData = home
            context.startActivity(Intent(context, GuideGridActivity::class.java))
        }

        private const val SLOT_S = 30 * 60L
        private const val WINDOW_S = 2 * 3600L
        private const val BACK_S = 26 * 3600L
        private const val AHEAD_S = 30 * 3600L
    }

    data class Program(val title: String, val start: Long, val end: Long, val description: String)

    private data class Group(val key: String, val name: String, val categoryId: String?, val items: List<Item>?)

    private lateinit var prefs: Prefs
    private val handler = Handler(Looper.getMainLooper())
    private val timeFormat = SimpleDateFormat("HH:mm", Locale("sv", "SE"))
    private val dayFormat = SimpleDateFormat("EEEE d MMMM", Locale("sv", "SE"))

    private lateinit var kicker: TextView
    private lateinit var titleView: TextView
    private lateinit var meta: TextView
    private lateinit var plot: TextView
    private lateinit var groupButton: TextView
    private lateinit var column: LinearLayout
    private lateinit var grid: GridView

    private val groups = ArrayList<Group>()
    private var group: Group? = null
    private var channels: List<Item> = emptyList()
    private val programs = HashMap<String, List<Program>>()
    private val loading = HashSet<String>()
    private var groupToken = 0

    private var firstRow = 0
    private var visibleRows = 6
    private var selRow = 0
    private var onGroupButton = false
    private var anchor = 0L
    private var windowStart = 0L

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        prefs = Prefs(this)
        Api.init(prefs)
        Look.init(this)
        Fonts.install(this)
        val now = System.currentTimeMillis() / 1000
        anchor = now
        windowStart = now / SLOT_S * SLOT_S - SLOT_S
        buildViews()
        buildGroups()
        val start = groups.firstOrNull { it.key == prefs.guideGroup } ?: groups.firstOrNull()
        if (start == null) {
            titleView.text = "Inga kanaler"
            meta.text = "Välj kanaler som favoriter, eller vänta tills listorna har hämtats."
            return
        }
        selectGroup(start)
    }

    override fun onDestroy() {
        handler.removeCallbacksAndMessages(null)
        super.onDestroy()
    }

    // ---------------------------------------------------------------- vyer

    private fun dp(v: Int) = Look.dp(this, v)

    private fun buildViews() {
        val root = LinearLayout(this)
        root.orientation = LinearLayout.VERTICAL
        root.setBackgroundColor(0xFF0A0A0C.toInt())
        root.setPadding(dp(48), dp(24), dp(48), dp(20))

        kicker = text(15f, 0xFFA1A1AA.toInt())
        titleView = text(28f, Color.WHITE, bold = true)
        meta = text(16f, 0xFFD4D4D8.toInt())
        plot = text(15f, 0xFFA1A1AA.toInt())
        plot.maxLines = 2
        plot.ellipsize = TextUtils.TruncateAt.END
        val info = LinearLayout(this)
        info.orientation = LinearLayout.VERTICAL
        info.addView(kicker)
        info.addView(titleView)
        info.addView(meta)
        info.addView(plot)
        root.addView(info, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(142)))

        groupButton = text(16f, Color.WHITE, bold = true)
        groupButton.setBackgroundResource(R.drawable.m_chip)
        groupButton.setPadding(dp(16), dp(6), dp(16), dp(6))
        groupButton.gravity = Gravity.CENTER_VERTICAL
        val header = LinearLayout(this)
        header.orientation = LinearLayout.HORIZONTAL
        header.gravity = Gravity.CENTER_VERTICAL
        header.addView(groupButton, LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, dp(36)))
        val hint = text(13f, 0xFF6E6E78.toInt())
        hint.text = "   ▲ från första raden: välj grupp   ·   OK: titta, se i efterhand eller påminn   ·   Håll in OK: fler val"
        header.addView(hint)
        root.addView(header, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(44)))

        val body = LinearLayout(this)
        body.orientation = LinearLayout.HORIZONTAL
        column = LinearLayout(this)
        column.orientation = LinearLayout.VERTICAL
        column.setPadding(0, dp(GridView.RULER_DP), 0, 0)
        body.addView(column, LinearLayout.LayoutParams(dp(190), ViewGroup.LayoutParams.MATCH_PARENT))
        grid = GridView(this)
        body.addView(grid, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.MATCH_PARENT, 1f))
        root.addView(body, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f))
        setContentView(root)
        grid.onRows = { rows ->
            if (rows != visibleRows || column.childCount != rows) {
                visibleRows = rows
                buildColumn()
                render()
            }
        }
    }

    private fun text(size: Float, color: Int, bold: Boolean = false): TextView {
        val t = TextView(this)
        t.textSize = size
        t.setTextColor(color)
        t.maxLines = 1
        t.ellipsize = TextUtils.TruncateAt.END
        if (bold) t.typeface = Typeface.create(Typeface.DEFAULT, Typeface.BOLD)
        return t
    }

    private fun buildColumn() {
        column.removeAllViews()
        for (i in 0 until visibleRows) {
            val cell = FrameLayout(this)
            cell.setPadding(dp(4), dp(4), dp(10), dp(4))
            val logo = ImageView(this)
            logo.scaleType = ImageView.ScaleType.FIT_CENTER
            logo.setBackgroundResource(R.drawable.m_logo_tile)
            val pad = dp(4)
            logo.setPadding(pad, pad, pad, pad)
            cell.addView(logo, FrameLayout.LayoutParams(dp(72), ViewGroup.LayoutParams.MATCH_PARENT))
            val name = text(14f, 0xFFD4D4D8.toInt())
            name.maxLines = 2
            name.gravity = Gravity.CENTER_VERTICAL
            val lp = FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT)
            lp.leftMargin = dp(80)
            cell.addView(name, lp)
            column.addView(cell, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(GridView.ROW_DP)))
        }
    }

    // ---------------------------------------------------------------- grupper och kanaler

    private fun buildGroups() {
        val home = homeData
        groups.clear()
        val favs = Item.list(home?.optJSONArray("favorites")).filter { it.type == "live" }
        if (favs.isNotEmpty()) groups.add(Group("fav", "★ Favoriter", null, favs))
        val recent = Item.list(home?.optJSONArray("recent_live"))
        if (recent.isNotEmpty()) groups.add(Group("recent", "Senaste kanaler", null, recent))
        val cats = home?.optJSONObject("categories")?.optJSONArray("live") ?: JSONArray()
        for (i in 0 until cats.length()) {
            val c = cats.optJSONObject(i) ?: continue
            val id = c.optString("category_id")
            groups.add(Group("cat:$id", c.optString("name"), id, null))
        }
    }

    private fun chooseGroup() {
        if (groups.isEmpty()) return
        Look.choose(this, "Grupp", groups.map { it.name }, checked = groups.indexOf(group)) { which -> selectGroup(groups[which]) }
    }

    private fun selectGroup(g: Group) {
        group = g
        prefs.guideGroup = g.key
        groupButton.text = "☰  ${g.name}"
        firstRow = 0
        selRow = 0
        val t = ++groupToken
        if (g.items != null) {
            setChannels(g.items)
            return
        }
        channels = emptyList()
        render()
        titleView.text = "Hämtar kanaler …"
        Api.obj("GET", "/device/browse?type=live&category_id=${Api.enc(g.categoryId ?: "")}&limit=400") { result ->
            if (t != groupToken || isFinishing) return@obj
            result.onSuccess { setChannels(Item.list(it.optJSONArray("items"))) }
                .onFailure { titleView.text = Api.friendly(it) }
        }
    }

    private fun setChannels(list: List<Item>) {
        channels = list
        if (list.isEmpty()) {
            titleView.text = "Inga kanaler i gruppen"
            meta.text = ""
            plot.text = ""
        }
        render()
    }

    /** Programmen för raderna som syns (och några till), max 30 kanaler per anrop. */
    private fun ensureLoaded() {
        val from = (firstRow - 4).coerceAtLeast(0)
        val to = (firstRow + visibleRows + 8).coerceAtMost(channels.size)
        val want = channels.subList(from, to).map { it.id }.filter { it !in programs && it !in loading }.take(30)
        if (want.isEmpty()) return
        loading.addAll(want)
        val now = System.currentTimeMillis() / 1000
        Api.obj("GET", "/device/epg-grid?ids=${Api.enc(want.joinToString(","))}&from=${now - BACK_S}&to=${now + AHEAD_S}") { result ->
            loading.removeAll(want.toSet())
            if (isFinishing) return@obj
            result.onSuccess { data ->
                val arr = data.optJSONArray("channels") ?: JSONArray()
                val got = HashSet<String>()
                for (i in 0 until arr.length()) {
                    val c = arr.optJSONObject(i) ?: continue
                    val p = c.optJSONArray("programs") ?: JSONArray()
                    programs[c.optString("id")] = (0 until p.length()).mapNotNull { j ->
                        p.optJSONObject(j)?.let { o -> Program(o.optString("title"), o.optLong("start"), o.optLong("end"), o.optString("description")) }
                    }.sortedBy { it.start }
                    got.add(c.optString("id"))
                }
                for (id in want) if (id !in got) programs[id] = emptyList()
                render()
            }.onFailure { for (id in want) programs[id] = emptyList(); render() }
        }
    }

    // ---------------------------------------------------------------- urval

    /** Det markerade programmet (eller en tom halvtimme om programguiden saknas). */
    private fun selected(): Program? {
        val ch = channels.getOrNull(selRow) ?: return null
        val list = programs[ch.id] ?: return null
        return list.firstOrNull { anchor >= it.start && anchor < it.end }
    }

    private fun slotFor(t: Long): Program {
        val s = t / SLOT_S * SLOT_S
        return Program("", s, s + SLOT_S, "")
    }

    private fun current(): Program = selected() ?: slotFor(anchor)

    private fun keepVisible() {
        val p = current()
        val windowEnd = windowStart + WINDOW_S
        if (p.start >= windowEnd - SLOT_S / 2 || anchor >= windowEnd) {
            windowStart = (maxOf(p.start, anchor - SLOT_S) / SLOT_S) * SLOT_S
        } else if (p.end <= windowStart + 60 || anchor < windowStart) {
            windowStart = (minOf(p.start, anchor) / SLOT_S) * SLOT_S
        }
        val now = System.currentTimeMillis() / 1000
        windowStart = windowStart.coerceIn((now - BACK_S) / SLOT_S * SLOT_S, (now + AHEAD_S - WINDOW_S) / SLOT_S * SLOT_S)
        if (selRow < firstRow) firstRow = selRow
        if (selRow >= firstRow + visibleRows) firstRow = selRow - visibleRows + 1
        firstRow = firstRow.coerceIn(0, (channels.size - visibleRows).coerceAtLeast(0))
    }

    private fun move(dx: Int) {
        val p = current()
        val now = System.currentTimeMillis() / 1000
        val target = if (dx > 0) p.end else p.start - 1
        if (target < now - BACK_S || target > now + AHEAD_S) return
        anchor = if (dx > 0) target else {
            val list = programs[channels.getOrNull(selRow)?.id ?: ""]
            list?.firstOrNull { target >= it.start && target < it.end }?.start ?: slotFor(target).start
        }
        keepVisible()
        render()
    }

    private fun moveRow(dy: Int) {
        if (onGroupButton) {
            if (dy > 0) {
                onGroupButton = false
                render()
            }
            return
        }
        val next = selRow + dy
        if (next < 0) {
            onGroupButton = true
            render()
            return
        }
        if (next >= channels.size) return
        // Samma tidpunkt på nästa kanal; programmet där börjar kanske tidigare.
        anchor = maxOf(current().start, windowStart)
        selRow = next
        keepVisible()
        render()
    }

    private fun page(dy: Int) {
        if (channels.isEmpty()) return
        selRow = (selRow + dy * visibleRows).coerceIn(0, channels.size - 1)
        onGroupButton = false
        keepVisible()
        render()
    }

    // ---------------------------------------------------------------- visning

    private fun render() {
        groupButton.setTextColor(if (onGroupButton) 0xFF0A0A0C.toInt() else Color.WHITE)
        groupButton.setBackgroundResource(if (onGroupButton) R.drawable.m_button_primary else R.drawable.m_chip)
        for (i in 0 until column.childCount) {
            val cell = column.getChildAt(i) as FrameLayout
            val ch = channels.getOrNull(firstRow + i)
            val logo = cell.getChildAt(0) as ImageView
            val name = cell.getChildAt(1) as TextView
            if (ch == null) {
                cell.visibility = View.INVISIBLE
                continue
            }
            cell.visibility = View.VISIBLE
            if (logo.tag != ch.icon) ImageLoader.load(logo, ch.icon)
            name.text = (ch.num?.let { "$it  " } ?: "") + ch.name
            name.setTextColor(if (firstRow + i == selRow && !onGroupButton) Color.WHITE else 0xFFA1A1AA.toInt())
        }
        grid.state = GridView.State(windowStart, (0 until visibleRows).map { i -> channels.getOrNull(firstRow + i)?.let { programs[it.id] } },
            (0 until visibleRows).map { i -> channels.getOrNull(firstRow + i)?.archiveDays ?: 0 },
            if (onGroupButton) -1 else selRow - firstRow, current(), channels.size > firstRow)
        grid.invalidate()
        renderInfo()
        ensureLoaded()
    }

    private fun renderInfo() {
        if (onGroupButton) {
            kicker.text = "Grupp"
            titleView.text = group?.name ?: ""
            meta.text = "OK: välj en annan grupp (Favoriter, Senaste kanaler eller en kategori)."
            plot.text = ""
            return
        }
        val ch = channels.getOrNull(selRow) ?: return
        val p = selected()
        val now = System.currentTimeMillis() / 1000
        kicker.text = ch.name
        if (p == null) {
            titleView.text = if (programs.containsKey(ch.id)) "Ingen programinformation" else "Hämtar programguiden …"
            meta.text = if (programs.containsKey(ch.id)) "OK: titta på kanalen" else ""
            plot.text = ""
            return
        }
        titleView.text = p.title
        val day = dayFormat.format(Date(p.start * 1000)).replaceFirstChar { it.uppercase() }
        val time = "${timeFormat.format(Date(p.start * 1000))}–${timeFormat.format(Date(p.end * 1000))}"
        val state = when {
            p.start <= now && now < p.end -> "● Sänds nu   ·   OK: titta"
            p.end <= now && canReplay(ch, p) -> "▶ Kan ses i efterhand   ·   OK: spela"
            p.end <= now -> "Har sänts"
            else -> "OK: påminn mig eller spela in"
        }
        meta.text = "$day  $time   ·   $state"
        plot.text = p.description
    }

    private fun canReplay(ch: Item, p: Program): Boolean {
        val now = System.currentTimeMillis() / 1000
        return ch.archiveDays > 0 && p.end <= now && p.start >= now - ch.archiveDays * 86400L
    }

    // ---------------------------------------------------------------- knappar

    override fun dispatchKeyEvent(event: KeyEvent): Boolean {
        prefs.touch()
        if (event.action == KeyEvent.ACTION_UP) {
            if (event.keyCode == KeyEvent.KEYCODE_DPAD_CENTER || event.keyCode == KeyEvent.KEYCODE_ENTER) {
                if (event.isCanceled || longPressed) {
                    longPressed = false
                    return true
                }
                open()
                return true
            }
            return super.dispatchKeyEvent(event)
        }
        when (event.keyCode) {
            KeyEvent.KEYCODE_DPAD_UP -> moveRow(-1)
            KeyEvent.KEYCODE_DPAD_DOWN -> moveRow(1)
            KeyEvent.KEYCODE_DPAD_LEFT -> if (!onGroupButton) move(-1)
            KeyEvent.KEYCODE_DPAD_RIGHT -> if (!onGroupButton) move(1)
            KeyEvent.KEYCODE_CHANNEL_UP, KeyEvent.KEYCODE_PAGE_UP -> page(-1)
            KeyEvent.KEYCODE_CHANNEL_DOWN, KeyEvent.KEYCODE_PAGE_DOWN -> page(1)
            KeyEvent.KEYCODE_MENU -> chooseGroup()
            KeyEvent.KEYCODE_DPAD_CENTER, KeyEvent.KEYCODE_ENTER -> {
                if (event.repeatCount == 1 && !onGroupButton) {
                    longPressed = true
                    options()
                }
            }
            else -> return super.dispatchKeyEvent(event)
        }
        return true
    }

    private var longPressed = false

    private fun open() {
        if (onGroupButton) {
            chooseGroup()
            return
        }
        val ch = channels.getOrNull(selRow) ?: return
        val p = selected()
        val now = System.currentTimeMillis() / 1000
        when {
            p == null || (p.start <= now && now < p.end) -> playLive(ch)
            p.end <= now && canReplay(ch, p) -> playArchive(ch, p)
            p.end <= now -> Toast.makeText(this, "Det här programmet finns inte att se i efterhand.", Toast.LENGTH_SHORT).show()
            else -> options()
        }
    }

    private fun options() {
        val ch = channels.getOrNull(selRow) ?: return
        val p = selected()
        val now = System.currentTimeMillis() / 1000
        val opts = ArrayList<Pair<String, () -> Unit>>()
        opts.add("▶ Titta på ${ch.name}" to { playLive(ch) })
        if (p != null && p.end <= now && canReplay(ch, p)) opts.add("▶ Se i efterhand" to { playArchive(ch, p) })
        if (p != null && p.start > now) {
            opts.add("🔔 Påminn mig när det börjar" to { remind(ch, p) })
            opts.add("● Spela in" to { record(ch, p) })
        }
        if (p != null && p.start <= now && now < p.end) opts.add("● Spela in resten av programmet" to { record(ch, p) })
        opts.add("Programguide för ${ch.name}" to { GuideActivity.start(this, ch.id, ch.name) })
        Look.choose(this, p?.title ?: ch.name, opts.map { it.first }) { opts[it].second() }
    }

    private fun playLive(ch: Item) {
        PlayerActivity.start(
            this, type = "live", id = ch.id, name = ch.name, ext = null,
            queueIds = ArrayList(channels.map { it.id }), queueNames = ArrayList(channels.map { it.name }),
            queueExts = null, queueIndex = channels.indexOfFirst { it.id == ch.id }, icon = ch.icon,
        )
    }

    private fun playArchive(ch: Item, p: Program) {
        PlayerActivity.start(
            this, type = "catchup", id = ch.id, name = "${ch.name}: ${p.title}", ext = null,
            catchupStart = p.start, catchupMinutes = ((p.end - p.start) / 60).toInt().coerceIn(1, 600), icon = ch.icon,
        )
    }

    private fun remind(ch: Item, p: Program) {
        val body = JSONObject().put("stream_id", ch.id).put("title", p.title).put("start", p.start).put("end", p.end)
        Api.obj("POST", "/device/reminders", body) { result ->
            result.onSuccess {
                Toast.makeText(this, "🔔 Boxen frågar om den ska byta kanal kl ${timeFormat.format(Date(p.start * 1000))}.", Toast.LENGTH_LONG).show()
            }.onFailure { Toast.makeText(this, Api.friendly(it), Toast.LENGTH_LONG).show() }
        }
    }

    private fun record(ch: Item, p: Program) {
        val body = JSONObject().put("stream_id", ch.id).put("title", p.title).put("start", p.start).put("end", p.end)
        Api.obj("POST", "/device/recordings", body) { result ->
            result.onSuccess {
                RecScheduler.sync(this)
                Toast.makeText(this, "● ${p.title}: planerad. Syns under Inspelningar.", Toast.LENGTH_LONG).show()
            }.onFailure { Toast.makeText(this, Api.friendly(it), Toast.LENGTH_LONG).show() }
        }
    }

    // ---------------------------------------------------------------- rutnätet

    /** Tidslinjen och programmen, ritade direkt (snabbt även på Z Alpha). */
    class GridView(context: Context) : View(context) {
        companion object {
            const val ROW_DP = 64
            const val RULER_DP = 30
        }

        data class State(
            val windowStart: Long,
            val rows: List<List<Program>?>,
            val archiveDays: List<Int>,
            val selRow: Int,
            val selected: Program,
            val hasRows: Boolean,
        )

        var state: State? = null
        var onRows: ((Int) -> Unit)? = null

        private val density = context.resources.displayMetrics.density
        private val rowH = ROW_DP * density
        private val ruler = RULER_DP * density
        private val fill = Paint(Paint.ANTI_ALIAS_FLAG)
        private val line = Paint().apply { color = 0xFF24242A.toInt(); strokeWidth = density }
        private val nowLine = Paint().apply { color = 0xFFFF3B30.toInt(); strokeWidth = 2 * density }
        private val title = TextPaint(Paint.ANTI_ALIAS_FLAG).apply { textSize = 16 * density; color = Color.WHITE }
        private val small = TextPaint(Paint.ANTI_ALIAS_FLAG).apply { textSize = 12.5f * density; color = 0xFFA1A1AA.toInt() }
        private val rulerText = TextPaint(Paint.ANTI_ALIAS_FLAG).apply { textSize = 13 * density; color = 0xFFA1A1AA.toInt() }
        private val rect = RectF()
        private val fmt = SimpleDateFormat("HH:mm", Locale("sv", "SE"))

        override fun onSizeChanged(w: Int, h: Int, oldw: Int, oldh: Int) {
            super.onSizeChanged(w, h, oldw, oldh)
            val rows = ((h - ruler) / rowH).toInt().coerceAtLeast(1)
            post { onRows?.invoke(rows) }
        }

        private fun x(t: Long, ws: Long): Float = ((t - ws).toFloat() / WINDOW_S) * width

        override fun onDraw(canvas: Canvas) {
            val s = state ?: return
            val ws = s.windowStart
            val we = ws + WINDOW_S
            val now = System.currentTimeMillis() / 1000
            // Tidslinjen
            var t = ws
            while (t < we) {
                val px = x(t, ws)
                canvas.drawText(fmt.format(Date(t * 1000)), px + 6 * density, ruler - 10 * density, rulerText)
                canvas.drawLine(px, ruler - 6 * density, px, ruler, line)
                t += SLOT_S
            }
            for ((i, list) in s.rows.withIndex()) {
                val top = ruler + i * rowH
                canvas.drawLine(0f, top + rowH, width.toFloat(), top + rowH, line)
                if (!s.hasRows && i > 0) continue
                if (list == null) {
                    // Hämtas: grå platshållare
                    fill.color = 0xFF17171B.toInt()
                    rect.set(2 * density, top + 3 * density, width - 2 * density, top + rowH - 3 * density)
                    canvas.drawRoundRect(rect, 8 * density, 8 * density, fill)
                    continue
                }
                val archive = s.archiveDays.getOrElse(i) { 0 }
                var drewSel = false
                for (p in list) {
                    if (p.end <= ws || p.start >= we) continue
                    val sel = i == s.selRow && p.start == s.selected.start && p.end == s.selected.end
                    if (sel) drewSel = true
                    drawBlock(canvas, p, top, ws, sel, now, archive)
                }
                if (i == s.selRow && !drewSel) drawBlock(canvas, s.selected, top, ws, true, now, archive)
            }
            if (now in ws until we) {
                val px = x(now, ws)
                canvas.drawLine(px, ruler - 4 * density, px, height.toFloat(), nowLine)
            }
        }

        private fun drawBlock(canvas: Canvas, p: Program, top: Float, ws: Long, sel: Boolean, now: Long, archive: Int) {
            val x0 = maxOf(0f, x(p.start, ws)) + 2 * density
            val x1 = minOf(width.toFloat(), x(p.end, ws)) - 2 * density
            if (x1 - x0 < 2 * density) return
            val live = p.start <= now && now < p.end
            val past = p.end <= now
            val replay = past && archive > 0 && p.start >= now - archive * 86400L
            fill.color = when {
                sel -> Color.WHITE
                live -> 0xFF2C2C33.toInt()
                past -> 0xFF141418.toInt()
                else -> 0xFF1E1E23.toInt()
            }
            rect.set(x0, top + 3 * density, x1, top + rowH - 3 * density)
            canvas.drawRoundRect(rect, 8 * density, 8 * density, fill)
            val textW = x1 - x0 - 16 * density
            if (textW < 12 * density) return
            title.color = when {
                sel -> 0xFF0A0A0C.toInt()
                past && !replay -> 0xFF6E6E78.toInt()
                else -> Color.WHITE
            }
            small.color = if (sel) 0xFF3A3A42.toInt() else 0xFF8E8E96.toInt()
            val label = (if (replay) "▶ " else "") + p.title.ifEmpty { "–" }
            val shown = TextUtils.ellipsize(label, title, textW, TextUtils.TruncateAt.END).toString()
            canvas.drawText(shown, x0 + 8 * density, top + 27 * density, title)
            if (p.title.isNotEmpty()) {
                val time = "${fmt.format(Date(p.start * 1000))}–${fmt.format(Date(p.end * 1000))}"
                canvas.drawText(TextUtils.ellipsize(time, small, textW, TextUtils.TruncateAt.END).toString(), x0 + 8 * density, top + 47 * density, small)
            }
        }
    }
}
