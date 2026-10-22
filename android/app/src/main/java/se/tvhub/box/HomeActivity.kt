package se.tvhub.box

import android.app.Activity
import android.content.Intent
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.view.KeyEvent
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.TextView
import android.widget.Toast
import androidx.recyclerview.widget.GridLayoutManager
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.LinearSmoothScroller
import androidx.recyclerview.widget.RecyclerView
import org.json.JSONArray
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * Det moderna utseendet (som Apple TV, Netflix och Google TV).
 *
 *  - Smal meny med ikoner till vänster. Den fälls ut med texter när den har fokus.
 *  - Hem: stor bild överst med det markerade, och rader med Fortsätt titta, favoritkanaler,
 *    nytt, topprankat, grupper och sparade filter.
 *  - Live-TV, Filmer, Serier, Sök (eget bokstavsrutnät), Inspelningar och Inställningar.
 *  - Dolda knapptryck på Inställningar som i det klassiska utseendet:
 *    vänster tre gånger = vuxenläge, höger tre gånger (i vuxenläge) = visa/dölj historik.
 *  - Tillbaka två gånger i menyn stänger appen.
 *
 * Det klassiska utseendet (MainActivity) finns kvar och väljs under Inställningar → Utseende.
 */
class HomeActivity : Activity(), CardListener {
    companion object {
        private const val PAGE = 120
    }

    private data class Nav(val key: String, val label: String, val icon: Int)

    private val navs = listOf(
        Nav("home", "Hem", R.drawable.ic_m_home),
        Nav("night", "Filmkväll", R.drawable.ic_m_night),
        Nav("search", "Sök", R.drawable.ic_m_search),
        Nav("favorites", "Favoriter", R.drawable.ic_m_star),
        Nav("live", "Live-TV", R.drawable.ic_m_live),
        Nav("vod", "Filmer", R.drawable.ic_m_movie),
        Nav("series", "Serier", R.drawable.ic_m_series),
        Nav("recordings", "Inspelningar", R.drawable.ic_m_record),
        Nav("media", "Mina media", R.drawable.ic_m_folder),
        Nav("settings", "Inställningar", R.drawable.ic_m_settings),
    )

    private lateinit var prefs: Prefs
    private lateinit var rail: LinearLayout
    private lateinit var container: FrameLayout
    private lateinit var heroBackdrop: ImageView
    private lateinit var scrimLeft: View
    private lateinit var scrimBottom: View
    private lateinit var status: TextView
    private val handler = Handler(Looper.getMainLooper())
    private val railViews = HashMap<String, View>()
    private var railExpanded = false

    private var home: JSONObject? = null
    private var shownAdult = false
    private var current = ""
    private var navTask: Runnable? = null
    private var retryTask: Runnable? = null
    private val timeFormat = SimpleDateFormat("HH:mm", Locale("sv", "SE"))

    private var homePage: HomePage? = null
    private var livePage: LivePage? = null
    private var vodPage: BrowsePage? = null
    private var seriesPage: BrowsePage? = null
    private var favoritesPage: BrowsePage? = null
    private var mediaPage: MediaPage? = null
    private var searchPage: SearchPage? = null
    private var recordingsPage: RecordingsPage? = null

    // Dolda knapptryck och Tillbaka två gånger
    private var secretPresses = 0
    private var secretStartedAt = 0L
    private var historyPresses = 0
    private var historyStartedAt = 0L
    private var backPressedAt = 0L

    // Fjärrkontrollens knappar: vad som är markerat just nu, och sifferknapparna.
    private var focusedItem: Item? = null
    private var focusedPage = ""
    private val digits by lazy { DigitEntry(this) { onDigits(it) } }

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
        if (FormFactor.isMobile(this)) {
            startActivity(Intent(this, MobileActivity::class.java))
            finish()
            return
        }
        Control.start(this)
        ImageLoader.appContext = applicationContext
        Fonts.install(this)
        setContentView(R.layout.activity_home)
        rail = findViewById(R.id.rail)
        container = findViewById(R.id.page_container)
        heroBackdrop = findViewById(R.id.hero_backdrop)
        scrimLeft = findViewById(R.id.hero_scrim_left)
        scrimBottom = findViewById(R.id.hero_scrim_bottom)
        status = findViewById(R.id.status)
        buildRail()
        rail.viewTreeObserver.addOnGlobalFocusChangeListener { _, newFocus ->
            setRailExpanded(newFocus != null && isInRail(newFocus))
        }
        // Påminnelse som öppnade appen.
        handler.post { Control.handleIntent(this, intent) }
        // Snabbstart: visa Hem från förra gången direkt, uppdatera i bakgrunden (onResume → loadHome).
        HomeCache.read(this)?.let { cached ->
            home = cached
            shownAdult = false
            showPage("home")
            homePage?.focusEntry()
        }
    }

    override fun onResume() {
        super.onResume()
        if (!::rail.isInitialized) return
        if (shownAdult && !prefs.adultMode) {
            resetMode()
            return
        }
        loadHome()
        Taste.promptPending(this)
    }

    /** USB-disk i eller ur: Mina media uppdateras. */
    private val mountReceiver = object : android.content.BroadcastReceiver() {
        override fun onReceive(context: android.content.Context, intent: Intent) {
            if (intent.action == Intent.ACTION_MEDIA_MOUNTED && Media.usbAllowed(this@HomeActivity)) {
                Toast.makeText(this@HomeActivity, "USB-disk ansluten. Den finns under Mina media.", Toast.LENGTH_LONG).show()
            }
            handler.postDelayed({ mediaPage?.refreshSources(discover = false) }, 800)
        }
    }

    override fun onStart() {
        super.onStart()
        val filter = android.content.IntentFilter().apply {
            addAction(Intent.ACTION_MEDIA_MOUNTED)
            addAction(Intent.ACTION_MEDIA_UNMOUNTED)
            addAction(Intent.ACTION_MEDIA_REMOVED)
            addAction(Intent.ACTION_MEDIA_EJECT)
            addDataScheme("file")
        }
        try {
            if (android.os.Build.VERSION.SDK_INT >= 33) registerReceiver(mountReceiver, filter, android.content.Context.RECEIVER_EXPORTED)
            else registerReceiver(mountReceiver, filter)
        } catch (_: Exception) {
        }
    }

    override fun onStop() {
        try {
            unregisterReceiver(mountReceiver)
        } catch (_: Exception) {
        }
        super.onStop()
    }

    @Deprecated("Deprecated in Java")
    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == Media.PERMISSION_REQUEST) mediaPage?.refreshSources(discover = false)
    }

    override fun onDestroy() {
        handler.removeCallbacksAndMessages(null)
        super.onDestroy()
    }

    // ================================================================ menyn till vänster

    private fun buildRail() {
        rail.removeAllViews()
        for (nav in navs) {
            val item = LayoutInflater.from(this).inflate(R.layout.rail_item, rail, false)
            item.findViewById<ImageView>(R.id.icon).setImageResource(nav.icon)
            item.findViewById<TextView>(R.id.title).text = nav.label
            item.tag = nav.key
            item.setOnClickListener { onRailClick(nav.key) }
            item.setOnFocusChangeListener { v, hasFocus ->
                tintRail(v, hasFocus)
                if (hasFocus) onRailFocus(nav.key)
            }
            item.setOnKeyListener { _, keyCode, event ->
                if (event.action == KeyEvent.ACTION_DOWN &&
                    (keyCode == KeyEvent.KEYCODE_DPAD_DOWN || keyCode == KeyEvent.KEYCODE_DPAD_UP)) {
                    // Upp/ner i menyn går alltid till nästa menyval. Annars kan Android välja något på
                    // sidan bakom den utfällda menyn (t.ex. sökens tangentbord, som ligger precis där).
                    val index = navs.indexOf(nav) + if (keyCode == KeyEvent.KEYCODE_DPAD_DOWN) 1 else -1
                    navs.getOrNull(index)?.let { railViews[it.key]?.requestFocus() }
                    true
                } else if (event.action == KeyEvent.ACTION_DOWN && keyCode == KeyEvent.KEYCODE_DPAD_RIGHT && nav.key != "settings" && nav.key != "night") {
                    navTask?.let { handler.removeCallbacks(it) }
                    if (current != nav.key) showPage(nav.key)
                    focusPage()
                } else {
                    false
                }
            }
            tintRail(item, false)
            rail.addView(item)
            railViews[nav.key] = item
        }
    }

    private fun tintRail(view: View, focused: Boolean) {
        val icon = view.findViewById<ImageView>(R.id.icon)
        val active = view.isActivated
        icon.setColorFilter(
            when {
                focused -> 0xFF0A0A0C.toInt()
                active -> 0xFFFFFFFF.toInt()
                else -> 0xFF8E8E96.toInt()
            }
        )
    }

    private fun isInRail(view: View): Boolean {
        var v: View? = view
        while (v != null) {
            if (v === rail) return true
            v = v.parent as? View
        }
        return false
    }

    private fun setRailExpanded(expanded: Boolean) {
        if (expanded == railExpanded) return
        railExpanded = expanded
        rail.background = if (expanded) getDrawable(R.drawable.m_rail_bg) else null
        for (i in 0 until rail.childCount) {
            rail.getChildAt(i).findViewById<TextView>(R.id.title).visibility = if (expanded) View.VISIBLE else View.GONE
        }
    }

    private fun onRailFocus(key: String) {
        navTask?.let { handler.removeCallbacks(it) }
        if (key == "settings" || key == "night" || key == current) return
        val task = Runnable { showPage(key) }
        navTask = task
        handler.postDelayed(task, 350)
    }

    private fun onRailClick(key: String) {
        navTask?.let { handler.removeCallbacks(it) }
        if (key == "settings") {
            showSettings()
            return
        }
        if (key == "night") {
            FilmNightActivity.start(this)
            return
        }
        if (key != current) showPage(key)
        focusPage()
    }

    private fun markRail(key: String) {
        for ((k, v) in railViews) {
            v.isActivated = k == key
            tintRail(v, v.hasFocus())
        }
    }

    private fun focusRail() {
        (railViews[current] ?: railViews["home"])?.requestFocus()
    }

    // ================================================================ sidor

    private abstract inner class Page {
        abstract val view: View
        open val usesHero = false
        open fun onShow() {}
        abstract fun focusEntry(): Boolean
        open fun onHomeData(data: JSONObject) {}
        /** Grupper-knappen: välj kategori i en lista. false om sidan saknar kategorier. */
        open fun chooseGroup(): Boolean = false
        /** Pilknapp som annars skulle hamna i menyn (eller på fel ställe). true = hanterad. */
        open fun onEdge(focused: View, keyCode: Int): Boolean = false
        /** Tillbaka på sidan (t.ex. upp en mapp). true = hanterad. */
        open fun onBack(): Boolean = false
    }

    private fun showPage(key: String) {
        val page: Page = when (key) {
            "home" -> homePage ?: HomePage().also { homePage = it }
            "live" -> livePage ?: LivePage().also { livePage = it }
            "vod" -> vodPage ?: BrowsePage("vod").also { vodPage = it }
            "series" -> seriesPage ?: BrowsePage("series").also { seriesPage = it }
            "favorites" -> favoritesPage ?: BrowsePage("favorites").also { favoritesPage = it }
            "search" -> searchPage ?: SearchPage().also { searchPage = it }
            "recordings" -> recordingsPage ?: RecordingsPage().also { recordingsPage = it }
            "media" -> mediaPage ?: MediaPage().also { mediaPage = it }
            else -> return
        }
        current = key
        markRail(key)
        container.removeAllViews()
        container.addView(page.view)
        val hero = if (page.usesHero) View.VISIBLE else View.GONE
        heroBackdrop.visibility = hero
        scrimLeft.visibility = hero
        scrimBottom.visibility = hero
        home?.let { page.onHomeData(it) }
        page.onShow()
    }

    private fun currentPage(): Page? = when (current) {
        "home" -> homePage
        "live" -> livePage
        "vod" -> vodPage
        "series" -> seriesPage
        "favorites" -> favoritesPage
        "search" -> searchPage
        "recordings" -> recordingsPage
        "media" -> mediaPage
        else -> null
    }

    private fun focusPage(): Boolean = currentPage()?.focusEntry() ?: false

    // ================================================================ hemdata

    private fun loadHome() {
        Api.obj("GET", "/device/home?app_version=${Api.enc(BuildConfig.VERSION_NAME)}") { result ->
            result.onSuccess { data ->
                if (prefs.adultMode && data.optString("mode") != "adult") prefs.adultMode = false
                if (data.optString("mode") != "adult") HomeCache.write(this, data)
                shownAdult = data.optString("mode") == "adult"
                val first = home == null
                home = data
                status.visibility = View.GONE
                retryTask?.let { handler.removeCallbacks(it) }
                if (first || current.isEmpty()) {
                    showPage("home")
                    homePage?.focusEntry()
                } else {
                    currentPage()?.onHomeData(data)
                }
                if (!Session.updateChecked) {
                    Session.updateChecked = true
                    Updater.check(this, prefs, quiet = true)
                }
            }.onFailure { handleError(it) }
        }
    }

    /** Hem från förra gången, för snabbstart. Bara vanligt läge sparas. */
    private object HomeCache {
        private const val MAX_AGE_MS = 7 * 24 * 3600 * 1000L

        private fun file(context: android.content.Context) = java.io.File(context.filesDir, "home_cache.json")

        fun read(context: android.content.Context): JSONObject? {
            if (Prefs(context).adultMode) return null
            return try {
                val f = file(context)
                if (!f.isFile || System.currentTimeMillis() - f.lastModified() > MAX_AGE_MS) return null
                JSONObject(f.readText()).takeIf { it.optString("mode") != "adult" }
            } catch (_: Exception) {
                null
            }
        }

        fun write(context: android.content.Context, data: JSONObject) {
            val text = data.toString()
            val f = file(context)
            Api.async({ f.writeText(text) }) {}
        }
    }

    private fun handleError(error: Throwable) {
        if (error is ApiException && error.status == 401) {
            Toast.makeText(this, "Boxen behöver paras om.", Toast.LENGTH_LONG).show()
            prefs.clearPairing()
            startActivity(Intent(this, SetupActivity::class.java))
            finish()
            return
        }
        if (home == null) {
            status.text = "${Api.friendly(error)}\nFörsöker igen automatiskt…"
            status.visibility = View.VISIBLE
            retryTask?.let { handler.removeCallbacks(it) }
            val task = Runnable { loadHome() }
            retryTask = task
            handler.postDelayed(task, 10_000)
        } else {
            Toast.makeText(this, Api.friendly(error), Toast.LENGTH_LONG).show()
        }
    }

    private fun resetMode() {
        home = null
        current = ""
        homePage = null
        livePage = null
        vodPage = null
        seriesPage = null
        favoritesPage = null
        mediaPage = null
        searchPage = null
        recordingsPage = null
        container.removeAllViews()
        loadHome()
    }

    private fun privateMode(): Boolean = home?.optString("mode") == "adult" && home?.optBoolean("history", true) == false

    // ================================================================ kort

    override fun onCardClick(card: Card, shelf: Shelf?) {
        when (val p = card.payload) {
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

    override fun onCardFocus(card: Card, shelf: Shelf?, shelfIndex: Int, cardIndex: Int) {
        markFocused(card.payload as? Item)
        if (current == "home") homePage?.onFocus(card, shelfIndex)
        if (current == "search") searchPage?.onFocus(shelfIndex)
    }

    override fun onCardLongClick(card: Card, shelf: Shelf?) {
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
            else -> {
                val channels = siblings.filter { it.type == "live" }.ifEmpty { listOf(item) }
                PlayerActivity.start(
                    this, type = "live", id = item.id, name = item.name, ext = null,
                    queueIds = ArrayList(channels.map { it.id }),
                    queueNames = ArrayList(channels.map { it.name }),
                    queueExts = null,
                    queueIndex = channels.indexOfFirst { it.id == item.id },
                    icon = item.icon,
                )
            }
        }
    }

    private fun showItemOptions(item: Item) {
        val options = ArrayList<Pair<String, () -> Unit>>()
        when (item.type) {
            "live" -> options.add("Programguide, tidigare program och inspelning" to { GuideActivity.start(this, item.id, item.name) })
            "vod" -> {
                options.add("Detaljer" to { DetailActivity.start(this, item.id, item.name) })
                options.add("⬇ Spela in till USB-minnet" to { RecordMedia.film(this, item.id) })
            }
            "series" -> {
                options.add("Avsnitt" to { SeriesActivity.start(this, item.id, item.name) })
                options.add("⬇ Spela in hela serien till USB-minnet" to { RecordMedia.series(this, item.id) })
            }
            "episode" -> {
                options.add("▶ Spela" to { playFavoriteEpisode(this, item) })
                item.seriesId?.let { sid -> options.add("Visa serien" to { SeriesActivity.start(this, sid, item.categoryName ?: item.name, item.id) }) }
            }
        }
        if (!privateMode()) {
            options.add((if (item.favorite) "Ta bort från favoriter" else "Lägg till i favoriter") to { toggleFavorite(item) })
        }
        options.addAll(Taste.options(this, item) { loadHome() })
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

    /** "Nu: Rapport  20:00–20:30" och förlopp 0–1000 för kanalkort. */
    private fun nowText(now: JSONObject?): Pair<String?, Int> {
        if (now == null) return null to -1
        val start = now.optLong("start")
        val end = now.optLong("end")
        val t = System.currentTimeMillis() / 1000
        val progress = if (end > start && t in start..end) ((t - start) * 1000 / (end - start)).toInt() else -1
        return "${now.optString("title")}  ${timeFormat.format(Date(start * 1000))}–${timeFormat.format(Date(end * 1000))}" to progress
    }

    private fun fetchNow(ids: List<String>, done: (JSONObject) -> Unit) {
        if (ids.isEmpty()) return
        Api.obj("GET", "/device/epg-now?ids=${Api.enc(ids.take(40).joinToString(","))}") { result ->
            result.onSuccess { done(it) }
        }
    }

    // ================================================================ Hem

    private inner class HomePage : Page() {
        override val view: View = LayoutInflater.from(this@HomeActivity).inflate(R.layout.page_home, container, false)
        override val usesHero = true
        private val kicker: TextView = view.findViewById(R.id.hero_kicker)
        private val title: TextView = view.findViewById(R.id.hero_title)
        private val meta: TextView = view.findViewById(R.id.hero_meta)
        private val plot: TextView = view.findViewById(R.id.hero_plot)
        private val heroProgress: ProgressBar = view.findViewById(R.id.hero_progress)
        private val shelves: RecyclerView = view.findViewById(R.id.shelves)
        // Androids egen rullning till det markerade stängs av: raden läggs alltid direkt under den
        // stora bilden av scrollShelfToTop. Annars drar de åt olika håll och sidan hoppar.
        private val manager = object : LinearLayoutManager(this@HomeActivity) {
            override fun requestChildRectangleOnScreen(
                parent: RecyclerView, child: View, rect: android.graphics.Rect, immediate: Boolean, focusedChildVisible: Boolean,
            ): Boolean = false
        }
        private val adapter = ShelfAdapter(this@HomeActivity).also { it.style = ViewStyle.TITLE }
        private var heroTask: Runnable? = null
        private var lastShelf = -1
        private var focusedKey: Pair<String, String>? = null
        private var loadedKey = ""

        init {
            shelves.layoutManager = manager
            shelves.adapter = adapter
            shelves.itemAnimator = null
        }

        override fun onHomeData(data: JSONObject) {
            val key = data.toString().hashCode().toString()
            if (key == loadedKey) return
            loadedKey = key
            build(data)
        }

        override fun focusEntry(): Boolean {
            val target = focusedKey
            if (target != null && restore(target)) return true
            return focusShelf(0, 0)
        }

        /** Tillbaka på Hem: upp till första raden. false om man redan är där. */
        fun goTop(): Boolean {
            if (lastShelf <= 0 || adapter.itemCount == 0) return false
            lastShelf = 0
            return focusShelf(0, 0)
        }

        fun focusFirst(): Boolean = focusShelf(0, 0)

        private fun build(data: JSONObject) {
            val list = ArrayList<Shelf>()
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
                list.add(Shelf("new_vod", "Nytt: filmer", items.map { Card.poster(it, "Ny film") }))
            }
            Item.list(data.optJSONArray("recent_series")).takeIf { it.isNotEmpty() }?.let { items ->
                list.add(Shelf("new_series", "Nytt: serier", items.map { Card.poster(it, "Ny serie") }))
            }
            Item.list(data.optJSONArray("top_vod")).takeIf { it.isNotEmpty() }?.let { items ->
                list.add(Shelf("top", "Topprankat", items.map { Card.poster(it, "Topprankat") }))
            }
            val groups = data.optJSONArray("groups") ?: JSONArray()
            for (i in 0 until groups.length()) {
                val g = groups.optJSONObject(i) ?: continue
                val items = Item.list(g.optJSONArray("items"))
                if (items.isEmpty()) continue
                list.add(Shelf("group:${g.optInt("id")}", g.optString("name"),
                    items.map { if (it.type == "live") Card.channel(it) else Card.poster(it, g.optString("name")) }))
            }
            adapter.submit(list)
            if (focusedKey == null) list.firstOrNull()?.cards?.firstOrNull()?.hero?.let { showHero(it) }
            if (list.isEmpty()) {
                title.text = "Välkommen till TV Hub"
                meta.text = "Välj Live-TV, Filmer eller Serier i menyn till vänster."
                plot.text = ""
            }
            loadSavedFilters(data)
            refreshChannels()
            val target = focusedKey
            if (target != null && currentFocus != null && !isInRail(currentFocus!!)) shelves.post { restore(target) }
        }

        /** Sparade filter blir egna rader längst ner. */
        private fun loadSavedFilters(data: JSONObject) {
            val filters = data.optJSONArray("saved_filters") ?: return
            for (i in 0 until filters.length()) {
                val f = filters.optJSONObject(i) ?: continue
                val type = f.optString("type", "vod")
                val params = f.optJSONObject("params") ?: JSONObject()
                val q = StringBuilder("type=$type&limit=30")
                for (name in params.keys()) q.append("&").append(name).append("=").append(Api.enc(params.optString(name)))
                val shelfKey = "filter:${f.optInt("id")}"
                Api.obj("GET", "/device/browse?$q") { result ->
                    result.onSuccess { res ->
                        val items = Item.list(res.optJSONArray("items"))
                        if (items.isNotEmpty() && adapter.shelves.none { it.key == shelfKey }) {
                            adapter.insert(Shelf(shelfKey, f.optString("name"), items.map {
                                if (it.type == "live") Card.channel(it) else Card.poster(it, f.optString("name"))
                            }), adapter.shelves.size)
                        }
                    }
                }
            }
        }

        /** Nu-program och förlopp på kanalkorten. */
        private fun refreshChannels() {
            val ids = adapter.shelves.flatMap { s -> s.cards.filter { it.kind == Card.CHANNEL }.mapNotNull { (it.payload as? Item)?.id } }.distinct()
            fetchNow(ids) { now ->
                for (shelf in adapter.shelves.toList()) {
                    if (shelf.cards.none { it.kind == Card.CHANNEL }) continue
                    val cards = shelf.cards.map { card ->
                        val item = card.payload as? Item
                        if (card.kind != Card.CHANNEL || item == null) {
                            card
                        } else {
                            val (text, progress) = nowText(now.optJSONObject(item.id)?.optJSONObject("now"))
                            Card.channel(item, text, progress)
                        }
                    }
                    adapter.updateShelf(shelf.key, Shelf(shelf.key, shelf.title, cards))
                }
            }
        }

        fun onFocus(card: Card, shelfIndex: Int) {
            focusedKey = (adapter.shelves.getOrNull(shelfIndex)?.key ?: "") to card.key
            if (shelfIndex != lastShelf) {
                lastShelf = shelfIndex
                scrollShelfToTop(shelfIndex)
            }
            heroTask?.let { handler.removeCallbacks(it) }
            val task = Runnable { showHero(card.hero) }
            heroTask = task
            handler.postDelayed(task, 160)
        }

        private fun showHero(hero: Hero?) {
            if (hero == null) return
            kicker.text = hero.kicker ?: ""
            title.text = hero.title
            meta.text = hero.meta ?: ""
            plot.text = hero.plot ?: ""
            if (hero.progress >= 0) {
                heroProgress.progress = hero.progress
                heroProgress.visibility = View.VISIBLE
            } else {
                heroProgress.visibility = View.GONE
            }
            ImageLoader.load(heroBackdrop, hero.backdrop, ImageLoader.BACKDROP, fade = true)
        }

        /** Den markerade raden hamnar överst, direkt under den stora bilden (som Netflix). */
        private fun scrollShelfToTop(index: Int) {
            if (Look.lite || index == 0) {
                manager.scrollToPositionWithOffset(index, 0)
                return
            }
            val scroller = object : LinearSmoothScroller(this@HomeActivity) {
                override fun getVerticalSnapPreference(): Int = SNAP_TO_START
            }
            scroller.targetPosition = index
            manager.startSmoothScroll(scroller)
        }

        private fun focusShelf(shelfIndex: Int, cardIndex: Int, attempt: Int = 0): Boolean {
            if (adapter.itemCount == 0) return false
            if (attempt == 0) manager.scrollToPositionWithOffset(shelfIndex, 0)
            // Listan ritas först i nästa bildruta; försök igen några gånger tills raden finns.
            handler.postDelayed({
                val holder = shelves.findViewHolderForAdapterPosition(shelfIndex) as? ShelfAdapter.Holder
                if (holder == null) {
                    if (attempt < 6) focusShelf(shelfIndex, cardIndex, attempt + 1)
                    return@postDelayed
                }
                holder.cards.scrollToPosition(cardIndex)
                handler.postDelayed({
                    holder.cards.findViewHolderForAdapterPosition(cardIndex)?.itemView?.requestFocus()
                        ?: holder.cards.requestFocus()
                }, 30)
            }, if (attempt == 0) 16L else 60L)
            return true
        }

        private fun restore(target: Pair<String, String>): Boolean {
            val shelfIndex = adapter.shelves.indexOfFirst { it.key == target.first }
            if (shelfIndex < 0) return false
            val cardIndex = adapter.shelves[shelfIndex].cards.indexOfFirst { it.key == target.second }.coerceAtLeast(0)
            return focusShelf(shelfIndex, cardIndex)
        }
    }

    // ================================================================ Filmer och Serier

    /**
     * Filmer, Serier och Favoriter: grupper överst, rutnät eller lista nedanför.
     * Favoriter: grupperna är Filmer, Serier, Avsnitt och Kanaler (från hemdatan, ingen sökning).
     */
    private inner class BrowsePage(private val type: String) : Page(), CardListener {
        override val view: View = LayoutInflater.from(this@HomeActivity).inflate(R.layout.page_browse, container, false)
        private val pageTitle: TextView = view.findViewById(R.id.page_title)
        private val sortButton: TextView = view.findViewById(R.id.sort_button)
        private val viewButton: TextView = view.findViewById(R.id.view_button)
        private val filterButton: TextView = view.findViewById(R.id.filter_button)
        private val chips: RecyclerView = view.findViewById(R.id.chips)
        private val info: TextView = view.findViewById(R.id.info)
        private val grid: RecyclerView = view.findViewById(R.id.grid)
        private val chipAdapter = RowAdapter(
            onClick = { row, _ -> selectChip(row, focusGrid = true) },
            onFocus = { row, _ -> previewChip(row) },
        )
        private val gridAdapter = CardAdapter(this)
        private val gridManager: GridLayoutManager
        private val columns: Int
        private val favoritesOnly = type == "favorites"
        private var style = prefs.viewStyle(type)
        private var categoryId: String? = null
        private var chipKey = "all"
        private val params = HashMap<String, String>()
        private var total = 0
        private var loading = false
        private var token = 0
        private var chipTask: Runnable? = null
        private var built = false

        init {
            pageTitle.text = when (type) {
                "vod" -> "Filmer"
                "series" -> "Serier"
                else -> "Favoriter"
            }
            chipAdapter.edgeLock = false
            chips.layoutManager = LinearLayoutManager(this@HomeActivity, LinearLayoutManager.HORIZONTAL, false)
            chips.adapter = ChipAdapter(chipAdapter)
            chips.itemAnimator = null
            val width = resources.displayMetrics.widthPixels / resources.displayMetrics.density
            columns = ((width - 96 - 40) / 154).toInt().coerceIn(4, 8)
            gridManager = GridLayoutManager(this@HomeActivity, columns)
            grid.layoutManager = gridManager
            grid.adapter = gridAdapter
            grid.itemAnimator = null
            gridAdapter.onEdgeKey = { position, keyCode ->
                // Upp från första raden: till grupperna.
                if (keyCode == KeyEvent.KEYCODE_DPAD_UP && position < gridManager.spanCount) {
                    chips.post { Focus.position(chips, chipAdapter.indexOf(chipKey).coerceAtLeast(0)) }
                    true
                } else {
                    false
                }
            }
            sortButton.setOnClickListener { showSort() }
            filterButton.setOnClickListener { showFilter() }
            viewButton.setOnClickListener { chooseStyle() }
            Look.focusGrow(sortButton, 1.04f)
            Look.focusGrow(viewButton, 1.04f)
            Look.focusGrow(filterButton, 1.04f)
            if (favoritesOnly) {
                sortButton.visibility = View.GONE
                filterButton.visibility = View.GONE
            }
            applyStyle()
            updateButtons()
        }

        /** Raden med knappar överst (Sortera, Visa, Filter): den första som syns. */
        private fun firstButton(): View = if (sortButton.visibility == View.VISIBLE) sortButton else viewButton

        override fun onEdge(focused: View, keyCode: Int): Boolean {
            val inChips = isInside(focused, chips)
            val onButtons = focused === sortButton || focused === viewButton || focused === filterButton
            return when {
                // Upp från grupperna: till Sortera (inte ut i menyn).
                inChips && keyCode == KeyEvent.KEYCODE_DPAD_UP -> {
                    firstButton().requestFocus()
                    true
                }
                // Ner från knapparna: till gruppen som är vald.
                onButtons && keyCode == KeyEvent.KEYCODE_DPAD_DOWN -> {
                    Focus.position(chips, chipAdapter.indexOf(chipKey).coerceAtLeast(0))
                    true
                }
                onButtons && keyCode == KeyEvent.KEYCODE_DPAD_UP -> true
                else -> false
            }
        }

        override fun onHomeData(data: JSONObject) {
            if (built) {
                // Favoriter ändras (★): uppdatera grupperna och listan som visas.
                if (favoritesOnly) buildFavoriteChips(data, reload = true)
                else if (chipKey == "fav") load(reset = true)
                return
            }
            built = true
            if (favoritesOnly) {
                buildFavoriteChips(data, reload = true)
                return
            }
            val rows = ArrayList<Row>()
            rows.add(Row("Alla", key = "all"))
            if (favorites(data).isNotEmpty()) rows.add(Row("★ Favoriter", key = "fav"))
            rows.add(Row("Nytt tillagt", key = "new"))
            val cats = data.optJSONObject("categories")?.optJSONArray(type) ?: JSONArray()
            for (i in 0 until cats.length()) {
                val c = cats.optJSONObject(i) ?: continue
                rows.add(Row(c.optString("name"), key = "cat:${c.optString("category_id")}", payload = c.optString("category_id")))
            }
            chipAdapter.submit(rows)
            // Startkategori från webbadminen (Kategorier → Start).
            data.optJSONObject("start_categories")?.optStringOrNull(type)?.let { id ->
                rows.firstOrNull { it.key == "cat:$id" }?.let { chipKey = it.key; categoryId = id }
            }
            chipAdapter.select(chipKey)
            load(reset = true)
        }

        /** Favoriter av sidans sort (Filmer: filmer, Serier: serier). */
        private fun favorites(data: JSONObject?): List<Item> =
            Item.list(data?.optJSONArray("favorites")).filter { it.type == type }

        private val favoriteKinds = listOf("vod" to "Filmer", "series" to "Serier", "episode" to "Avsnitt", "live" to "Kanaler")

        private fun buildFavoriteChips(data: JSONObject, reload: Boolean) {
            val all = Item.list(data.optJSONArray("favorites"))
            val rows = favoriteKinds.filter { (kind, _) -> all.any { it.type == kind } }
                .map { (kind, label) -> Row("$label (${all.count { it.type == kind }})", key = "fav:$kind") }.toMutableList()
            // Mina media: favoriter från NAS:en och (i vuxenläget) USB-disken. Sparade på boxen.
            Media.visibleFavorites(this@HomeActivity, usb = false).takeIf { it.isNotEmpty() }?.let { rows.add(Row("NAS (${it.size})", key = "fav:nas")) }
            Media.visibleFavorites(this@HomeActivity, usb = true).takeIf { it.isNotEmpty() }?.let { rows.add(Row("USB-disk (${it.size})", key = "fav:usb")) }
            chipAdapter.submit(rows)
            if (rows.none { it.key == chipKey }) chipKey = rows.firstOrNull()?.key ?: "fav:vod"
            chipAdapter.select(chipKey)
            if (reload) load(reset = true)
        }

        override fun focusEntry(): Boolean {
            if (gridAdapter.itemCount > 0) {
                grid.post { grid.findViewHolderForAdapterPosition(0)?.itemView?.requestFocus() ?: grid.requestFocus() }
            } else {
                Focus.position(chips, 0)
            }
            return true
        }

        private fun previewChip(row: Row) {
            chipTask?.let { handler.removeCallbacks(it) }
            if (row.key == chipKey) return
            val task = Runnable { selectChip(row, focusGrid = false) }
            chipTask = task
            handler.postDelayed(task, 450)
        }

        private fun selectChip(row: Row, focusGrid: Boolean) {
            chipTask?.let { handler.removeCallbacks(it) }
            if (row.key != chipKey) {
                chipKey = row.key
                chipAdapter.select(row.key)
                categoryId = row.payload as? String
                if (row.key == "new") {
                    params["sort"] = "added"
                    params["new_only"] = "1"
                } else {
                    params.remove("new_only")
                }
                load(reset = true, focusFirst = focusGrid)
            } else if (focusGrid) {
                focusEntry()
            }
        }

        // ---------- visningsläge

        /** Kanaler visas alltid som lista (korten är för breda för rutnätet). */
        private fun effectiveStyle(): Int = if (chipKey == "fav:live") ViewStyle.LIST else style

        private fun applyStyle() {
            val s = effectiveStyle()
            if (gridAdapter.style != s) {
                gridAdapter.style = s
                gridManager.spanCount = if (s == ViewStyle.LIST) 1 else columns
                gridAdapter.notifyDataSetChanged()
            }
            viewButton.text = "Visa: " + ViewStyle.label(style)
        }

        private fun chooseStyle() {
            Look.choose(this@HomeActivity, "Visa", ViewStyle.LABELS, ViewStyle.index(style)) { which ->
                style = ViewStyle.fromIndex(which)
                prefs.setViewStyle(type, style)
                applyStyle()
            }
        }

        private fun cardFor(item: Item): Card = if (item.type == "live") Card.channel(item) else Card.poster(item)

        private fun load(reset: Boolean, focusFirst: Boolean = false) {
            if (favoritesOnly && (chipKey == "fav:nas" || chipKey == "fav:usb")) {
                val favs = Media.visibleFavorites(this@HomeActivity, usb = chipKey == "fav:usb")
                token++
                total = favs.size
                applyStyle()
                gridAdapter.submit(favs.map { f ->
                    val e = f.entry
                    Card(Card.POSTER, e.key, (if (e.folder) "📁 " else if (e.kind == "image") "🖼 " else "") + e.name,
                        if (e.folder) "Mapp · ${f.sourceLabel}" else f.sourceLabel, e.thumb, payload = f)
                })
                grid.scrollToPosition(0)
                info.text = "${favs.size} favoriter  ·  Håll in OK för att ta bort"
                if (focusFirst && favs.isNotEmpty()) focusEntry()
                updateButtons()
                return
            }
            if (favoritesOnly || chipKey == "fav") {
                // Favoriter: finns redan i hemdatan.
                val kind = if (favoritesOnly) chipKey.removePrefix("fav:") else type
                val items = Item.list(home?.optJSONArray("favorites")).filter { it.type == kind }
                token++
                total = items.size
                applyStyle()
                gridAdapter.submit(items.map { cardFor(it) })
                grid.scrollToPosition(0)
                info.text = if (items.isEmpty()) "Inga favoriter här ännu. Tryck ★ eller håll in OK på en titel." else "${items.size} favoriter"
                if (focusFirst && items.isNotEmpty()) focusEntry()
                updateButtons()
                return
            }
            applyStyle()
            val t = if (reset) ++token else token
            val offset = if (reset) 0 else gridAdapter.itemCount
            loading = true
            val q = StringBuilder("type=$type&offset=$offset&limit=$PAGE")
            categoryId?.let { q.append("&category_id=").append(Api.enc(it)) }
            for ((k, v) in params) q.append("&").append(k).append("=").append(Api.enc(v))
            if (reset) info.text = "Hämtar…"
            Api.obj("GET", "/device/browse?$q") { result ->
                if (t != token) return@obj
                loading = false
                result.onSuccess { data ->
                    total = data.optInt("total")
                    val cards = Item.list(data.optJSONArray("items")).map { Card.poster(it) }
                    if (reset) {
                        gridAdapter.submit(cards)
                        grid.scrollToPosition(0)
                        info.text = if (cards.isEmpty()) {
                            if (params["new_only"] == "1") {
                                "Inget nytt ännu. Här visas det som dykt upp i listan sedan TV Hub började hålla koll. Listorna uppdateras var 12:e timme."
                            } else if (params.keys.any { it != "sort" }) "Inget matchar filtret." else "Inget innehåll här."
                        } else {
                            "${total} titlar"
                        }
                        if (focusFirst && cards.isNotEmpty()) focusEntry()
                    } else {
                        gridAdapter.append(cards)
                    }
                    updateButtons()
                }.onFailure { info.text = Api.friendly(it) }
            }
        }

        override fun onCardClick(card: Card, shelf: Shelf?) {
            (card.payload as? Media.Fav)?.let { openMediaFavorite(it) ; return }
            val item = card.payload as? Item
            if (item?.type == "live") {
                // Kanaler: zappa bland kanalerna i listan.
                openItem(item, gridAdapter.cards.mapNotNull { it.payload as? Item })
            } else {
                this@HomeActivity.onCardClick(card, shelf)
            }
        }

        override fun onCardLongClick(card: Card, shelf: Shelf?) {
            val fav = card.payload as? Media.Fav
            if (fav == null) {
                this@HomeActivity.onCardLongClick(card, shelf)
                return
            }
            Look.choose(this@HomeActivity, fav.entry.name, listOf("Öppna", "★ Ta bort ur favoriter")) { which ->
                if (which == 0) openMediaFavorite(fav) else {
                    Media.removeFavorite(this@HomeActivity, fav.entry.key)
                    home?.let { buildFavoriteChips(it, reload = true) }
                }
            }
        }

        /** Mapp: öppnas i Mina media. Video: spelas. Bild: bildspel bland bildfavoriterna. */
        private fun openMediaFavorite(f: Media.Fav) {
            val e = f.entry
            if (f.usb && !Media.usbDrives(this@HomeActivity).any { e.ref.startsWith(it.root.absolutePath) }) {
                Toast.makeText(this@HomeActivity, "Sätt i USB-disken först.", Toast.LENGTH_LONG).show()
                return
            }
            when (e.kind) {
                "folder" -> {
                    showPage("media")
                    mediaPage?.openFolder(f.sourceId, e.ref, e.name)
                }
                "video" -> {
                    val url = e.url ?: return
                    if (ExternalPlayer.needsVlc(this@HomeActivity, url) &&
                        ExternalPlayer.openInVlc(this@HomeActivity, url, e.name, prefs.recordingPosition(url))) return
                    PlayerActivity.start(this@HomeActivity, type = "file", id = url, name = e.name, ext = null,
                        subtitle = e.subtitle, label = f.sourceLabel)
                }
                "image" -> {
                    val images = gridAdapter.cards.mapNotNull { (it.payload as? Media.Fav)?.entry }.filter { it.kind == "image" }
                    ImageViewerActivity.start(this@HomeActivity, images, images.indexOfFirst { it.key == e.key }.coerceAtLeast(0))
                }
            }
        }

        override fun onCardFocus(card: Card, shelf: Shelf?, shelfIndex: Int, cardIndex: Int) {
            val item = card.payload as? Item ?: return
            markFocused(item)
            val added = if (params["new_only"] == "1") item.added?.let { Format.addedLabel(it) } else null
            info.text = listOfNotNull(item.name, added, item.facts.ifEmpty { null }, item.genre, if (item.watched) "✓ Sedd" else null)
                .joinToString("   ·   ")
            if (!favoritesOnly && chipKey != "fav" && !loading && gridAdapter.itemCount < total &&
                cardIndex >= gridAdapter.itemCount - 24) load(reset = false)
        }

        override fun chooseGroup(): Boolean {
            val rows = chipAdapter.rows.filter { !it.header }
            if (rows.isEmpty()) return false
            Look.choose(this@HomeActivity, "Grupper", rows.map { it.title }, checked = rows.indexOfFirst { it.key == chipKey }) {
                selectChip(rows[it], focusGrid = true)
            }
            return true
        }

        // ---------- sortering och filter (samma som i det klassiska utseendet)

        private fun sortOptions(): List<Pair<String, String>> = if (type == "series") {
            listOf("added" to "Senast tillagda", "rating" to "Betyg", "year" to "Årtal", "name" to "Namn")
        } else {
            listOf(
                "added" to "Senast tillagda", "rating" to "Betyg", "year" to "Årtal",
                "duration" to "Längd, kortast först", "duration_desc" to "Längd, längst först", "name" to "Namn",
            )
        }

        private fun filterSummary(): String {
            val parts = ArrayList<String>()
            params["min_rating"]?.let { parts.add("★ $it+") }
            params["max_duration"]?.let { parts.add("under ${Format.minutes(it.toInt())}") }
            params["min_duration"]?.let { parts.add("över ${Format.minutes(it.toInt())}") }
            val from = params["min_year"]
            val to = params["max_year"]
            when {
                from != null && to != null -> parts.add("$from–$to")
                from != null -> parts.add("från $from")
                to != null -> parts.add("till $to")
            }
            params["genre"]?.let { parts.add(it) }
            return parts.joinToString(" · ")
        }

        private fun updateButtons() {
            val options = sortOptions()
            val key = params["sort"] ?: "added"
            sortButton.text = "Sortera: " + (options.firstOrNull { it.first == key }?.second ?: options.first().second)
            filterButton.text = "Filter" + filterSummary().let { if (it.isEmpty()) "" else ": $it" }
        }

        private fun showSort() {
            val options = sortOptions()
            val currentIndex = options.indexOfFirst { it.first == (params["sort"] ?: "added") }.coerceAtLeast(0)
            Look.choose(this@HomeActivity, "Sortera", options.map { it.second }, currentIndex) { which ->
                params["sort"] = options[which].first
                load(reset = true)
            }
        }

        private fun showFilter() {
            val labels = ArrayList<String>()
            val actions = ArrayList<() -> Unit>()
            labels.add("Betyg: " + (params["min_rating"]?.let { "★ $it eller högre" } ?: "Alla")); actions.add { chooseRating() }
            if (type == "vod") {
                labels.add("Längd: " + when {
                    params["max_duration"] != null -> "Under ${Format.minutes(params["max_duration"]!!.toInt())}"
                    params["min_duration"] != null -> "Över ${Format.minutes(params["min_duration"]!!.toInt())}"
                    else -> "Alla"
                }); actions.add { chooseDuration() }
            }
            labels.add("Årtal: " + when {
                params["min_year"] != null && params["max_year"] != null -> "${params["min_year"]}–${params["max_year"]}"
                params["min_year"] != null -> "Från ${params["min_year"]}"
                params["max_year"] != null -> "Före ${params["max_year"]!!.toInt() + 1}"
                else -> "Alla"
            }); actions.add { chooseYear() }
            labels.add("Genre: ${params["genre"] ?: "Alla"}"); actions.add { chooseGenre() }
            labels.add("Spara som rad på Hem…"); actions.add { saveFilter() }
            if (filterSummary().isNotEmpty()) {
                labels.add("Rensa alla filter")
                actions.add {
                    listOf("min_rating", "min_duration", "max_duration", "min_year", "max_year", "genre").forEach { params.remove(it) }
                    load(reset = true)
                }
            }
            Look.choose(this@HomeActivity, "Filter", labels) { actions[it]() }
        }

        private fun pick(title: String, labels: List<String>, current: Int, apply: (Int) -> Unit) {
            Look.choose(this@HomeActivity, title, labels, current) { which ->
                apply(which)
                load(reset = true)
                handler.postDelayed({ showFilter() }, 250)
            }
        }

        private fun chooseRating() {
            val values = listOf(null, "5", "6", "7", "8")
            pick("Betyg", listOf("Alla", "★ 5 eller högre", "★ 6 eller högre", "★ 7 eller högre", "★ 8 eller högre"),
                values.indexOf(params["min_rating"]).coerceAtLeast(0)) { which ->
                values[which]?.let { params["min_rating"] = it } ?: params.remove("min_rating")
            }
        }

        private fun chooseDuration() {
            val values = listOf(null to null, null to "90", null to "120", "120" to null)
            pick("Längd", listOf("Alla", "Under 1 h 30 min", "Under 2 h", "Över 2 h"),
                values.indexOf(params["min_duration"] to params["max_duration"]).coerceAtLeast(0)) { which ->
                params.remove("min_duration")
                params.remove("max_duration")
                values[which].first?.let { params["min_duration"] = it }
                values[which].second?.let { params["max_duration"] = it }
            }
        }

        private fun chooseYear() {
            val values = listOf(null to null, "2020" to null, "2010" to null, "2000" to null, null to "1999")
            pick("Årtal", listOf("Alla", "Från 2020", "Från 2010", "Från 2000", "Före 2000"),
                values.indexOf(params["min_year"] to params["max_year"]).coerceAtLeast(0)) { which ->
                params.remove("min_year")
                params.remove("max_year")
                values[which].first?.let { params["min_year"] = it }
                values[which].second?.let { params["max_year"] = it }
            }
        }

        private fun chooseGenre() {
            Api.arr("/device/genres?type=$type") { result ->
                result.onSuccess { array ->
                    val list = ArrayList<String>()
                    for (i in 0 until array.length()) array.optJSONObject(i)?.optString("name")?.let { list.add(it) }
                    if (list.isEmpty()) {
                        Toast.makeText(this@HomeActivity, "Genrer hämtas fortfarande i bakgrunden. Prova senare.", Toast.LENGTH_LONG).show()
                        return@onSuccess
                    }
                    pick("Genre", listOf("Alla") + list, (list.indexOf(params["genre"]) + 1).coerceAtLeast(0)) { which ->
                        if (which == 0) params.remove("genre") else params["genre"] = list[which - 1]
                    }
                }.onFailure { Toast.makeText(this@HomeActivity, Api.friendly(it), Toast.LENGTH_LONG).show() }
            }
        }

        private fun saveFilter() {
            val input = EditText(this@HomeActivity)
            val base = if (type == "vod") "Filmer" else "Serier"
            input.setText(listOf(base, filterSummary()).filter { it.isNotEmpty() }.joinToString(" · ").take(40))
            android.app.AlertDialog.Builder(this@HomeActivity)
                .setTitle("Namn på raden")
                .setView(input)
                .setPositiveButton("Spara") { _, _ ->
                    val p = JSONObject()
                    for ((k, v) in params) p.put(k, v)
                    categoryId?.let { p.put("category_id", it) }
                    val body = JSONObject().put("name", input.text.toString().trim()).put("type", type).put("params", p)
                    Api.obj("POST", "/device/filters", body) { result ->
                        result.onSuccess {
                            Toast.makeText(this@HomeActivity, "Sparat som rad på Hem", Toast.LENGTH_SHORT).show()
                            homePage = null
                            loadHome()
                        }.onFailure { Toast.makeText(this@HomeActivity, Api.friendly(it), Toast.LENGTH_LONG).show() }
                    }
                }
                .setNegativeButton("Avbryt", null)
                .show()
        }
    }

    /** Chips i en rad (kategorier). Återanvänder RowAdapters logik men med chip-layout. */
    private class ChipAdapter(private val rows: RowAdapter) : RecyclerView.Adapter<RowAdapter.Holder>() {
        init {
            rows.registerAdapterDataObserver(object : RecyclerView.AdapterDataObserver() {
                override fun onChanged() = notifyDataSetChanged()
                override fun onItemRangeChanged(positionStart: Int, itemCount: Int) = notifyItemRangeChanged(positionStart, itemCount)
            })
        }

        override fun getItemCount() = rows.itemCount

        override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): RowAdapter.Holder =
            RowAdapter.Holder(LayoutInflater.from(parent.context).inflate(R.layout.chip_m, parent, false))

        override fun onBindViewHolder(holder: RowAdapter.Holder, position: Int) = rows.onBindViewHolder(holder, position)
    }

    // ================================================================ Live-TV

    private inner class LivePage : Page() {
        override val view: View = LayoutInflater.from(this@HomeActivity).inflate(R.layout.page_live, container, false)
        private val chips: RecyclerView = view.findViewById(R.id.chips)
        private val list: RecyclerView = view.findViewById(R.id.list)
        private val guideTitle: TextView = view.findViewById(R.id.guide_title)
        private val guide: TextView = view.findViewById(R.id.guide)
        private val chipAdapter = RowAdapter(onClick = { row, _ -> selectChip(row, true) }, onFocus = { row, _ -> previewChip(row) })
        private val channelAdapter = ChannelAdapter()
        private var chipKey = "all"
        private var categoryId: String? = null
        private var token = 0
        private var total = 0
        private var loading = false
        private var chipTask: Runnable? = null
        private var guideTask: Runnable? = null
        private var built = false
        private val now = HashMap<String, JSONObject?>()

        init {
            chipAdapter.edgeLock = false
            chips.layoutManager = LinearLayoutManager(this@HomeActivity, LinearLayoutManager.HORIZONTAL, false)
            chips.adapter = ChipAdapter(chipAdapter)
            chips.itemAnimator = null
            list.layoutManager = LinearLayoutManager(this@HomeActivity)
            list.adapter = channelAdapter
            list.itemAnimator = null
            list.addOnScrollListener(object : RecyclerView.OnScrollListener() {
                override fun onScrollStateChanged(recyclerView: RecyclerView, newState: Int) {
                    if (newState == RecyclerView.SCROLL_STATE_IDLE) refreshNow()
                }
            })
        }

        override fun onHomeData(data: JSONObject) {
            if (built) return
            built = true
            val rows = ArrayList<Row>()
            rows.add(Row("Alla kanaler", key = "all"))
            val favLive = Item.list(data.optJSONArray("favorites")).filter { it.type == "live" }
            if (favLive.isNotEmpty()) rows.add(Row("Favoriter", key = "fav"))
            val cats = data.optJSONObject("categories")?.optJSONArray("live") ?: JSONArray()
            for (i in 0 until cats.length()) {
                val c = cats.optJSONObject(i) ?: continue
                rows.add(Row(c.optString("name"), key = "cat:${c.optString("category_id")}", payload = c.optString("category_id")))
            }
            chipAdapter.submit(rows)
            data.optJSONObject("start_categories")?.optStringOrNull("live")?.let { id ->
                rows.firstOrNull { it.key == "cat:$id" }?.let { chipKey = it.key; categoryId = id }
            }
            chipAdapter.select(chipKey)
            load(reset = true)
        }

        override fun focusEntry(): Boolean {
            if (channelAdapter.items.isNotEmpty()) Focus.position(list, 0) else Focus.position(chips, 0)
            return true
        }

        private fun previewChip(row: Row) {
            chipTask?.let { handler.removeCallbacks(it) }
            if (row.key == chipKey) return
            val task = Runnable { selectChip(row, false) }
            chipTask = task
            handler.postDelayed(task, 450)
        }

        private fun selectChip(row: Row, focus: Boolean) {
            chipTask?.let { handler.removeCallbacks(it) }
            if (row.key != chipKey) {
                chipKey = row.key
                chipAdapter.select(row.key)
                categoryId = row.payload as? String
                load(reset = true, focusFirst = focus)
            } else if (focus) {
                focusEntry()
            }
        }

        private fun load(reset: Boolean, focusFirst: Boolean = false) {
            if (chipKey == "fav") {
                val fav = Item.list(home?.optJSONArray("favorites")).filter { it.type == "live" }
                channelAdapter.submit(fav)
                total = fav.size
                if (focusFirst) focusEntry()
                refreshNow()
                return
            }
            val t = if (reset) ++token else token
            val offset = if (reset) 0 else channelAdapter.items.size
            loading = true
            val q = StringBuilder("type=live&sort=num&offset=$offset&limit=300")
            categoryId?.let { q.append("&category_id=").append(Api.enc(it)) }
            Api.obj("GET", "/device/browse?$q") { result ->
                if (t != token) return@obj
                loading = false
                result.onSuccess { data ->
                    total = data.optInt("total")
                    val items = Item.list(data.optJSONArray("items"))
                    if (reset) {
                        channelAdapter.submit(items)
                        list.scrollToPosition(0)
                        if (focusFirst && items.isNotEmpty()) focusEntry()
                    } else {
                        channelAdapter.append(items)
                    }
                    list.post { refreshNow() }
                }.onFailure { Toast.makeText(this@HomeActivity, Api.friendly(it), Toast.LENGTH_LONG).show() }
            }
        }

        private fun refreshNow() {
            val lm = list.layoutManager as? LinearLayoutManager ?: return
            val first = lm.findFirstVisibleItemPosition().coerceAtLeast(0)
            val last = lm.findLastVisibleItemPosition().let { if (it < 0) first + 12 else it + 6 }
            val ids = (first..last.coerceAtMost(channelAdapter.items.size - 1))
                .mapNotNull { channelAdapter.items.getOrNull(it)?.id }
                .filter { !now.containsKey(it) }
            if (ids.isEmpty()) return
            ids.forEach { now[it] = null }
            fetchNow(ids) { data ->
                for (id in ids) now[id] = data.optJSONObject(id)
                channelAdapter.notifyDataSetChanged()
            }
        }

        override fun chooseGroup(): Boolean {
            val rows = chipAdapter.rows.filter { !it.header }
            if (rows.isEmpty()) return false
            Look.choose(this@HomeActivity, "Grupper", rows.map { it.title }, checked = rows.indexOfFirst { it.key == chipKey }) {
                selectChip(rows[it], true)
            }
            return true
        }

        override fun onEdge(focused: View, keyCode: Int): Boolean =
            keyCode == KeyEvent.KEYCODE_DPAD_UP && isInside(focused, chips)

        /** Kanalerna i listan just nu (sifferknapparna spelar med dem som kanallista). */
        fun channels(): List<Item> = channelAdapter.items

        private fun onChannelFocus(item: Item, position: Int) {
            markFocused(item)
            if (!loading && chipKey != "fav" && channelAdapter.items.size < total && position >= channelAdapter.items.size - 30) {
                load(reset = false)
            }
            guideTitle.text = listOfNotNull(item.num?.toString(), item.name).joinToString("  ")
            guide.text = ""
            guideTask?.let { handler.removeCallbacks(it) }
            val id = item.id
            val task = Runnable {
                Api.arr("/device/epg/${Api.enc(id)}") { result ->
                    if (channelAdapter.focusedId != id) return@arr
                    result.onSuccess { array -> guide.text = guideText(array) }
                }
            }
            guideTask = task
            handler.postDelayed(task, 400)
        }

        /** De närmaste programmen, ungefär tre timmar framåt. */
        private fun guideText(array: JSONArray): String {
            val t = System.currentTimeMillis() / 1000
            val lines = ArrayList<String>()
            for (i in 0 until array.length()) {
                val p = array.optJSONObject(i) ?: continue
                val start = p.optLong("start")
                val end = p.optLong("end")
                if (end in 1 until t) continue
                if (start > t + 3 * 3600 && lines.size >= 2) break
                val label = if (start <= t) "Nu" else timeFormat.format(Date(start * 1000))
                lines.add("$label   ${p.optString("title")}")
                if (lines.size >= 7) break
            }
            return if (lines.isEmpty()) "Ingen programguide för kanalen." else lines.joinToString("\n") +
                "\n\nOK: titta   Håll in OK: programguide och inspelning"
        }

        private inner class ChannelAdapter : RecyclerView.Adapter<ChannelAdapter.Holder>() {
            var items: List<Item> = emptyList()
                private set
            var focusedId: String? = null

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

            override fun getItemCount() = items.size

            override fun onCreateViewHolder(parent: ViewGroup, viewType: Int) =
                Holder(LayoutInflater.from(parent.context).inflate(R.layout.row_channel_m, parent, false))

            override fun onBindViewHolder(holder: Holder, position: Int) {
                val item = items[position]
                holder.title.text = listOfNotNull(item.num?.toString(), item.name).joinToString("   ")
                holder.fallback.text = item.name.take(14)
                holder.fallback.visibility = View.VISIBLE
                ImageLoader.load(holder.art, item.icon) { ok -> if (ok) holder.fallback.visibility = View.INVISIBLE }
                val (text, progress) = nowText(now[item.id]?.optJSONObject("now"))
                holder.subtitle.text = text ?: (item.categoryName ?: "")
                if (progress >= 0) {
                    holder.progress.progress = progress
                    holder.progress.visibility = View.VISIBLE
                } else {
                    holder.progress.visibility = View.GONE
                }
                holder.star.visibility = if (item.favorite && !privateMode()) View.VISIBLE else View.GONE
                holder.itemView.setOnClickListener {
                    val p = holder.bindingAdapterPosition
                    if (p == RecyclerView.NO_POSITION) return@setOnClickListener
                    val ch = items[p]
                    PlayerActivity.start(
                        this@HomeActivity, type = "live", id = ch.id, name = ch.name, ext = null,
                        queueIds = ArrayList(items.map { it.id }),
                        queueNames = ArrayList(items.map { it.name }),
                        queueExts = null,
                        queueIndex = p,
                        icon = ch.icon,
                    )
                }
                holder.itemView.setOnLongClickListener {
                    val p = holder.bindingAdapterPosition
                    if (p != RecyclerView.NO_POSITION) showItemOptions(items[p])
                    true
                }
                holder.itemView.setOnFocusChangeListener { _, hasFocus ->
                    if (!hasFocus) return@setOnFocusChangeListener
                    val p = holder.bindingAdapterPosition
                    if (p == RecyclerView.NO_POSITION) return@setOnFocusChangeListener
                    focusedId = items[p].id
                    onChannelFocus(items[p], p)
                }
            }
        }
    }

    // ================================================================ Sök

    private inner class SearchPage : Page() {
        override val view: View = LayoutInflater.from(this@HomeActivity).inflate(R.layout.page_search, container, false)
        private val queryView: TextView = view.findViewById(R.id.query)
        private val keys: RecyclerView = view.findViewById(R.id.keys)
        private val hint: TextView = view.findViewById(R.id.hint)
        private val shelves: RecyclerView = view.findViewById(R.id.shelves)
        private val shelfAdapter = ShelfAdapter(this@HomeActivity).also { it.style = ViewStyle.TITLE }
        private val keyAdapter = RowAdapter(onClick = { row, _ -> press(row.key) }, onKey = { _, p, code -> keyEdge(p, code) })
        private val keyColumns = 6
        private val keyManager = GridLayoutManager(this@HomeActivity, keyColumns)
        private var query = ""
        private var task: Runnable? = null
        private var token = 0

        init {
            val letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZÅÄÖ1234567890".map { it.toString() }
            // Röstsök överst, om boxen har Googles röstinmatning.
            val voice = if (Voice.available(this@HomeActivity)) listOf(Row("🎤  Sök med rösten", key = "voice")) else emptyList()
            val rows = voice + letters.map { Row(it, key = it) } +
                listOf(Row("Mellanslag", key = " "), Row("⌫", key = "back"), Row("Rensa", key = "clear"))
            val offset = voice.size
            keyManager.spanSizeLookup = object : GridLayoutManager.SpanSizeLookup() {
                override fun getSpanSize(position: Int): Int = when {
                    position < offset -> keyColumns
                    position >= offset + letters.size -> 2
                    else -> 1
                }
            }
            keyAdapter.edgeLock = false
            keys.layoutManager = keyManager
            keys.adapter = KeyAdapter(keyAdapter)
            keyAdapter.submit(rows)
            shelves.layoutManager = LinearLayoutManager(this@HomeActivity)
            shelves.adapter = shelfAdapter
            shelves.itemAnimator = null
            render()
        }

        override fun focusEntry(): Boolean {
            Focus.position(keys, 0)
            return true
        }

        fun onFocus(shelfIndex: Int) {
            (shelves.layoutManager as LinearLayoutManager).scrollToPositionWithOffset(shelfIndex, 0)
        }

        /**
         * Kanterna på tangentbordet: vänster i första kolumnen går till menyn, höger i sista
         * kolumnen till träffarna. Utan det här hoppar fokus runt inne i tangentbordet.
         */
        private fun keyEdge(position: Int, keyCode: Int): Boolean {
            val lookup = keyManager.spanSizeLookup
            val column = lookup.getSpanIndex(position, keyColumns)
            return when (keyCode) {
                KeyEvent.KEYCODE_DPAD_LEFT -> if (column == 0) {
                    focusRail()
                    true
                } else {
                    false
                }
                KeyEvent.KEYCODE_DPAD_RIGHT -> if (column + lookup.getSpanSize(position) >= keyColumns) {
                    focusResults()
                    true  // inga träffar: stanna kvar i stället för att hoppa runt
                } else {
                    false
                }
                else -> false
            }
        }

        private fun focusResults() {
            val holder = shelves.findViewHolderForAdapterPosition(0) as? ShelfAdapter.Holder ?: return
            (holder.cards.getChildAt(0) ?: holder.cards).requestFocus()
        }

        fun type(key: String) = press(key)

        /** Röstsök: det som sades blir sökningen. */
        fun setQuery(text: String) {
            query = text.lowercase()
            render()
            task?.let { handler.removeCallbacks(it) }
            search()
        }

        private fun press(key: String) {
            if (key == "voice") {
                Voice.start(this@HomeActivity)
                return
            }
            query = when (key) {
                "back" -> query.dropLast(1)
                "clear" -> ""
                " " -> if (query.isEmpty() || query.endsWith(" ")) query else "$query "
                else -> query + key.lowercase()
            }
            render()
            task?.let { handler.removeCallbacks(it) }
            val t = Runnable { search() }
            task = t
            handler.postDelayed(t, 450)
        }

        private fun render() {
            queryView.text = if (query.isEmpty()) "Sök kanaler, filmer, serier" else query
            queryView.setTextColor(if (query.isEmpty()) 0xFF6E6E78.toInt() else 0xFFF5F5F7.toInt())
            val recent = prefs.recentSearches
            hint.text = if (query.isEmpty() && recent.isNotEmpty()) "Senaste: " + recent.joinToString(", ") else
                "Välj bokstäver med fjärrkontrollen, eller 🎤 överst. Träffarna visas till höger.\nVänster: till menyn. Tillbaka: till Hem."
        }

        private fun search() {
            val q = query.trim()
            if (q.length < 2) {
                shelfAdapter.submit(emptyList())
                return
            }
            val t = ++token
            Api.arr("/device/search?q=${Api.enc(q)}&limit=120") { result ->
                if (t != token) return@arr
                result.onSuccess { array ->
                    val items = Item.list(array)
                    val list = ArrayList<Shelf>()
                    items.filter { it.type == "live" }.takeIf { it.isNotEmpty() }?.let { list.add(Shelf("s_live", "Kanaler", it.map { i -> Card.channel(i) })) }
                    items.filter { it.type == "vod" }.takeIf { it.isNotEmpty() }?.let { list.add(Shelf("s_vod", "Filmer", it.map { i -> Card.poster(i) })) }
                    items.filter { it.type == "series" }.takeIf { it.isNotEmpty() }?.let { list.add(Shelf("s_series", "Serier", it.map { i -> Card.poster(i) })) }
                    shelfAdapter.submit(list)
                    hint.text = if (list.isEmpty()) "Inga träffar på \"$q\"." else "Höger för att gå till träffarna. Vänster: till menyn. Tillbaka: till Hem."
                    if (list.isNotEmpty()) prefs.recentSearches = listOf(q) + prefs.recentSearches.filter { it != q }
                }.onFailure { hint.text = Api.friendly(it) }
            }
        }
    }

    /** Tangenter i sökrutnätet. */
    private class KeyAdapter(private val rows: RowAdapter) : RecyclerView.Adapter<RowAdapter.Holder>() {
        init {
            rows.registerAdapterDataObserver(object : RecyclerView.AdapterDataObserver() {
                override fun onChanged() = notifyDataSetChanged()
            })
        }

        override fun getItemCount() = rows.itemCount

        override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): RowAdapter.Holder =
            RowAdapter.Holder(LayoutInflater.from(parent.context).inflate(R.layout.key_m, parent, false))

        override fun onBindViewHolder(holder: RowAdapter.Holder, position: Int) = rows.onBindViewHolder(holder, position)
    }

    // ================================================================ Mina media

    /**
     * Mina media: NAS:en (DLNA) och USB-disken. Mappar som på disken, samma visningslägen som Filmer.
     * Inget härifrån hamnar i Fortsätt titta. Dolda mappar syns bara i vuxenläget.
     */
    private inner class MediaPage : Page(), CardListener {
        override val view: View = LayoutInflater.from(this@HomeActivity).inflate(R.layout.page_browse, container, false)
        private val pageTitle: TextView = view.findViewById(R.id.page_title)
        private val sortButton: TextView = view.findViewById(R.id.sort_button)
        private val viewButton: TextView = view.findViewById(R.id.view_button)
        private val addButton: TextView = view.findViewById(R.id.filter_button)
        private val chips: RecyclerView = view.findViewById(R.id.chips)
        private val info: TextView = view.findViewById(R.id.info)
        private val grid: RecyclerView = view.findViewById(R.id.grid)
        private val chipAdapter = RowAdapter(
            onClick = { row, _ -> selectSource(row, focusGrid = true) },
            onLongClick = { row, _ -> sourceOptions(row) },
        )
        private val gridAdapter = CardAdapter(this)
        private val gridManager: GridLayoutManager
        private val columns: Int
        private var style = prefs.viewStyle("media")
        private var byDate = false
        private var sources: List<Media.Source> = emptyList()
        private var source: Media.Source? = null
        private val stack = ArrayList<Pair<String, String>>()  // (ref, namn) från roten och nedåt
        private var entries: List<Media.Entry> = emptyList()
        private var token = 0
        private var searching = false
        private var lastDiscover = 0L
        private val allCache = HashMap<String, List<Media.Entry>>()

        /** Visar "Alla videor" (alla undermappar, nyast först) just nu? */
        private fun flat(): Boolean = stack.lastOrNull()?.first?.startsWith("flat:") == true

        private fun openAll(ref: String, name: String) {
            stack.add("flat:$ref" to "Alla videor i $name")
            load(focusFirst = true)
        }

        init {
            pageTitle.text = "Mina media"
            chipAdapter.edgeLock = false
            chips.layoutManager = LinearLayoutManager(this@HomeActivity, LinearLayoutManager.HORIZONTAL, false)
            chips.adapter = ChipAdapter(chipAdapter)
            chips.itemAnimator = null
            val width = resources.displayMetrics.widthPixels / resources.displayMetrics.density
            columns = ((width - 96 - 40) / 154).toInt().coerceIn(4, 8)
            gridManager = GridLayoutManager(this@HomeActivity, columns)
            grid.layoutManager = gridManager
            grid.adapter = gridAdapter
            grid.itemAnimator = null
            gridAdapter.onEdgeKey = { position, keyCode ->
                if (keyCode == KeyEvent.KEYCODE_DPAD_UP && position < gridManager.spanCount) {
                    chips.post { Focus.position(chips, chipAdapter.indexOf(source?.id?.let { "src:$it" }).coerceAtLeast(0)) }
                    true
                } else {
                    false
                }
            }
            sortButton.setOnClickListener {
                val labels = listOf("Namn", "Datum (nyast först)", "Alla videor i undermapparna, nyast först")
                Look.choose(this@HomeActivity, "Sortera", labels, if (flat()) 2 else if (byDate) 1 else 0) { which ->
                    when (which) {
                        2 -> if (!flat()) stack.lastOrNull()?.let { (ref, name) -> openAll(ref, name) }
                        else -> {
                            byDate = which == 1
                            if (flat()) onBack() else render()
                        }
                    }
                }
            }
            viewButton.setOnClickListener {
                Look.choose(this@HomeActivity, "Visa", ViewStyle.LABELS, ViewStyle.index(style)) { which ->
                    style = ViewStyle.fromIndex(which)
                    prefs.setViewStyle("media", style)
                    applyStyle()
                }
            }
            addButton.setOnClickListener { addServer() }
            Look.focusGrow(sortButton, 1.04f)
            Look.focusGrow(viewButton, 1.04f)
            Look.focusGrow(addButton, 1.04f)
            addButton.text = "Lägg till NAS…"
            applyStyle()
            updateButtons()
        }

        override fun onShow() {
            refreshSources(discover = true)
        }

        override fun focusEntry(): Boolean {
            if (gridAdapter.itemCount > 0) {
                grid.post { grid.findViewHolderForAdapterPosition(0)?.itemView?.requestFocus() ?: grid.requestFocus() }
            } else {
                Focus.position(chips, 0)
            }
            return true
        }

        override fun onEdge(focused: View, keyCode: Int): Boolean {
            val inChips = isInside(focused, chips)
            val onButtons = focused === sortButton || focused === viewButton || focused === addButton
            return when {
                inChips && keyCode == KeyEvent.KEYCODE_DPAD_UP -> {
                    sortButton.requestFocus()
                    true
                }
                onButtons && keyCode == KeyEvent.KEYCODE_DPAD_DOWN -> {
                    Focus.position(chips, chipAdapter.indexOf(source?.id?.let { "src:$it" }).coerceAtLeast(0))
                    true
                }
                onButtons && keyCode == KeyEvent.KEYCODE_DPAD_UP -> true
                else -> false
            }
        }

        /** Tillbaka: upp en mapp. I roten: som vanligt (till Hem). */
        override fun onBack(): Boolean {
            if (stack.size <= 1) return false
            stack.removeAt(stack.size - 1)
            load(focusFirst = true)
            return true
        }

        override fun chooseGroup(): Boolean {
            val rows = chipAdapter.rows
            if (rows.isEmpty()) return false
            Look.choose(this@HomeActivity, "Källor", rows.map { it.title }) { selectSource(rows[it], focusGrid = true) }
            return true
        }

        // ---------- källor

        fun refreshSources(discover: Boolean) {
            val list = ArrayList<Media.Source>()
            Media.savedServers(this@HomeActivity).forEach { list.add(Media.DlnaSource(this@HomeActivity, it)) }
            // USB-disken visas bara i vuxenläget.
            val drives = if (Media.usbAllowed(this@HomeActivity)) Media.usbDrives(this@HomeActivity) else emptyList()
            val usbAllowed = Media.hasReadPermission(this@HomeActivity)
            if (usbAllowed) drives.forEach { list.add(Media.UsbSource(this@HomeActivity, it)) }
            sources = list
            val rows = ArrayList<Row>()
            list.forEach { rows.add(Row((if (it is Media.UsbSource) "USB: " else "") + it.label, key = "src:${it.id}")) }
            if (drives.isNotEmpty() && !usbAllowed) rows.add(Row("USB-disk: ge TV Hub läsrätt", key = "usbperm"))
            chipAdapter.submit(rows)
            val keep = source?.id?.let { id -> list.firstOrNull { it.id == id } }
            if (keep == null) {
                allCache.clear()
                source = list.firstOrNull()
                stack.clear()
                source?.let { stack.add(it.rootRef to it.label) }
                load()
            }
            chipAdapter.select(source?.id?.let { "src:$it" })
            if (list.isEmpty()) {
                gridAdapter.submit(emptyList())
                info.text = when {
                    searching -> "Letar efter NAS i hemnätet…"
                    drives.isNotEmpty() && !usbAllowed -> "Välj \"USB-disk: ge TV Hub läsrätt\" och tryck Tillåt."
                    else -> "Ingen NAS hittades. Har NAS:en en mediaserver (Synology: Media Server)? " +
                        "Välj Lägg till NAS… uppe till höger och skriv in dess IP-adress."
                }
            }
            if (discover && !searching && System.currentTimeMillis() - lastDiscover > 60_000) discoverServers()
        }

        private fun discoverServers() {
            searching = true
            lastDiscover = System.currentTimeMillis()
            if (sources.isEmpty()) info.text = "Letar efter NAS i hemnätet…"
            Api.async({ Dlna.discover(3_000) }) { result ->
                searching = false
                val found = result.getOrNull().orEmpty()
                val saved = Media.savedServers(this@HomeActivity)
                val merged = saved.map { old -> found.firstOrNull { it.udn.isNotEmpty() && it.udn == old.udn } ?: old } +
                    found.filter { f -> saved.none { it.udn.isNotEmpty() && it.udn == f.udn || it.location == f.location } }
                Media.saveServers(this@HomeActivity, merged)
                if (current == "media") refreshSources(discover = false)
            }
        }

        private fun addServer() {
            val input = android.widget.EditText(this@HomeActivity)
            input.hint = "t.ex. 192.168.1.20"
            input.inputType = android.text.InputType.TYPE_CLASS_PHONE
            val pad = Look.dp(this@HomeActivity, 20)
            input.setPadding(pad, pad / 2, pad, pad / 2)
            android.app.AlertDialog.Builder(this@HomeActivity)
                .setTitle("NAS:ens IP-adress")
                .setView(input)
                .setPositiveButton("Lägg till") { _, _ ->
                    val ip = input.text.toString().trim()
                    if (ip.isEmpty()) return@setPositiveButton
                    info.text = "Letar efter en mediaserver på $ip…"
                    Api.async({ Dlna.findAt(ip, 3_000) }) { result ->
                        val server = result.getOrNull()
                        if (server == null) {
                            Look.message(this@HomeActivity, "Hittade ingen mediaserver",
                                "Ingen mediaserver svarade på $ip. Kontrollera adressen, och att mediaservern är på " +
                                    "(Synology: paketet Media Server).")
                            info.text = ""
                            return@async
                        }
                        Media.saveServers(this@HomeActivity, Media.savedServers(this@HomeActivity) + server)
                        source = null
                        refreshSources(discover = false)
                        Toast.makeText(this@HomeActivity, "${server.name} är tillagd", Toast.LENGTH_SHORT).show()
                    }
                }
                .setNegativeButton("Avbryt", null)
                .show()
            input.requestFocus()
        }

        private fun selectSource(row: Row, focusGrid: Boolean) {
            if (row.key == "usbperm") {
                Media.requestReadPermission(this@HomeActivity)
                return
            }
            val s = sources.firstOrNull { "src:${it.id}" == row.key } ?: return
            if (s.id != source?.id) {
                source = s
                allCache.clear()
                stack.clear()
                stack.add(s.rootRef to s.label)
                chipAdapter.select(row.key)
                load(focusFirst = focusGrid)
            } else if (focusGrid) {
                focusEntry()
            }
        }

        private fun sourceOptions(row: Row) {
            val s = sources.firstOrNull { "src:${it.id}" == row.key } as? Media.DlnaSource ?: return
            Look.confirm(this@HomeActivity, "Glöm ${s.label}?", "Den hittas igen nästa gång den svarar i hemnätet.", "Glöm") {
                Media.forgetServer(this@HomeActivity, s.server)
                source = null
                lastDiscover = System.currentTimeMillis()
                refreshSources(discover = false)
            }
        }

        // ---------- innehåll

        private fun load(focusFirst: Boolean = false) {
            val s = source ?: return
            val (ref, _) = stack.lastOrNull() ?: return
            val t = ++token
            info.text = "Hämtar…"
            val all = ref.startsWith("flat:")
            val cached = if (all) allCache[ref] else null
            if (cached != null) {
                entries = cached
                render()
                if (focusFirst) focusEntry()
                return
            }
            Api.async({
                if (all) {
                    Media.allVideos(this@HomeActivity, s, ref.removePrefix("flat:")) { done, known ->
                        handler.post { if (t == token) info.text = "Hämtar alla videor: $done av $known mappar…" }
                    }.also { allCache[ref] = it }
                } else {
                    s.list(ref)
                }
            }) { result ->
                if (t != token) return@async
                result.onSuccess { list ->
                    entries = list
                    render()
                    if (focusFirst) focusEntry()
                }.onFailure {
                    entries = emptyList()
                    gridAdapter.submit(emptyList())
                    info.text = if (s is Media.DlnaSource) "${s.label} svarar inte. Är NAS:en på? (${Api.friendly(it)})"
                    else "Kan inte läsa USB-disken. Sitter den i?"
                }
            }
        }

        private fun render() {
            val visible = if (flat()) entries else Media.sort(entries.filter { Media.visible(this@HomeActivity, it) }, byDate)
            applyStyle()
            gridAdapter.submit(visible.map { card(it) })
            grid.scrollToPosition(0)
            val path = stack.joinToString("  ›  ") { it.second }
            val folders = visible.count { it.folder }
            val videos = visible.count { it.kind == "video" }
            val images = visible.count { it.kind == "image" }
            val counts = listOfNotNull(
                folders.takeIf { it > 0 }?.let { "$it mappar" }, videos.takeIf { it > 0 }?.let { "$it videor" },
                images.takeIf { it > 0 }?.let { "$it bilder" },
            ).joinToString(", ")
            info.text = if (visible.isEmpty()) "$path  ·  Tom mapp" else "$path  ·  $counts" +
                if (stack.size > 1) "   ·   Tillbaka: upp en mapp" else ""
            updateButtons()
        }

        private fun card(e: Media.Entry): Card {
            val dateText = e.date?.let { java.text.SimpleDateFormat("d MMM yyyy", java.util.Locale("sv", "SE")).format(java.util.Date(it * 1000)) }
            val sub = when {
                e.parent != null -> listOfNotNull(dateText, e.durationMs?.let { Format.minutes((it / 60_000).toInt().coerceAtLeast(1)) },
                    e.parent).joinToString("  ·  ")
                e.folder -> "Mapp"
                e.kind == "video" -> e.durationMs?.let { Format.minutes((it / 60_000).toInt().coerceAtLeast(1)) }
                    ?: e.date?.let { java.text.SimpleDateFormat("d MMM yyyy", java.util.Locale("sv", "SE")).format(java.util.Date(it * 1000)) }
                else -> e.date?.let { java.text.SimpleDateFormat("d MMM yyyy", java.util.Locale("sv", "SE")).format(java.util.Date(it * 1000)) }
            }
            val badge = when {
                e.hidden -> "Dold"
                e.kind == "video" && e.url != null && Media.isSeen(this@HomeActivity, e.url) -> "✓"
                Media.isFavorite(this@HomeActivity, e.key) -> "★"
                else -> null
            }
            return Card(Card.POSTER, e.key, (if (e.folder) "📁 " else if (e.kind == "image") "🖼 " else "") + e.name, sub, e.thumb,
                badge = badge, payload = e)
        }

        private fun applyStyle() {
            if (gridAdapter.style != style) {
                gridAdapter.style = style
                gridManager.spanCount = if (style == ViewStyle.LIST) 1 else columns
                gridAdapter.notifyDataSetChanged()
            }
            viewButton.text = "Visa: " + ViewStyle.label(style)
        }

        private fun updateButtons() {
            sortButton.text = "Sortera: " + if (flat()) "Alla videor" else if (byDate) "Datum" else "Namn"
        }

        private fun shown(): List<Media.Entry> = gridAdapter.cards.mapNotNull { it.payload as? Media.Entry }

        override fun onCardClick(card: Card, shelf: Shelf?) {
            val e = card.payload as? Media.Entry ?: return
            when (e.kind) {
                "folder" -> {
                    stack.add(e.ref to e.name)
                    load(focusFirst = true)
                }
                "video" -> play(e, fromStart = false)
                "image" -> {
                    val images = shown().filter { it.kind == "image" }
                    ImageViewerActivity.start(this@HomeActivity, images, images.indexOfFirst { it.key == e.key }.coerceAtLeast(0))
                }
            }
        }

        private fun play(e: Media.Entry, fromStart: Boolean) {
            val url = e.url ?: return
            // Filer som boxen inte kan avkoda (t.ex. gamla DivX-filmer) öppnas direkt i VLC.
            if (ExternalPlayer.needsVlc(this@HomeActivity, url) &&
                ExternalPlayer.openInVlc(this@HomeActivity, url, e.name, if (fromStart) 0L else prefs.recordingPosition(url))) return
            val videos = shown().filter { it.kind == "video" && it.url != null }
            if (fromStart) prefs.setRecordingPosition(url, 0L)
            PlayerActivity.start(
                this@HomeActivity, type = "file", id = url, name = e.name, ext = null,
                queueIds = ArrayList(videos.map { it.url!! }), queueNames = ArrayList(videos.map { it.name }),
                queueIndex = videos.indexOfFirst { it.key == e.key }, subtitle = e.subtitle,
                queueSubs = ArrayList(videos.map { it.subtitle ?: "" }), label = source?.label,
            )
        }

        override fun onCardLongClick(card: Card, shelf: Shelf?) {
            val e = card.payload as? Media.Entry ?: return
            val options = ArrayList<Pair<String, () -> Unit>>()
            when (e.kind) {
                "folder" -> {
                    options.add("Öppna" to { onCardClick(card, shelf) })
                    options.add("Alla videor här, nyast först" to { openAll(e.ref, e.name) })
                    options.add("Bildspel av mappen" to { slideshow(e) })
                    if (e.hidden) {
                        if (!Media.isAdultName(e.name)) options.add("Visa mappen igen" to {
                            Media.setHidden(this@HomeActivity, e.key, false)
                            load()
                        })
                    } else {
                        options.add("Dölj mappen" to {
                            Media.setHidden(this@HomeActivity, e.key, true)
                            Toast.makeText(this@HomeActivity, "Dold. Syns bara i vuxenläget.", Toast.LENGTH_LONG).show()
                            load()
                        })
                    }
                }
                "video" -> {
                    val url = e.url ?: return
                    val resume = prefs.recordingPosition(url)
                    options.add((if (resume > 0) "▶ Fortsätt (${Format.clock(resume)})" else "▶ Spela") to { play(e, fromStart = false) })
                    if (resume > 0) options.add("Spela från början" to { play(e, fromStart = true) })
                    val seen = Media.isSeen(this@HomeActivity, url)
                    options.add((if (seen) "Markera som osedd" else "Markera som sedd") to {
                        Media.setSeen(this@HomeActivity, url, !seen)
                        render()
                    })
                }
                "image" -> {
                    options.add("Visa" to { onCardClick(card, shelf) })
                    options.add("Bildspel från den här bilden" to {
                        val images = shown().filter { it.kind == "image" }
                        ImageViewerActivity.start(this@HomeActivity, images, images.indexOfFirst { it.key == e.key }.coerceAtLeast(0), slideshow = true)
                    })
                }
            }
            source?.let { src ->
                val fav = Media.isFavorite(this@HomeActivity, e.key)
                options.add((if (fav) "★ Ta bort ur favoriter" else "☆ Lägg till i favoriter") to {
                    val on = Media.toggleFavorite(this@HomeActivity, e, src)
                    val group = if (src is Media.UsbSource) "USB-disk" else "NAS"
                    Toast.makeText(this@HomeActivity, if (on) "★ Finns under Favoriter → $group" else "Borttagen ur favoriter",
                        Toast.LENGTH_SHORT).show()
                    render()
                })
            }
            if (options.isEmpty()) return
            Look.choose(this@HomeActivity, e.name, options.map { it.first }) { options[it].second() }
        }

        /** Från Favoriter: öppna en favoritmapp. */
        fun openFolder(sourceId: String, ref: String, name: String) {
            refreshSources(discover = false)
            val s = sources.firstOrNull { it.id == sourceId }
            if (s == null) {
                Toast.makeText(this@HomeActivity, if (sourceId.startsWith("usb:")) "Sätt i USB-disken först." else "NAS:en hittas inte just nu.",
                    Toast.LENGTH_LONG).show()
                return
            }
            source = s
            allCache.clear()
            stack.clear()
            stack.add(s.rootRef to s.label)
            if (ref != s.rootRef) stack.add(ref to name)
            chipAdapter.select("src:${s.id}")
            load(focusFirst = true)
        }

        /** Bildspel av alla bilder i en mapp (inte undermappar). */
        private fun slideshow(folder: Media.Entry) {
            val s = source ?: return
            Api.async({ s.list(folder.ref) }) { result ->
                val images = Media.sort(result.getOrNull().orEmpty().filter { it.kind == "image" }, byDate = false)
                if (images.isEmpty()) {
                    Toast.makeText(this@HomeActivity, "Inga bilder direkt i den mappen.", Toast.LENGTH_SHORT).show()
                } else {
                    ImageViewerActivity.start(this@HomeActivity, images, 0, slideshow = true)
                }
            }
        }

        override fun onCardFocus(card: Card, shelf: Shelf?, shelfIndex: Int, cardIndex: Int) {
            markFocused(null)
        }
    }

    // ================================================================ Inspelningar

    private inner class RecordingsPage : Page() {
        override val view: View = LayoutInflater.from(this@HomeActivity).inflate(R.layout.page_list, container, false)
        private val info: TextView = view.findViewById(R.id.info)
        private val list: RecyclerView = view.findViewById(R.id.list)
        private val adapter = RowAdapter(onClick = { row, _ -> (row.payload as? Rec)?.let { open(it) } },
            onLongClick = { row, _ -> (row.payload as? Rec)?.let { options(it) } })

        init {
            view.findViewById<TextView>(R.id.page_title).text = "Inspelningar"
            adapter.modern = true
            list.layoutManager = LinearLayoutManager(this@HomeActivity)
            list.adapter = adapter
            list.itemAnimator = null
        }

        override fun onShow() = load()

        override fun focusEntry(): Boolean {
            if (adapter.itemCount > 0) Focus.position(list, 0)
            return adapter.itemCount > 0
        }

        fun load() {
            Api.arr("/device/recordings") { result ->
                result.onSuccess { array ->
                    val recs = Rec.list(array)
                    val rows = recs.map { rec ->
                        val state = when (rec.status) {
                            "scheduled" -> "Planerad"
                            "recording" -> if (rec.media) "⬇ Laddar ner · ${Rec.size(rec.localFile?.length() ?: rec.sizeBytes)}" else "● Spelar in"
                            "done" -> if (rec.localFile != null) "Klar · ${Rec.size(rec.localFile?.length() ?: rec.sizeBytes)}" else "Klar, men filen finns inte på USB-minnet här"
                            "failed" -> "Misslyckades" + (rec.error?.let { ": $it" } ?: "")
                            else -> rec.status
                        }
                        Row(rec.title, subtitle = "${rec.channel}   ·   ${rec.timeLabel}   ·   $state", key = "rec:${rec.id}", payload = rec)
                    }
                    Focus.submitKeeping(list, adapter, rows)
                    info.text = if (recs.isEmpty()) {
                        "Inga inspelningar. Spela in från programguiden (håll in OK på en kanal), under uppspelning (OK två gånger) eller från webbadminen."
                    } else {
                        "OK: spela   Håll in OK: stoppa eller radera"
                    }
                }.onFailure { info.text = Api.friendly(it) }
            }
        }

        private fun open(rec: Rec) {
            val file = rec.localFile
            when {
                rec.media && rec.status == "recording" ->
                    Toast.makeText(this@HomeActivity, "Laddas ner just nu. Den går att se när den är klar.", Toast.LENGTH_SHORT).show()
                file != null && (rec.status == "done" || rec.status == "recording") ->
                    PlayerActivity.start(this@HomeActivity, type = "file", id = file.absolutePath, name = rec.title, ext = null)
                rec.status == "scheduled" -> Toast.makeText(this@HomeActivity, "Planerad: ${rec.timeLabel}. Boxen måste vara påslagen eller i standby.", Toast.LENGTH_LONG).show()
                rec.status == "recording" -> Toast.makeText(this@HomeActivity, "Inspelningen har precis börjat. Försök igen om en stund.", Toast.LENGTH_SHORT).show()
                rec.status == "failed" -> Look.choose(this@HomeActivity, "Inspelningen misslyckades", listOf("OK"), subtitle = rec.error ?: "Okänt fel.") {}
                else -> Toast.makeText(this@HomeActivity, "Filen finns inte här. Den kan ligga på den andra boxens USB-minne.", Toast.LENGTH_LONG).show()
            }
        }

        private fun options(rec: Rec) {
            val labels = ArrayList<String>()
            val actions = ArrayList<() -> Unit>()
            if (rec.localFile != null) { labels.add("Spela"); actions.add { open(rec) } }
            if (rec.status == "recording") {
                labels.add(if (rec.media) "Avbryt nedladdningen" else "Stoppa inspelningen (behåll det inspelade)")
                actions.add {
                    RecScheduler.send(this@HomeActivity, RecScheduler.ACTION_STOP, rec.id)
                    handler.postDelayed({ load() }, 2_500)
                }
            }
            labels.add(if (rec.status == "scheduled") "Ta bort planeringen" else "Radera inspelningen")
            actions.add {
                Look.confirm(this@HomeActivity, "Radera \"${rec.title}\"?",
                    if (rec.status == "scheduled") "Programmet spelas inte in." else "Filen raderas från USB-minnet.", "Radera") {
                    RecScheduler.send(this@HomeActivity, RecScheduler.ACTION_DELETE, rec.id)
                    handler.postDelayed({ load() }, 1_500)
                }
            }
            Look.choose(this@HomeActivity, rec.title, labels) { actions[it]() }
        }
    }

    // ================================================================ Inställningar

    private fun showSettings() {
        val labels = listOf(
            "Utseende: Modernt (byt till Klassiskt)",
            "Var inspelningar sparas",
            "Testa hastigheten i hemnätet",
            "Sök efter uppdatering av appen",
            "Hämta listor igen",
            "Para om den här boxen",
            "Om TV Hub",
            "Fjärrkontrollens knappar",
            "Röststyrning när appen är stängd",
            "Mobilläge (för telefon)",
            "Nytt per dag",
            "Starta på senaste kanalen: " + if (prefs.startOnLastChannel) "På" else "Av",
        )
        Look.choose(this, "Inställningar", labels) { which ->
            when (which) {
                0 -> {
                    prefs.modern = false
                    startActivity(Intent(this, MainActivity::class.java))
                    finish()
                }
                1 -> chooseStorage()
                2 -> runSpeedTest()
                3 -> Updater.check(this, prefs, quiet = false)
                4 -> resetMode()
                5 -> Look.confirm(this, "Para om boxen?", "Du behöver en ny kod från webbadminen.", "Para om") {
                    prefs.clearPairing()
                    startActivity(Intent(this, SetupActivity::class.java))
                    finish()
                }
                6 -> Look.choose(this, "TV Hub ${BuildConfig.VERSION_NAME}", listOf("OK"), subtitle =
                    "Server: ${prefs.server}\nBox: ${prefs.deviceName}\n\n" +
                        "Menyn till vänster: upp/ner väljer, höger går in. Håll in OK för fler val.\n" +
                        "Under uppspelning: upp/ner byter kanal, vänster/höger spolar i filmer, OK visar val.\n" +
                        "Tillbaka två gånger i menyn stänger appen.") {}
                7 -> Keys.showSettings(this)
                8 -> Control.showSettings(this)
                10 -> NewDays.show(this)
                11 -> {
                    prefs.startOnLastChannel = !prefs.startOnLastChannel
                    Toast.makeText(this, if (prefs.startOnLastChannel) "Appen startar på senaste kanalen" else "Appen startar på Hem",
                        Toast.LENGTH_SHORT).show()
                }
                9 -> Look.confirm(this, "Byta till mobilläge?", "Mobilläget är gjort för touch på en telefon. På en TV-box behövs det inte.", "Byt") {
                    prefs.layoutMode = "mobile"
                    startActivity(Intent(this, MobileActivity::class.java))
                    finish()
                }
            }
        }
    }

    private fun chooseStorage() {
        val volumes = RecStorage.volumes(this)
        if (volumes.isEmpty()) {
            Toast.makeText(this, "Ingen lagring hittades.", Toast.LENGTH_LONG).show()
            return
        }
        val chosen = RecStorage.chosen(this, prefs)
        val selected = volumes.indexOfFirst { it.dir.absolutePath == chosen?.dir?.absolutePath }
        val note = if (volumes.none { it.removable }) "Inget USB-minne hittades. Sätt i ett och öppna det här igen." else null
        Look.choose(this, "Var inspelningar sparas", volumes.map { it.label }, selected, note) { which ->
            prefs.recordDir = volumes[which].dir.absolutePath
            Toast.makeText(this, "Inspelningar sparas på: ${volumes[which].label}", Toast.LENGTH_LONG).show()
        }
    }

    private fun runSpeedTest() {
        Toast.makeText(this, "Mäter hastigheten mot Home Assistant…", Toast.LENGTH_SHORT).show()
        Api.async({ SpeedTest.runBlocking(prefs, 20) }) { result ->
            result.onSuccess { mbps ->
                Api.report("speedtest", null, JSONObject().put("mbps", mbps).put("trigger", "manual"))
                val verdict = if (mbps >= 30) {
                    "Det räcker gott för Toucans krav på 30 Mbit/s. Om bilden ändå stannar ligger felet utanför hemnätet; se Diagnostik i webbadminen."
                } else {
                    "Det är under Toucans krav på 30 Mbit/s. Wi-Fi eller kabeln till boxen är troligen flaskhalsen. Prova nätverkskabel."
                }
                Look.choose(this, "$mbps Mbit/s i hemnätet", listOf("OK"), subtitle = "Mätt mellan boxen och Home Assistant.\n\n$verdict") {}
            }.onFailure { Toast.makeText(this, Api.friendly(it), Toast.LENGTH_LONG).show() }
        }
    }

    // ================================================================ fjärrkontroll

    override fun dispatchKeyEvent(event: KeyEvent): Boolean {
        if (::rail.isInitialized && event.action == KeyEvent.ACTION_DOWN) {
            if (shownAdult && !prefs.adultMode) {
                resetMode()
                return true
            }
            prefs.touch()
            val onSettings = currentFocus?.tag == "settings"
            if (onSettings && event.repeatCount == 0 && event.keyCode == KeyEvent.KEYCODE_DPAD_LEFT) {
                val t = SystemClock.elapsedRealtime()
                if (t - secretStartedAt > 2_000) {
                    secretStartedAt = t
                    secretPresses = 0
                }
                if (++secretPresses >= 3) {
                    secretPresses = 0
                    secretStartedAt = 0L
                    AdultGate.toggle(this) {
                        resetMode()
                        handler.postDelayed({ railViews["settings"]?.requestFocus() }, 300)
                    }
                }
                return true
            }
            if (onSettings && event.repeatCount == 0 && event.keyCode == KeyEvent.KEYCODE_DPAD_RIGHT && prefs.adultMode) {
                val t = SystemClock.elapsedRealtime()
                if (t - historyStartedAt > 2_000) {
                    historyStartedAt = t
                    historyPresses = 0
                }
                if (++historyPresses >= 3) {
                    historyPresses = 0
                    historyStartedAt = 0L
                    prefs.adultHistory = !prefs.adultHistory
                    Toast.makeText(this, if (prefs.adultHistory) "Historik visas" else "Historik dold", Toast.LENGTH_SHORT).show()
                    resetMode()
                    handler.postDelayed({ railViews["settings"]?.requestFocus() }, 300)
                }
                return true
            }
            if (navigationKey(event.keyCode)) return true
            if (event.repeatCount == 0) {
                val code = event.keyCode
                if (current == "search" && currentFocus !is EditText) {
                    // Sök: siffrorna skriver i sökrutan.
                    val d = when (code) {
                        in KeyEvent.KEYCODE_0..KeyEvent.KEYCODE_9 -> code - KeyEvent.KEYCODE_0
                        else -> -1
                    }
                    if (d >= 0) {
                        searchPage?.type(d.toString())
                        return true
                    }
                } else if (currentFocus !is EditText && digits.onKey(code)) {
                    return true
                }
                Keys.browseAction(prefs, code)?.let { if (runKey(it)) return true }
            }
        }
        return super.dispatchKeyEvent(event)
    }

    private fun isInside(view: View, parent: View): Boolean {
        var v: View? = view
        while (v != null) {
            if (v === parent) return true
            v = v.parent as? View
        }
        return false
    }

    /**
     * Pilar som skulle hamna i menyn från sidan: vänster går till menyvalet för sidan man är på
     * (inte det som råkar ligga närmast), upp/ner stannar kvar. Sidan får först bestämma själv.
     */
    private fun navigationKey(code: Int): Boolean {
        if (code != KeyEvent.KEYCODE_DPAD_LEFT && code != KeyEvent.KEYCODE_DPAD_UP && code != KeyEvent.KEYCODE_DPAD_DOWN) return false
        val focused = currentFocus ?: return false
        if (isInRail(focused)) return false
        if (currentPage()?.onEdge(focused, code) == true) return true
        val direction = when (code) {
            KeyEvent.KEYCODE_DPAD_LEFT -> View.FOCUS_LEFT
            KeyEvent.KEYCODE_DPAD_UP -> View.FOCUS_UP
            else -> View.FOCUS_DOWN
        }
        val next = try {
            focused.focusSearch(direction)
        } catch (_: Exception) {
            null
        }
        if (next == null || !isInRail(next)) return false
        if (code == KeyEvent.KEYCODE_DPAD_LEFT) (railViews[current] ?: railViews["home"])?.requestFocus()
        return true
    }

    /** Tillbaka: till Hem (menyn fälls ihop). På Hem: först upp till första raden, sedan fråga om stänga. */
    private fun goHome() {
        navTask?.let { handler.removeCallbacks(it) }
        digits.cancel()
        if (current != "home") showPage("home")
        homePage?.focusFirst()
    }

    private fun markFocused(item: Item?) {
        focusedItem = item
        focusedPage = current
    }

    /** Det markerade kortet eller kanalen, om fokus fortfarande ligger på sidan (inte i menyn). */
    private fun focused(): Item? {
        val view = currentFocus ?: return null
        if (isInRail(view) || focusedPage != current) return null
        return focusedItem
    }

    private fun needFocused(what: String = "en titel"): Item? {
        val item = focused()
        if (item == null) Toast.makeText(this, "Markera $what först.", Toast.LENGTH_SHORT).show()
        return item
    }

    /** Fjärrkontrollens knappar i menyerna, se [Keys]. */
    private fun runKey(action: String): Boolean {
        when (action) {
            "options" -> {
                val view = currentFocus ?: return false
                if (isInRail(view)) return false
                view.performLongClick()
            }
            "favorite" -> {
                val item = needFocused() ?: return true
                if (privateMode() || item.type == "file") return true
                toggleFavorite(item)
            }
            "groups" -> {
                val page = currentPage()
                if (page == null || !page.chooseGroup()) {
                    showPage("live")
                    handler.post { if (livePage?.chooseGroup() != true) focusPage() }
                }
            }
            "night" -> FilmNightActivity.start(this)
            "search", "live", "home" -> onRailClick(action)
            "favorites" -> onRailClick("favorites")
            "new" -> showNew()
            "info" -> {
                val item = needFocused() ?: return true
                when (item.type) {
                    "vod" -> DetailActivity.start(this, item.id, item.name)
                    "series" -> SeriesActivity.start(this, item.id, item.name)
                    "live" -> GuideActivity.start(this, item.id, item.name)
                    "episode" -> item.seriesId?.let { SeriesActivity.start(this, it, item.categoryName ?: item.name, item.id) }
                }
            }
            "guide" -> {
                val item = focused()
                if (item?.type == "live") GuideActivity.start(this, item.id, item.name)
                else Toast.makeText(this, "Markera en kanal först.", Toast.LENGTH_SHORT).show()
            }
            "record" -> {
                val item = needFocused() ?: return true
                when (item.type) {
                    "live" -> recordNowOn(item)
                    "vod" -> RecordMedia.film(this, item.id)
                    "series" -> RecordMedia.series(this, item.id)
                    else -> Toast.makeText(this, "Det går inte att spela in.", Toast.LENGTH_SHORT).show()
                }
            }
            else -> return false
        }
        return true
    }

    /** Spelar in programmet som sänds nu på kanalen. */
    private fun recordNowOn(channel: Item) {
        Api.arr("/device/epg/${Api.enc(channel.id)}") { result ->
            result.onSuccess { array ->
                val now = System.currentTimeMillis() / 1000
                val program = (0 until array.length()).mapNotNull { array.optJSONObject(it) }
                    .firstOrNull { it.optLong("start") <= now && it.optLong("end") > now }
                if (program == null) {
                    GuideActivity.start(this, channel.id, channel.name)
                    return@onSuccess
                }
                val title = program.optString("title")
                Look.confirm(this, "● Spela in?", "${channel.name}: $title\n" +
                    "${timeFormat.format(Date(program.optLong("start") * 1000))}–${timeFormat.format(Date(program.optLong("end") * 1000))}",
                    "Spela in") {
                    val body = JSONObject().put("stream_id", channel.id).put("title", title)
                        .put("start", program.optLong("start")).put("end", program.optLong("end"))
                    Api.obj("POST", "/device/recordings", body) { r ->
                        r.onSuccess {
                            RecScheduler.sync(this)
                            Toast.makeText(this, "● Spelar in \"$title\" till USB-minnet.", Toast.LENGTH_LONG).show()
                        }.onFailure { Toast.makeText(this, Api.friendly(it), Toast.LENGTH_LONG).show() }
                    }
                }
            }.onFailure { Toast.makeText(this, Api.friendly(it), Toast.LENGTH_LONG).show() }
        }
    }

    private fun showNew() {
        Api.obj("GET", "/device/new?type=all&limit=80") { result ->
            result.onSuccess { data ->
                val items = Item.list(data.optJSONArray("items"))
                if (items.isEmpty()) {
                    Toast.makeText(this, "Inget nytt ännu.", Toast.LENGTH_SHORT).show()
                    return@onSuccess
                }
                val labels = items.map { item ->
                    listOfNotNull(item.name, item.typeLabel.ifEmpty { null }, item.added?.let { Format.addedLabel(it) }).joinToString("  ·  ")
                }
                Look.choose(this, "Nytt tillagt", labels) { openItem(items[it], items) }
            }.onFailure { Toast.makeText(this, Api.friendly(it), Toast.LENGTH_LONG).show() }
        }
    }

    /** Sifferknapparna: kanalen med numret. Spelas med kanallistan om den är öppen. */
    private fun onDigits(text: String) {
        DigitEntry.lookup(this, text) { channel ->
            val list = livePage?.channels()?.takeIf { l -> current == "live" && l.any { it.id == channel.id } } ?: listOf(channel)
            openItem(channel, list)
        }
    }

    @Deprecated("Deprecated in Java")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        val text = Voice.result(requestCode, resultCode, data) ?: return
        if (current != "search") showPage("search")
        searchPage?.setQuery(text)
    }

    @Deprecated("Deprecated in Java")
    override fun onBackPressed() {
        if (!::rail.isInitialized) {
            super.onBackPressed()
            return
        }
        val focused = currentFocus
        if (focused != null && !isInRail(focused) && currentPage()?.onBack() == true) return
        if (current != "home") {
            goHome()
            return
        }
        if (focused != null && !isInRail(focused) && homePage?.goTop() == true) return
        if (focused != null && isInRail(focused) && homePage != null && focused.tag != "home") {
            goHome()
            return
        }
        val t = SystemClock.elapsedRealtime()
        if (t - backPressedAt > 2_000) {
            backPressedAt = t
            Toast.makeText(this, "Tryck Tillbaka igen för att stänga", Toast.LENGTH_SHORT).show()
            return
        }
        super.onBackPressed()
    }
}
