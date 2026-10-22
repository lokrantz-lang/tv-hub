package se.tvhub.box

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.graphics.PorterDuff
import android.graphics.Rect
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.text.Editable
import android.text.TextWatcher
import android.view.LayoutInflater
import android.view.MotionEvent
import android.view.View
import android.view.ViewGroup
import android.view.WindowManager
import android.view.inputmethod.EditorInfo
import android.view.inputmethod.InputMethodManager
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.TextView
import android.widget.Toast
import androidx.recyclerview.widget.GridLayoutManager
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import org.json.JSONArray
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * Mobilläget: samma app på telefonen. Stående, touch och flikar längst ner:
 * Hem, Live, Filmer, Serier och Sök. Inställningar uppe till höger.
 *
 * Dolt vuxenläge som på TV:n: tryck tre gånger snabbt på "TV Hub" överst.
 * I vuxenläget: håll in "TV Hub" för att visa eller dölja favoriter och historik.
 */
class MobileActivity : Activity(), CardListener {
    private lateinit var prefs: Prefs
    private lateinit var pages: FrameLayout
    private lateinit var status: TextView
    private lateinit var tabsBar: LinearLayout
    private lateinit var routeView: TextView
    private val handler = Handler(Looper.getMainLooper())
    private val timeFormat = SimpleDateFormat("HH:mm", Locale("sv", "SE"))

    private class Tab(val key: String, val label: String, val icon: Int)

    private val tabs = listOf(
        Tab("home", "Hem", R.drawable.ic_m_home),
        Tab("live", "Live", R.drawable.ic_m_live),
        Tab("vod", "Filmer", R.drawable.ic_m_movie),
        Tab("series", "Serier", R.drawable.ic_m_series),
        Tab("search", "Sök", R.drawable.ic_m_search),
    )
    private val tabViews = HashMap<String, View>()
    private val pageCache = HashMap<String, MPage>()
    private var current = ""
    private var home: JSONObject? = null
    private var shownAdult = false
    private var retryTask: Runnable? = null
    private var titleTaps = 0
    private var titleTapStart = 0L

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        prefs = Prefs(this)
        Api.init(prefs)
        Look.init(this)
        if (!prefs.paired) {
            startActivity(Intent(this, SetupActivity::class.java))
            finish()
            return
        }
        if (!FormFactor.isMobile(this)) {
            startActivity(Intent(this, MainActivity::class.java))
            finish()
            return
        }
        if (!Session.started) {
            // Ny start av appen: vuxenläget slås alltid av (som på TV:n).
            Session.started = true
            if (prefs.adultMode) prefs.adultMode = false
        }
        FormFactor.portraitIfMobile(this)
        Fonts.install(this)
        // Telefonen: visa statusraden (klocka, batteri) i stället för helskärm.
        window.clearFlags(WindowManager.LayoutParams.FLAG_FULLSCREEN)
        window.statusBarColor = Color.parseColor("#0A0A0C")
        window.navigationBarColor = Color.parseColor("#17171B")
        setContentView(R.layout.activity_mobile)
        pages = findViewById(R.id.m_pages)
        status = findViewById(R.id.m_status)
        tabsBar = findViewById(R.id.m_tabs)
        routeView = findViewById(R.id.m_route)
        buildTabs()
        findViewById<View>(R.id.m_settings).setOnClickListener { showSettings() }
        findViewById<View>(R.id.m_profile).setOnClickListener { Profiles.choose(this) { onProfileChanged() } }
        Profiles.checkRevert(this)
        updateProfileChip()
        findViewById<View>(R.id.m_night).setOnClickListener {
            if (prefs.adultMode) Toast.makeText(this, "Filmkväll finns bara i vanligt läge.", Toast.LENGTH_SHORT).show()
            else FilmNightActivity.start(this)
        }
        val title = findViewById<TextView>(R.id.m_title)
        title.setOnClickListener { onTitleTap() }
        title.setOnLongClickListener {
            if (prefs.adultMode) {
                prefs.adultHistory = !prefs.adultHistory
                Toast.makeText(this, if (prefs.adultHistory) "Historik visas" else "Historik dold", Toast.LENGTH_SHORT).show()
                resetAll()
            }
            true
        }
        Remote.refreshConfig()
    }

    override fun onResume() {
        super.onResume()
        if (!::pages.isInitialized) return
        if (Profiles.checkRevert(this) || (shownAdult && !prefs.adultMode)) {
            updateProfileChip()
            resetAll()
            return
        }
        loadHome()
        Taste.promptPending(this)
    }

    private fun updateProfileChip() {
        Profiles.bindAvatar(findViewById(R.id.m_profile), Profiles.current(this))
    }

    private fun onProfileChanged() {
        updateProfileChip()
        resetAll()
    }

    override fun onDestroy() {
        handler.removeCallbacksAndMessages(null)
        super.onDestroy()
    }

    override fun dispatchTouchEvent(ev: MotionEvent): Boolean {
        if (ev.action == MotionEvent.ACTION_DOWN && ::prefs.isInitialized) prefs.touch()
        return super.dispatchTouchEvent(ev)
    }

    @Deprecated("Deprecated in Java")
    override fun onBackPressed() {
        if (current.isNotEmpty() && current != "home") {
            show("home")
            return
        }
        super.onBackPressed()
    }

    // ================================================================ flikar och sidor

    private fun buildTabs() {
        tabsBar.removeAllViews()
        for (tab in tabs) {
            val v = LayoutInflater.from(this).inflate(R.layout.tab_m, tabsBar, false)
            v.findViewById<ImageView>(R.id.icon).setImageResource(tab.icon)
            v.findViewById<TextView>(R.id.label).text = tab.label
            v.setOnClickListener {
                if (current == tab.key) pageCache[tab.key]?.onReselect() else show(tab.key)
            }
            tabsBar.addView(v)
            tabViews[tab.key] = v
        }
        markTabs()
    }

    private fun markTabs() {
        val on = Color.parseColor("#F5F5F7")
        val off = Color.parseColor("#7C7C86")
        for ((key, v) in tabViews) {
            val color = if (key == current) on else off
            v.findViewById<ImageView>(R.id.icon).setColorFilter(color, PorterDuff.Mode.SRC_IN)
            v.findViewById<TextView>(R.id.label).setTextColor(color)
        }
    }

    private abstract inner class MPage {
        abstract val view: View
        open fun onShow() {}
        open fun onHomeData(data: JSONObject) {}
        /** Fliken trycks igen: till början. */
        open fun onReselect() {}
    }

    private fun pageFor(key: String): MPage = pageCache.getOrPut(key) {
        when (key) {
            "live" -> LivePage()
            "vod" -> BrowsePage("vod")
            "series" -> BrowsePage("series")
            "search" -> SearchPage()
            else -> HomePage()
        }.also { page -> home?.let { page.onHomeData(it) } }
    }

    private fun show(key: String) {
        hideKeyboard()
        current = key
        markTabs()
        val page = pageFor(key)
        for (i in pages.childCount - 1 downTo 0) {
            val child = pages.getChildAt(i)
            if (child !== status) pages.removeView(child)
        }
        pages.addView(page.view, 0)
        // Meddelandet i mitten (välkomsttext) hör bara till Hem; fel visas av loadHome.
        if (home != null) status.visibility = View.GONE
        page.onShow()
    }

    @Deprecated("Deprecated in Java")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: android.content.Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        val text = Voice.result(requestCode, resultCode, data) ?: return
        if (current != "search") show("search")
        (pageFor("search") as? SearchPage)?.setQuery(text)
    }

    private fun resetAll() {
        home = null
        pageCache.clear()
        val keep = current.ifEmpty { "home" }
        current = ""
        for (i in pages.childCount - 1 downTo 0) {
            val child = pages.getChildAt(i)
            if (child !== status) pages.removeView(child)
        }
        loadHome(keep)
    }

    private fun loadHome(showKey: String? = null) {
        Api.obj("GET", "/device/home?app_version=${Api.enc(BuildConfig.VERSION_NAME)}") { result ->
            updateRoute()
            result.onSuccess { data ->
                if (prefs.adultMode && data.optString("mode") != "adult") prefs.adultMode = false
                shownAdult = data.optString("mode") == "adult"
                home = data
                Profiles.onHome(this, data)
                updateProfileChip()
                if (!shownAdult) {
                    MyPrograms.load { home?.let { d -> for (page in pageCache.values) page.onHomeData(d) } }
                    Sports.load { home?.let { d -> for (page in pageCache.values) page.onHomeData(d) } }
                } else {
                    MyPrograms.clear()
                    Sports.clear()
                }
                status.visibility = View.GONE
                retryTask?.let { handler.removeCallbacks(it) }
                for (page in pageCache.values) page.onHomeData(data)
                if (current.isEmpty()) show(showKey ?: "home")
                if (!Session.updateChecked) {
                    Session.updateChecked = true
                    Updater.check(this, prefs, quiet = true)
                }
            }.onFailure { handleError(it) }
        }
    }

    private fun updateRoute() {
        routeView.text = if (Remote.lastRoute == "remote") "via Nabu Casa" else ""
    }

    private fun handleError(error: Throwable) {
        if (error is ApiException && error.status == 401 && error.message?.contains("parad") == true) {
            Toast.makeText(this, "Telefonen behöver paras om.", Toast.LENGTH_LONG).show()
            prefs.clearPairing()
            startActivity(Intent(this, SetupActivity::class.java))
            finish()
            return
        }
        if (home == null) {
            status.text = "${Api.friendly(error)}\n\nFörsöker igen automatiskt…"
            status.visibility = View.VISIBLE
            status.bringToFront()
            retryTask?.let { handler.removeCallbacks(it) }
            val task = Runnable { loadHome() }
            retryTask = task
            handler.postDelayed(task, 10_000)
        } else {
            Toast.makeText(this, Api.friendly(error), Toast.LENGTH_LONG).show()
        }
    }

    private fun privateMode(): Boolean = home?.optString("mode") == "adult" && home?.optBoolean("history", true) == false

    private fun onTitleTap() {
        val t = SystemClock.elapsedRealtime()
        if (t - titleTapStart > 2_000) {
            titleTapStart = t
            titleTaps = 0
        }
        if (++titleTaps >= 3) {
            titleTaps = 0
            titleTapStart = 0L
            if (!prefs.adultMode && Profiles.current(this)?.allowAdult == false) return
            AdultGate.toggle(this) { resetAll() }
        }
    }

    private fun hideKeyboard() {
        val imm = getSystemService(Context.INPUT_METHOD_SERVICE) as InputMethodManager
        currentFocus?.let { imm.hideSoftInputFromWindow(it.windowToken, 0) }
    }

    // ================================================================ öppna och val

    override fun onCardClick(card: Card, shelf: Shelf?) {
        when (val p = card.payload) {
            is MyPrograms.Program -> MyPrograms.play(this, p)
            is Sports.Event -> Sports.open(this, p)
            is ContinueItem -> {
                if (p.type == "episode" && p.seriesId != null) {
                    SeriesActivity.start(this, p.seriesId, p.seriesName ?: p.name, resumeEpisodeId = p.id)
                } else {
                    DetailActivity.start(this, p.id, p.name)
                }
            }
            is Item -> openItem(p, shelf?.cards?.mapNotNull { it.payload as? Item } ?: emptyList())
        }
    }

    override fun onCardFocus(card: Card, shelf: Shelf?, shelfIndex: Int, cardIndex: Int) {}

    override fun onShelfTitle(shelf: Shelf) {
        when (shelf.key) {
            "continue" -> Unit
            "new_vod" -> MListActivity.startNew(this, "vod")
            "new_series" -> MListActivity.startNew(this, "series")
            else -> MListActivity.startShelf(this, shelf.title, shelf.cards.mapNotNull { it.payload as? Item })
        }
    }

    override fun onCardLongClick(card: Card, shelf: Shelf?) {
        (card.payload as? Sports.Event)?.let { Sports.options(this, it); return }
        (card.payload as? ContinueItem)?.let { c ->
            continueOptions(this, c, open = { onCardClick(card, shelf) }, removed = { loadHome() })
            return
        }
        (card.payload as? Item)?.let { showItemOptions(it) }
    }

    private fun openItem(item: Item, siblings: List<Item>) {
        when (item.type) {
            "series" -> SeriesActivity.start(this, item.id, item.name)
            "vod" -> DetailActivity.start(this, item.id, item.name)
            "episode" -> playFavoriteEpisode(this, item)
            else -> playChannel(item, siblings.filter { it.type == "live" }.ifEmpty { listOf(item) })
        }
    }

    private fun playChannel(item: Item, channels: List<Item>) {
        PlayerActivity.start(
            this, type = "live", id = item.id, name = item.name, ext = null,
            queueIds = ArrayList(channels.map { it.id }),
            queueNames = ArrayList(channels.map { it.name }),
            queueExts = null,
            queueIndex = channels.indexOfFirst { it.id == item.id },
            icon = item.icon,
        )
    }

    private fun showItemOptions(item: Item) {
        val options = ArrayList<Pair<String, () -> Unit>>()
        when (item.type) {
            "live" -> {
                options.add("▶  Titta" to { playChannel(item, listOf(item)) })
                options.add("Programguide och tidigare program" to { GuideActivity.start(this, item.id, item.name) })
            }
            "vod" -> options.add("Detaljer" to { DetailActivity.start(this, item.id, item.name) })
            "series" -> options.add("Avsnitt" to { SeriesActivity.start(this, item.id, item.name) })
            "episode" -> {
                options.add("▶  Spela" to { playFavoriteEpisode(this, item) })
                item.seriesId?.let { sid -> options.add("Visa serien" to { SeriesActivity.start(this, sid, item.categoryName ?: item.name, item.id) }) }
            }
        }
        if (!privateMode()) {
            options.add((if (item.favorite) "Ta bort från favoriter" else "Lägg till i favoriter") to { toggleFavorite(item) })
        }
        options.addAll(Taste.options(this, item) { loadHome() })
        options.addAll(BoxSend.options(this, item))
        Look.choose(this, item.name, options.map { it.first }) { options[it].second() }
    }

    private fun toggleFavorite(item: Item) {
        val on = !item.favorite
        Api.obj("POST", "/device/favorites", JSONObject().put("type", item.type).put("id", item.id).put("on", on)) { result ->
            result.onSuccess {
                item.favorite = on
                Toast.makeText(this, if (on) "★ ${item.name} är nu en favorit" else "${item.name} borttagen ur favoriter", Toast.LENGTH_SHORT).show()
                loadHome()
            }.onFailure { Toast.makeText(this, Api.friendly(it), Toast.LENGTH_LONG).show() }
        }
    }

    /** "Rapport  20:00–20:30" och förlopp 0–1000. */
    private fun nowText(now: JSONObject?): Pair<String?, Int> {
        if (now == null) return null to -1
        val start = now.optLong("start")
        val end = now.optLong("end")
        val t = System.currentTimeMillis() / 1000
        val progress = if (end > start && t in start..end) ((t - start) * 1000 / (end - start)).toInt() else -1
        return "${now.optString("title")}  ${timeFormat.format(Date(start * 1000))}–${timeFormat.format(Date(end * 1000))}" to progress
    }

    // ================================================================ Inställningar

    private fun showSettings() {
        val labels = listOf(
            "Profil: ${Profiles.current(this)?.name ?: "–"} (byt)",
            "Utanför hemmet: ${Remote.describe()}",
            "Utseende: Mobil (byt till TV-läge)",
            "Sök efter uppdatering av appen",
            "Hämta listor igen",
            "Para om telefonen",
            "Om TV Hub",
            "Nytt per dag",
        )
        Look.choose(this, "Inställningar", labels) { picked ->
            if (picked == 0) {
                Profiles.choose(this) { onProfileChanged() }
                return@choose
            }
            when (picked - 1) {
                0 -> Look.choose(this, "Utanför hemmet", listOf("OK"), subtitle =
                    "Hemma på wifi pratar appen direkt med Home Assistant. Annars går den via Nabu Casa, automatiskt.\n\n" +
                        "Uppgifterna ställs in i TV Hub-adminen under Boxar och hämtas när telefonen är hemma.\n\n" +
                        "Videon kommer direkt från Toucan och använder mobildata (ungefär 2–4 GB per timme i HD).\n\n" +
                        Remote.describe()) {}
                1 -> Look.confirm(this, "Byta till TV-läge?", "TV-läget är gjort för fjärrkontroll. Du kan byta tillbaka under Inställningar → Utseende.", "Byt") {
                    prefs.layoutMode = "tv"
                    startActivity(Intent(this, MainActivity::class.java))
                    finish()
                }
                2 -> Updater.check(this, prefs, quiet = false)
                3 -> resetAll()
                6 -> NewDays.show(this)
                4 -> Look.confirm(this, "Para om telefonen?", "Du behöver en ny kod från webbadminen och måste vara hemma på wifi.", "Para om") {
                    prefs.clearPairing()
                    startActivity(Intent(this, SetupActivity::class.java))
                    finish()
                }
                5 -> Look.choose(this, "TV Hub ${BuildConfig.VERSION_NAME}", listOf("OK"), subtitle =
                    "Hemma: ${prefs.server}\nEnhet: ${prefs.deviceName}\n\n" +
                        "Tryck för att öppna, håll in för fler val (favorit, programguide).\n" +
                        "I spelaren: tryck på bilden för knapparna. Vrid telefonen för helskärm.") {}
            }
        }
    }

    // ================================================================ Hem

    private inner class HomePage : MPage() {
        private val list = RecyclerView(this@MobileActivity)
        private val adapter = ShelfAdapter(this@MobileActivity)
        override val view: View = list
        private var loadedKey = ""

        init {
            list.layoutParams = FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT)
            list.layoutManager = LinearLayoutManager(this@MobileActivity)
            adapter.titleClicks = true
            list.adapter = adapter
            list.itemAnimator = null
            list.clipToPadding = false
            list.setPadding(Look.dp(this@MobileActivity, 8), Look.dp(this@MobileActivity, 4), 0, Look.dp(this@MobileActivity, 16))
        }

        override fun onReselect() = list.smoothScrollToPosition(0)

        override fun onShow() = showWelcome()

        override fun onHomeData(data: JSONObject) {
            val key = data.toString().hashCode().toString() + ":" + MyPrograms.cached.hashCode() + ":" + Sports.cached.hashCode()
            if (key == loadedKey) return
            loadedKey = key
            adapter.submit(homeShelves(data))
            showWelcome()
        }

        /** Tom Hem-sida: förklara varför (bara när Hem visas). */
        private fun showWelcome() {
            if (current != "home" || home == null) return
            if (adapter.itemCount > 0) {
                status.visibility = View.GONE
                return
            }
            status.text = if (privateMode()) {
                "Favoriter och historik är dolda i det här läget.\nHåll in \"TV Hub\" överst för att visa dem, eller välj Live, Filmer eller Serier nedan."
            } else {
                "Välkommen till TV Hub.\nVälj Live, Filmer eller Serier nedan."
            }
            status.visibility = View.VISIBLE
            status.bringToFront()
        }
    }

    private fun homeShelves(data: JSONObject): List<Shelf> {
        val list = ArrayList<Shelf>()
        MyPrograms.cached?.takeIf { it.isNotEmpty() && data.optString("mode") != "adult" }?.let { progs ->
            list.add(Shelf("myprog", "Mina program", progs.map { MyPrograms.card(it) }))
        }
        Sports.homeCards(Sports.cached).takeIf { it.isNotEmpty() && data.optString("mode") != "adult" }?.let { cards ->
            list.add(Shelf("sport", "Sport: nu och snart", cards))
        }
        ContinueItem.list(data.optJSONArray("continue")).takeIf { it.isNotEmpty() }?.let { items ->
            list.add(Shelf("continue", "Fortsätt titta", items.map { Card.resume(it) }))
        }
        val favorites = Item.list(data.optJSONArray("favorites"))
        favorites.filter { it.type == "live" }.takeIf { it.isNotEmpty() }?.let { ch ->
            list.add(Shelf("fav_live", "Favoritkanaler", ch.map { Card.channel(it) }))
        }
        favorites.filter { it.type != "live" }.takeIf { it.isNotEmpty() }?.let { items ->
            list.add(Shelf("fav", "Mina favoriter", items.map { Card.poster(it) }))
        }
        Item.list(data.optJSONArray("recent_live")).takeIf { it.isNotEmpty() }?.let { ch ->
            list.add(Shelf("recent_live", "Senaste kanaler", ch.map { Card.channel(it) }))
        }
        Item.list(data.optJSONArray("watch_hits")).takeIf { it.isNotEmpty() }?.let { items ->
            list.add(Shelf("hits", "Nytt från dina bevakningar", items.map { if (it.type == "live") Card.channel(it) else Card.poster(it) }))
        }
        Item.list(data.optJSONArray("recent_vod")).takeIf { it.isNotEmpty() }?.let { items ->
            list.add(Shelf("new_vod", "Nytt: filmer", items.map { Card.poster(it) }))
        }
        Item.list(data.optJSONArray("recent_series")).takeIf { it.isNotEmpty() }?.let { items ->
            list.add(Shelf("new_series", "Nytt: serier", items.map { Card.poster(it) }))
        }
        Item.list(data.optJSONArray("top_vod")).takeIf { it.isNotEmpty() }?.let { items ->
            list.add(Shelf("top", "Topprankat", items.map { Card.poster(it) }))
        }
        val groups = data.optJSONArray("groups") ?: JSONArray()
        for (i in 0 until groups.length()) {
            val g = groups.optJSONObject(i) ?: continue
            val items = Item.list(g.optJSONArray("items"))
            if (items.isEmpty()) continue
            list.add(Shelf("group:${g.optInt("id")}", g.optString("name"),
                items.map { if (it.type == "live") Card.channel(it) else Card.poster(it) }))
        }
        return list
    }

    // ================================================================ kategorier som chips

    private inner class Chips(private val onPick: (Row) -> Unit) : RecyclerView.Adapter<Chips.Holder>() {
        var rows: List<Row> = emptyList()
            private set
        var selected = ""

        inner class Holder(val text: TextView) : RecyclerView.ViewHolder(text)

        fun submit(list: List<Row>) {
            rows = list
            notifyDataSetChanged()
        }

        fun select(key: String) {
            selected = key
            notifyDataSetChanged()
        }

        override fun getItemCount() = rows.size

        override fun onCreateViewHolder(parent: ViewGroup, viewType: Int) =
            Holder(LayoutInflater.from(parent.context).inflate(R.layout.chip_mobile, parent, false) as TextView)

        override fun onBindViewHolder(holder: Holder, position: Int) {
            val row = rows[position]
            holder.text.text = row.title
            holder.text.isSelected = row.key == selected
            holder.text.setOnClickListener {
                val p = holder.bindingAdapterPosition
                if (p != RecyclerView.NO_POSITION) onPick(rows[p])
            }
        }
    }

    // ================================================================ kategorival

    private fun categoryRows(data: JSONObject, type: String): List<Row> {
        val out = ArrayList<Row>()
        val cats = data.optJSONObject("categories")?.optJSONArray(type) ?: JSONArray()
        for (i in 0 until cats.length()) {
            val c = cats.optJSONObject(i) ?: continue
            out.add(Row(c.optString("name"), subtitle = c.optInt("count").takeIf { it > 0 }?.toString(),
                key = "cat:${c.optString("category_id")}", payload = c.optString("category_id")))
        }
        return out
    }

    /** Startkategorin från webbadminen (Kategorier → Start), om den finns i listan. */
    private fun startCategory(data: JSONObject, type: String, rows: List<Row>): Row? {
        val id = data.optJSONObject("start_categories")?.optStringOrNull(type) ?: return null
        return rows.firstOrNull { it.key == "cat:$id" }
    }

    private fun pickCategory(title: String, rows: List<Row>, selected: String, onPick: (Row) -> Unit) {
        val pinned = rows.filter { !it.key.startsWith("cat:") }
        val cats = rows.filter { it.key.startsWith("cat:") }
        Sheet.choose(this, title,
            pinned.map { Sheet.Entry(it.key, it.title, it.subtitle, it) },
            cats.map { Sheet.Entry(it.key, it.title, it.subtitle, it) }, selected) { entry ->
            onPick(entry.payload as Row)
        }
    }

    // ================================================================ Filmer och Serier

    private inner class BrowsePage(private val type: String) : MPage(), CardListener {
        override val view: View = LayoutInflater.from(this@MobileActivity).inflate(R.layout.page_m_list, pages, false)
        private val chips: RecyclerView = view.findViewById(R.id.chips)
        private val catButton: TextView = view.findViewById(R.id.cat_button)
        private val info: TextView = view.findViewById(R.id.info)
        private val sort: TextView = view.findViewById(R.id.sort)
        private val viewToggle: TextView = view.findViewById(R.id.view_toggle)
        private val grid: RecyclerView = view.findViewById(R.id.list)
        private val chipAdapter = Chips { selectChip(it) }
        private val gridAdapter = CardAdapter(this)
        private val rowsAdapter = ItemRows(live = false)
        private val items = ArrayList<Item>()
        private val columns = 3
        private var categoryId: String? = null
        private var chipKey = "all"
        private val params = HashMap<String, String>()
        private var total = 0
        private var loading = false
        private var token = 0
        private var built = false

        init {
            // Kategorier väljs i en lista med sökruta (knappen), inte genom att svepa.
            chips.visibility = View.GONE
            catButton.visibility = View.VISIBLE
            catButton.text = "Kategori: Alla  ▾"
            catButton.setOnClickListener {
                pickCategory(if (type == "vod") "Filmer" else "Serier", chipAdapter.rows, chipKey) { row ->
                    if (row.key == "new") MListActivity.startNew(this@MobileActivity, type) else selectChip(row)
                }
            }
            view.findViewById<View>(R.id.bar).visibility = View.VISIBLE
            viewToggle.visibility = View.VISIBLE
            chips.layoutManager = LinearLayoutManager(this@MobileActivity, LinearLayoutManager.HORIZONTAL, false)
            chips.adapter = chipAdapter
            chips.itemAnimator = null
            val gap = Look.dp(this@MobileActivity, 4)
            val width = resources.displayMetrics.widthPixels - Look.dp(this@MobileActivity, 16)
            val cardWidth = width / columns - 2 * gap
            // Affischen 2:3 och namnet under (38 dp).
            gridAdapter.itemSize = cardWidth to (cardWidth * 3 / 2 + Look.dp(this@MobileActivity, 38))
            grid.addItemDecoration(object : RecyclerView.ItemDecoration() {
                override fun getItemOffsets(outRect: Rect, view: View, parent: RecyclerView, state: RecyclerView.State) {
                    if (parent.adapter === gridAdapter) outRect.set(gap, gap, gap, gap) else outRect.set(0, 0, 0, 0)
                }
            })
            grid.itemAnimator = null
            grid.addOnScrollListener(object : RecyclerView.OnScrollListener() {
                override fun onScrolled(recyclerView: RecyclerView, dx: Int, dy: Int) {
                    val lm = grid.layoutManager as? LinearLayoutManager ?: return
                    if (!loading && chipKey != "fav" && items.size < total && lm.findLastVisibleItemPosition() >= items.size - 18) {
                        load(reset = false)
                    }
                }
            })
            sort.setOnClickListener { showSort() }
            viewToggle.setOnClickListener {
                prefs.mobileList = !prefs.mobileList
                applyView()
            }
            applyView()
            updateSort()
        }

        /** Rutnät med affischer eller lista med namn och fakta. Valet sparas. */
        private fun applyView() {
            val list = prefs.mobileList
            viewToggle.text = if (list) "Rutnät" else "Lista"
            grid.layoutManager = if (list) LinearLayoutManager(this@MobileActivity) else GridLayoutManager(this@MobileActivity, columns)
            grid.adapter = if (list) rowsAdapter else gridAdapter
            grid.invalidateItemDecorations()
        }

        private fun show(reset: Boolean, more: List<Item>) {
            if (reset) items.clear()
            items.addAll(more)
            if (reset) {
                gridAdapter.submit(items.map { Card.poster(it) })
                rowsAdapter.submit(items.toList())
                grid.scrollToPosition(0)
            } else {
                gridAdapter.append(more.map { Card.poster(it) })
                rowsAdapter.append(more)
            }
        }

        override fun onReselect() = grid.smoothScrollToPosition(0)

        override fun onHomeData(data: JSONObject) {
            if (chipKey == "fav") showFavorites()
            if (built) return
            built = true
            val rows = ArrayList<Row>()
            rows.add(Row("Alla", key = "all"))
            rows.add(Row("Favoriter", key = "fav"))
            rows.add(Row("Nytt tillagt (med datum)", key = "new"))
            rows.addAll(categoryRows(data, type))
            chipAdapter.submit(rows)
            startCategory(data, type, rows)?.let { start ->
                chipKey = start.key
                categoryId = start.payload as? String
            }
            chipAdapter.select(chipKey)
            catButton.text = "Kategori: ${rows.firstOrNull { it.key == chipKey }?.title ?: "Alla"}  ▾"
            load(reset = true)
        }

        private fun selectChip(row: Row) {
            if (row.key == chipKey) return
            chipKey = row.key
            catButton.text = "Kategori: ${row.title}  ▾"
            chipAdapter.select(row.key)
            categoryId = row.payload as? String
            if (row.key == "new") {
                params["sort"] = "added"
                params["new_only"] = "1"
            } else {
                params.remove("new_only")
            }
            updateSort()
            load(reset = true)
        }

        /** Favoriter: från Hem-datan (filmer, eller serier och favoritavsnitt). */
        private fun showFavorites() {
            val wanted = if (type == "vod") setOf("vod") else setOf("series", "episode")
            val fav = Item.list(home?.optJSONArray("favorites")).filter { it.type in wanted }
            total = fav.size
            show(reset = true, more = fav)
            info.text = when {
                fav.isNotEmpty() -> "${fav.size} favoriter"
                privateMode() -> "Favoriter är dolda i det här läget. Håll in \"TV Hub\" överst för att visa dem."
                else -> "Inga favoriter ännu. Håll in en titel och välj Lägg till i favoriter."
            }
        }

        private fun load(reset: Boolean) {
            if (chipKey == "fav") {
                showFavorites()
                return
            }
            val t = if (reset) ++token else token
            val offset = if (reset) 0 else items.size
            loading = true
            val q = StringBuilder("type=$type&offset=$offset&limit=60")
            categoryId?.let { q.append("&category_id=").append(Api.enc(it)) }
            for ((k, v) in params) q.append("&").append(k).append("=").append(Api.enc(v))
            if (reset) info.text = "Hämtar…"
            Api.obj("GET", "/device/browse?$q") { result ->
                if (t != token) return@obj
                loading = false
                result.onSuccess { data ->
                    total = data.optInt("total")
                    val more = Item.list(data.optJSONArray("items"))
                    show(reset, more)
                    if (reset) {
                        info.text = when {
                            more.isNotEmpty() -> "$total titlar"
                            params["new_only"] == "1" -> "Inget nytt ännu."
                            else -> "Inget innehåll här."
                        }
                    }
                }.onFailure { info.text = Api.friendly(it) }
            }
        }

        private fun sortOptions(): List<Pair<String, String>> = if (type == "series") {
            listOf("added" to "Senast tillagda", "rating" to "Betyg", "year" to "Årtal", "name" to "Namn")
        } else {
            listOf(
                "added" to "Senast tillagda", "rating" to "Betyg", "year" to "Årtal",
                "duration" to "Längd, kortast först", "duration_desc" to "Längd, längst först", "name" to "Namn",
            )
        }

        private fun updateSort() {
            val options = sortOptions()
            val key = params["sort"] ?: "added"
            sort.text = options.firstOrNull { it.first == key }?.second ?: "Sortera"
        }

        private fun showSort() {
            val options = sortOptions()
            val currentIndex = options.indexOfFirst { it.first == (params["sort"] ?: "added") }.coerceAtLeast(0)
            Look.choose(this@MobileActivity, "Sortera", options.map { it.second }, currentIndex) { which ->
                params["sort"] = options[which].first
                updateSort()
                load(reset = true)
            }
        }

        override fun onCardClick(card: Card, shelf: Shelf?) = this@MobileActivity.onCardClick(card, shelf)
        override fun onCardLongClick(card: Card, shelf: Shelf?) = this@MobileActivity.onCardLongClick(card, shelf)
        override fun onCardFocus(card: Card, shelf: Shelf?, shelfIndex: Int, cardIndex: Int) {}
    }

    // ================================================================ Live

    private inner class LivePage : MPage() {
        override val view: View = LayoutInflater.from(this@MobileActivity).inflate(R.layout.page_m_list, pages, false)
        private val chips: RecyclerView = view.findViewById(R.id.chips)
        private val catButton: TextView = view.findViewById(R.id.cat_button)
        private val list: RecyclerView = view.findViewById(R.id.list)
        private val chipAdapter = Chips { selectChip(it) }
        private val channels = ItemRows(live = true)
        private var chipKey = "all"
        private var categoryId: String? = null
        private var token = 0
        private var total = 0
        private var loading = false
        private var built = false

        init {
            chips.visibility = View.GONE
            catButton.visibility = View.VISIBLE
            catButton.text = "Kategori: Alla kanaler  ▾"
            catButton.setOnClickListener {
                pickCategory("Kanaler", chipAdapter.rows, chipKey) { row ->
                    if (row.key == "new") MListActivity.startNew(this@MobileActivity, "live") else selectChip(row)
                }
            }
            list.layoutManager = LinearLayoutManager(this@MobileActivity)
            list.adapter = channels
            list.itemAnimator = null
            list.addOnScrollListener(object : RecyclerView.OnScrollListener() {
                override fun onScrollStateChanged(recyclerView: RecyclerView, newState: Int) {
                    if (newState == RecyclerView.SCROLL_STATE_IDLE) channels.refreshNow(list)
                }

                override fun onScrolled(recyclerView: RecyclerView, dx: Int, dy: Int) {
                    val lm = list.layoutManager as LinearLayoutManager
                    if (!loading && chipKey != "fav" && channels.items.size < total &&
                        lm.findLastVisibleItemPosition() >= channels.items.size - 30) load(reset = false)
                }
            })
        }

        override fun onReselect() = list.smoothScrollToPosition(0)

        override fun onHomeData(data: JSONObject) {
            if (built) return
            built = true
            val rows = ArrayList<Row>()
            rows.add(Row("Alla kanaler", key = "all"))
            if (Item.list(data.optJSONArray("favorites")).any { it.type == "live" }) rows.add(Row("Favoriter", key = "fav"))
            rows.add(Row("Nytt tillagt (med datum)", key = "new"))
            rows.addAll(categoryRows(data, "live"))
            chipAdapter.submit(rows)
            startCategory(data, "live", rows)?.let { start ->
                chipKey = start.key
                categoryId = start.payload as? String
            }
            chipAdapter.select(chipKey)
            catButton.text = "Kategori: ${rows.firstOrNull { it.key == chipKey }?.title ?: "Alla kanaler"}  ▾"
            load(reset = true)
        }

        private fun selectChip(row: Row) {
            if (row.key == chipKey) return
            chipKey = row.key
            catButton.text = "Kategori: ${row.title}  ▾"
            chipAdapter.select(row.key)
            categoryId = row.payload as? String
            load(reset = true)
        }

        private fun load(reset: Boolean) {
            if (chipKey == "fav") {
                val fav = Item.list(home?.optJSONArray("favorites")).filter { it.type == "live" }
                channels.submit(fav)
                total = fav.size
                list.post { channels.refreshNow(list) }
                return
            }
            val t = if (reset) ++token else token
            val offset = if (reset) 0 else channels.items.size
            loading = true
            val q = StringBuilder("type=live&sort=num&offset=$offset&limit=200")
            categoryId?.let { q.append("&category_id=").append(Api.enc(it)) }
            Api.obj("GET", "/device/browse?$q") { result ->
                if (t != token) return@obj
                loading = false
                result.onSuccess { data ->
                    total = data.optInt("total")
                    val items = Item.list(data.optJSONArray("items"))
                    if (reset) {
                        channels.submit(items)
                        list.scrollToPosition(0)
                    } else {
                        channels.append(items)
                    }
                    list.post { channels.refreshNow(list) }
                }.onFailure { Toast.makeText(this@MobileActivity, Api.friendly(it), Toast.LENGTH_LONG).show() }
            }
        }
    }

    // ================================================================ Sök

    private inner class SearchPage : MPage() {
        override val view: View = LayoutInflater.from(this@MobileActivity).inflate(R.layout.page_m_list, pages, false)
        private val query: EditText = view.findViewById(R.id.query)
        private val info: TextView = view.findViewById(R.id.info)
        private val list: RecyclerView = view.findViewById(R.id.list)
        private val results = ItemRows(live = false)
        private var token = 0
        private val searchTask = Runnable { search() }

        fun setQuery(text: String) {
            query.setText(text)
            query.setSelection(text.length)
            handler.removeCallbacks(searchTask)
            search()
            hideKeyboard()
        }

        init {
            query.visibility = View.VISIBLE
            view.findViewById<View>(R.id.bar).visibility = View.VISIBLE
            view.findViewById<View>(R.id.sort).visibility = View.GONE
            // Röstsök: samma Google-ruta som på TV:n.
            if (Voice.available(this@MobileActivity)) {
                view.findViewById<TextView>(R.id.view_toggle).let {
                    it.text = "🎤 Röst"
                    it.visibility = View.VISIBLE
                    it.setOnClickListener { Voice.start(this@MobileActivity) }
                }
            }
            list.layoutManager = LinearLayoutManager(this@MobileActivity)
            list.adapter = results
            list.itemAnimator = null
            info.text = prefs.recentSearches.takeIf { it.isNotEmpty() }?.let { "Senast: " + it.take(4).joinToString(", ") } ?: ""
            query.addTextChangedListener(object : TextWatcher {
                override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) {}
                override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) {}
                override fun afterTextChanged(s: Editable?) {
                    handler.removeCallbacks(searchTask)
                    if ((s?.trim()?.length ?: 0) >= 2) handler.postDelayed(searchTask, 500)
                }
            })
            query.setOnEditorActionListener { _, actionId, _ ->
                if (actionId == EditorInfo.IME_ACTION_SEARCH) {
                    handler.removeCallbacks(searchTask)
                    search()
                    hideKeyboard()
                    true
                } else {
                    false
                }
            }
            list.addOnScrollListener(object : RecyclerView.OnScrollListener() {
                override fun onScrollStateChanged(recyclerView: RecyclerView, newState: Int) {
                    if (newState == RecyclerView.SCROLL_STATE_DRAGGING) hideKeyboard()
                }
            })
        }

        override fun onShow() {
            if (query.text.isEmpty()) {
                query.requestFocus()
                query.post {
                    (getSystemService(Context.INPUT_METHOD_SERVICE) as InputMethodManager)
                        .showSoftInput(query, InputMethodManager.SHOW_IMPLICIT)
                }
            }
        }

        private fun search() {
            val q = query.text.toString().trim()
            if (q.length < 2) return
            val t = ++token
            info.text = "Söker…"
            Api.arr("/device/search?q=${Api.enc(q)}&limit=120") { result ->
                if (t != token) return@arr
                result.onSuccess { array ->
                    val items = Item.list(array)
                    results.submit(items)
                    list.scrollToPosition(0)
                    info.text = if (items.isEmpty()) "Inga träffar för \"$q\"." else "${items.size} träffar"
                    if (items.isNotEmpty()) prefs.recentSearches = listOf(q) + prefs.recentSearches.filter { it != q }
                }.onFailure { info.text = Api.friendly(it) }
            }
        }
    }

    /** Rader med bild, namn och text. Kanaler visar vad som sänds nu. */
    private inner class ItemRows(private val live: Boolean) : RecyclerView.Adapter<ItemRows.Holder>() {
        var items: List<Item> = emptyList()
            private set
        private val now = HashMap<String, JSONObject?>()

        inner class Holder(view: View) : RecyclerView.ViewHolder(view) {
            val art: ImageView = view.findViewById(R.id.art)
            val fallback: TextView = view.findViewById(R.id.fallback)
            val title: TextView = view.findViewById(R.id.title)
            val subtitle: TextView = view.findViewById(R.id.subtitle)
            val progress: ProgressBar = view.findViewById(R.id.progress)
            val star: TextView = view.findViewById(R.id.star)
        }

        fun submit(list: List<Item>) {
            items = list
            notifyDataSetChanged()
        }

        fun append(more: List<Item>) {
            val start = items.size
            items = items + more
            notifyItemRangeInserted(start, more.size)
        }

        fun refreshNow(list: RecyclerView) {
            val lm = list.layoutManager as? LinearLayoutManager ?: return
            val first = lm.findFirstVisibleItemPosition().coerceAtLeast(0)
            val last = lm.findLastVisibleItemPosition().let { if (it < 0) first + 12 else it + 6 }
            val ids = (first..last.coerceAtMost(items.size - 1))
                .mapNotNull { items.getOrNull(it)?.takeIf { item -> item.type == "live" }?.id }
                .filter { !now.containsKey(it) }
            if (ids.isEmpty()) return
            ids.forEach { now[it] = null }
            Api.obj("GET", "/device/epg-now?ids=${Api.enc(ids.take(40).joinToString(","))}") { result ->
                result.onSuccess { data ->
                    for (id in ids) now[id] = data.optJSONObject(id)
                    notifyDataSetChanged()
                }
            }
        }

        override fun getItemCount() = items.size

        override fun onCreateViewHolder(parent: ViewGroup, viewType: Int) =
            Holder(LayoutInflater.from(parent.context).inflate(R.layout.row_channel_m, parent, false))

        override fun onBindViewHolder(holder: Holder, position: Int) {
            val item = items[position]
            holder.title.text = if (live) listOfNotNull(item.num?.toString(), item.name).joinToString("  ") else item.name
            holder.fallback.text = item.name.take(14)
            holder.fallback.visibility = View.VISIBLE
            ImageLoader.load(holder.art, item.icon) { ok -> if (ok) holder.fallback.visibility = View.INVISIBLE }
            if (item.type == "live") {
                val (text, progress) = nowText(now[item.id]?.optJSONObject("now"))
                holder.subtitle.text = text ?: (if (live) item.categoryName ?: "" else "Kanal")
                holder.progress.progress = progress.coerceAtLeast(0)
                holder.progress.visibility = if (progress >= 0) View.VISIBLE else View.GONE
            } else {
                holder.subtitle.text = listOf(item.typeLabel, item.facts).filter { it.isNotEmpty() }.joinToString(" · ")
                holder.progress.visibility = View.GONE
            }
            holder.star.visibility = if (item.favorite && !privateMode()) View.VISIBLE else View.GONE
            holder.itemView.setOnClickListener {
                val p = holder.bindingAdapterPosition
                if (p == RecyclerView.NO_POSITION) return@setOnClickListener
                val chosen = items[p]
                if (chosen.type == "live") playChannel(chosen, items.filter { it.type == "live" }) else openItem(chosen, items)
            }
            holder.itemView.setOnLongClickListener {
                val p = holder.bindingAdapterPosition
                if (p != RecyclerView.NO_POSITION) showItemOptions(items[p])
                true
            }
        }
    }
}
