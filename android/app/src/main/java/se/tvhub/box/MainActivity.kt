package se.tvhub.box

import android.app.Activity
import android.app.AlertDialog
import android.content.Intent
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.text.Editable
import android.text.TextWatcher
import android.view.KeyEvent
import android.view.View
import android.view.inputmethod.EditorInfo
import android.widget.EditText
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
 * Huvudskärm.
 *
 * Tre kolumner: meny, kategorier och innehåll. Fokus förhandsvisar och OK öppnar
 * innehållet i helskärm (affischrutnät för filmer och serier). Höger från innehållet
 * öppnar också helskärm, och Tillbaka eller vänster från första kolumnen går ur.
 * I helskärm finns Sortera och Filter överst. Håll in OK för fler val.
 */
class MainActivity : Activity() {
    companion object {
        private const val PAGE = 200
        private const val GRID_COLUMNS = 7
    }

    /** En sorterbar och filtrerbar lista från TV Hub. */
    private data class Listing(
        val title: String,
        val type: String,
        val categoryId: String?,
        val params: MutableMap<String, String>,
    )

    private lateinit var prefs: Prefs
    private lateinit var menuPane: View
    private lateinit var menuList: RecyclerView
    private lateinit var middlePane: View
    private lateinit var middleTitle: TextView
    private lateinit var middleList: RecyclerView
    private lateinit var itemsList: RecyclerView
    private lateinit var paneTitle: TextView
    private lateinit var totalView: TextView
    private lateinit var toolbar: View
    private lateinit var sortButton: TextView
    private lateinit var filterButton: TextView
    private lateinit var searchBox: EditText
    private lateinit var status: TextView
    private lateinit var info: TextView
    private lateinit var menuAdapter: RowAdapter
    private lateinit var middleAdapter: RowAdapter
    private lateinit var itemsAdapter: RowAdapter

    private val handler = Handler(Looper.getMainLooper())
    private var home: JSONObject? = null
    private var shownAdult = false
    private var section: String? = null
    private var categoryKey: String? = null
    private var listing: Listing? = null
    private var entries: List<Any> = emptyList()   // Item eller ContinueItem
    private var total = 0
    private var loadingMore = false
    private var fullscreen = false
    private var lastSearch: List<Item> = emptyList()
    private var genres: List<String> = emptyList()
    private val epgNow = HashMap<String, String>()
    private var loadToken = 0
    private var epgToken = 0
    private var menuTask: Runnable? = null
    private var categoryTask: Runnable? = null
    private var epgTask: Runnable? = null
    private var searchTask: Runnable? = null
    private var retryTask: Runnable? = null
    private val timeFormat = SimpleDateFormat("HH:mm", Locale("sv", "SE"))

    // Dolt lägesbyte (gesten beskrivs i de interna anteckningarna, inte här).
    private var secretPresses = 0
    private var secretStartedAt = 0L

    // Dolt val i det dolda läget (se interna anteckningar).
    private var historyPresses = 0
    private var historyStartedAt = 0L

    // Tillbaka två gånger i menyn stänger appen.
    private var backPressedAt = 0L

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        prefs = Prefs(this)
        Api.init(prefs)
        if (!Session.started) {
            // Ny start av appen eller boxen: vuxenläget slås alltid av.
            Session.started = true
            if (prefs.adultMode) prefs.adultMode = false
        }
        if (!prefs.paired) {
            openSetup()
            return
        }
        if (!Session.recordingsSynced) {
            Session.recordingsSynced = true
            RecScheduler.sync(this)
        }
        // Röststyrning och fjärrstart (bara TV-boxarna).
        Control.start(this)
        if (FormFactor.isMobile(this)) {
            // Telefonen: mobilläget (samma app, eget utseende).
            startActivity(Intent(this, MobileActivity::class.java))
            finish()
            return
        }
        if (prefs.modern) {
            // Det moderna utseendet är standard. Det klassiska väljs under Inställningar → Utseende.
            startActivity(Intent(this, HomeActivity::class.java))
            startLastChannel()
            finish()
            return
        }
        startLastChannel()
        setContentView(R.layout.activity_main)
        window.decorView.post { Control.handleIntent(this, intent) }
        menuPane = findViewById(R.id.menu_pane)
        menuList = findViewById(R.id.menu)
        middlePane = findViewById(R.id.middle_pane)
        middleTitle = findViewById(R.id.middle_title)
        middleList = findViewById(R.id.middle)
        itemsList = findViewById(R.id.items)
        paneTitle = findViewById(R.id.pane_title)
        totalView = findViewById(R.id.total)
        toolbar = findViewById(R.id.toolbar)
        sortButton = findViewById(R.id.sort_button)
        filterButton = findViewById(R.id.filter_button)
        searchBox = findViewById(R.id.search)
        status = findViewById(R.id.status)
        info = findViewById(R.id.info)

        menuAdapter = RowAdapter(
            onClick = { row, _ -> onMenuClick(row) },
            onFocus = { row, _ -> onMenuFocus(row) },
            onLongClick = { row, _ -> onMenuLongClick(row) },
        )
        middleAdapter = RowAdapter(
            onClick = { row, _ -> loadCategory(row, open = true) },
            onFocus = { row, _ -> onCategoryFocus(row) },
        )
        itemsAdapter = RowAdapter(
            onClick = { row, _ -> onItemClick(row) },
            onFocus = { row, position -> onItemFocus(row, position) },
            onLongClick = { row, position -> onItemLongClick(row, position) },
            stopAtTop = { toolbar.visibility != View.VISIBLE && section != "search" },
            onKey = { _, position, keyCode -> onItemKey(position, keyCode) },
        )
        for ((list, adapter) in listOf(menuList to menuAdapter, middleList to middleAdapter, itemsList to itemsAdapter)) {
            list.layoutManager = LinearLayoutManager(this)
            list.adapter = adapter
            list.itemAnimator = null
        }
        itemsList.addOnScrollListener(object : RecyclerView.OnScrollListener() {
            override fun onScrollStateChanged(recyclerView: RecyclerView, newState: Int) {
                if (newState == RecyclerView.SCROLL_STATE_IDLE) requestEpgForVisible()
            }
        })

        sortButton.setOnClickListener { showSortDialog() }
        filterButton.setOnClickListener { showFilterDialog() }
        searchBox.setOnEditorActionListener { _, actionId, _ ->
            if (actionId == EditorInfo.IME_ACTION_SEARCH || actionId == EditorInfo.IME_ACTION_DONE) {
                runSearch()
                Focus.first(itemsList)
                true
            } else {
                false
            }
        }
        searchBox.addTextChangedListener(object : TextWatcher {
            override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) {}
            override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) {}
            override fun afterTextChanged(s: Editable?) {
                searchTask?.let { handler.removeCallbacks(it) }
                val task = Runnable { runSearch() }
                searchTask = task
                handler.postDelayed(task, 600)
            }
        })
    }

    override fun onResume() {
        super.onResume()
        if (!::menuList.isInitialized) return
        if (shownAdult && !prefs.adultMode) {
            resetMode()
            return
        }
        loadHome()
    }

    override fun onDestroy() {
        handler.removeCallbacksAndMessages(null)
        super.onDestroy()
    }

    override fun dispatchKeyEvent(event: KeyEvent): Boolean {
        if (::menuList.isInitialized && event.action == KeyEvent.ACTION_DOWN) {
            // Vuxenläget kan ha gått ut medan ingen rörde fjärrkontrollen.
            if (shownAdult && !prefs.adultMode) {
                resetMode()
                return true
            }
            prefs.touch()
            if (event.keyCode == KeyEvent.KEYCODE_DPAD_LEFT && event.repeatCount == 0 && isOnSettingsRow()) {
                val now = SystemClock.elapsedRealtime()
                if (now - secretStartedAt > 2_000) {
                    secretStartedAt = now
                    secretPresses = 0
                }
                secretPresses++
                if (secretPresses >= 3) {
                    secretPresses = 0
                    secretStartedAt = 0L
                    AdultGate.toggle(this) { resetMode() }
                }
                return true
            }
            if (event.keyCode == KeyEvent.KEYCODE_DPAD_RIGHT && event.repeatCount == 0 && prefs.adultMode && isOnSettingsRow()) {
                val now = SystemClock.elapsedRealtime()
                if (now - historyStartedAt > 2_000) {
                    historyStartedAt = now
                    historyPresses = 0
                }
                historyPresses++
                if (historyPresses >= 3) {
                    historyPresses = 0
                    historyStartedAt = 0L
                    prefs.adultHistory = !prefs.adultHistory
                    Toast.makeText(this, if (prefs.adultHistory) "Historik visas" else "Historik dold", Toast.LENGTH_SHORT).show()
                    resetMode()
                }
                return true
            }
        }
        return super.dispatchKeyEvent(event)
    }

    /** Sektioner med en mellankolumn: kategorier och "Nytt tillagt". */
    private fun hasMiddle(key: String?): Boolean = key != null && (key.startsWith("cat:") || key == "recent")

    /** Vuxenläge utan historik: favoriter och historik visas inte. */
    private fun privateMode(): Boolean = home?.optString("mode") == "adult" && home?.optBoolean("history", true) == false

    private fun isOnSettingsRow(): Boolean {
        val item = currentFocus?.let { menuList.findContainingItemView(it) } ?: return false
        val position = menuList.getChildAdapterPosition(item)
        return position != RecyclerView.NO_POSITION && menuAdapter.rows.getOrNull(position)?.key == "settings"
    }

    private fun resetMode() {
        home = null
        section = null
        categoryKey = null
        listing = null
        lastSearch = emptyList()
        epgNow.clear()
        searchBox.setText("")
        if (fullscreen) exitFullscreen(restoreFocus = false)
        loadHome()
    }

    @Deprecated("Deprecated in Java")
    override fun onBackPressed() {
        if (!::menuList.isInitialized) {
            super.onBackPressed()
            return
        }
        if (fullscreen) {
            exitFullscreen(restoreFocus = true)
            return
        }
        val focused = currentFocus
        val inItems = Focus.contains(itemsList, focused) || focused == sortButton || focused == filterButton
        if (inItems && middlePane.visibility == View.VISIBLE) {
            Focus.position(middleList, middleAdapter.indexOf(categoryKey).coerceAtLeast(0))
            return
        }
        if (inItems || Focus.contains(middleList, focused) || focused == searchBox) {
            Focus.position(menuList, menuAdapter.indexOf(section).coerceAtLeast(0))
            return
        }
        // I menyn: två tryck inom två sekunder för att stänga appen.
        val now = SystemClock.elapsedRealtime()
        if (now - backPressedAt > 2_000) {
            backPressedAt = now
            Toast.makeText(this, "Tryck Tillbaka igen för att stänga", Toast.LENGTH_SHORT).show()
            return
        }
        super.onBackPressed()
    }

    // ================================================================ hemdata

    private fun loadHome() {
        Api.obj("GET", "/device/home?app_version=${Api.enc(BuildConfig.VERSION_NAME)}") { result ->
            result.onSuccess { data ->
                // Boxens profil kan förbjuda vuxenläget.
                if (prefs.adultMode && data.optString("mode") != "adult") prefs.adultMode = false
                shownAdult = data.optString("mode") == "adult"
                val first = home == null
                home = data
                retryTask?.let { handler.removeCallbacks(it) }
                buildMenu()
                val current = section
                if (first || current == null || menuAdapter.indexOf(current) < 0) {
                    val start = when {
                        data.optJSONArray("continue")?.length() ?: 0 > 0 -> "continue"
                        data.optJSONArray("favorites")?.length() ?: 0 > 0 -> "favorites"
                        else -> menuAdapter.rows.firstOrNull { it.key.startsWith("cat:") || it.key.startsWith("group:") }?.key ?: "search"
                    }
                    if (fullscreen) exitFullscreen(restoreFocus = false)
                    showSection(start)
                    Focus.position(menuList, menuAdapter.indexOf(start))
                } else if (current in setOf("continue", "favorites", "recent_live", "hits", "recordings") || current.startsWith("group:")) {
                    showSection(current, keepFocus = true)
                }
                if (!Session.updateChecked) {
                    Session.updateChecked = true
                    Updater.check(this, prefs, quiet = true)
                }
            }.onFailure { handleError(it) }
        }
    }

    private fun handleError(error: Throwable) {
        if (error is ApiException && error.status == 401) {
            Toast.makeText(this, "Boxen behöver paras om.", Toast.LENGTH_LONG).show()
            prefs.clearPairing()
            openSetup()
            return
        }
        showStatus("${Api.friendly(error)}\nFörsöker igen automatiskt…")
        if (home == null) {
            retryTask?.let { handler.removeCallbacks(it) }
            val task = Runnable { loadHome() }
            retryTask = task
            handler.postDelayed(task, 10_000)
        }
    }

    private fun countLabel(n: Int?): String? = n?.let { "$it st" }

    private fun buildMenu() {
        val data = home ?: return
        val rows = ArrayList<Row>()
        val cont = data.optJSONArray("continue")?.length() ?: 0
        if (cont > 0) rows.add(Row("Fortsätt titta", subtitle = countLabel(cont), key = "continue"))
        rows.add(Row("Sök", key = "search"))
        if (!privateMode()) rows.add(Row("Favoriter", subtitle = countLabel(data.optJSONArray("favorites")?.length()), key = "favorites"))
        val recordings = data.optInt("recordings")
        rows.add(Row("Inspelningar", subtitle = if (recordings > 0) countLabel(recordings) else null, key = "recordings"))
        val recentLive = data.optJSONArray("recent_live")?.length() ?: 0
        if (recentLive > 0) rows.add(Row("Senaste kanaler", subtitle = countLabel(recentLive), key = "recent_live"))
        val hits = data.optJSONArray("watch_hits")?.length() ?: 0
        if (hits > 0) rows.add(Row("Nytt från bevakningar", subtitle = countLabel(hits), key = "hits"))
        val filters = data.optJSONArray("saved_filters") ?: JSONArray()
        for (i in 0 until filters.length()) {
            val f = filters.optJSONObject(i) ?: continue
            rows.add(Row(f.optString("name"), subtitle = "Sparat filter", key = "filter:${f.optInt("id")}", payload = f))
        }
        val groups = data.optJSONArray("groups") ?: JSONArray()
        for (i in 0 until groups.length()) {
            val g = groups.optJSONObject(i) ?: continue
            rows.add(Row(g.optString("name"), subtitle = countLabel(g.optInt("count")), key = "group:${g.optInt("id")}"))
        }
        val counts = data.optJSONObject("counts")
        val categories = data.optJSONObject("categories")
        for ((type, label) in listOf("live" to "Kanaler", "vod" to "Filmer", "series" to "Serier")) {
            if ((categories?.optJSONArray(type)?.length() ?: 0) > 0) {
                rows.add(Row(label, subtitle = countLabel(counts?.optJSONObject(type)?.optInt("items")), key = "cat:$type"))
            }
        }
        if (data.optJSONObject("profile")?.isNull("only_group_id") != false) rows.add(Row("Nytt tillagt", key = "recent"))
        rows.add(Row("Inställningar", key = "settings"))
        Focus.submitKeeping(menuList, menuAdapter, rows)
        menuAdapter.select(section)
    }

    // ================================================================ meny

    private fun onMenuFocus(row: Row) {
        menuTask?.let { handler.removeCallbacks(it) }
        if (row.key == section || row.key == "settings") return
        val task = Runnable { showSection(row.key) }
        menuTask = task
        handler.postDelayed(task, 220)
    }

    private fun onMenuClick(row: Row) {
        menuTask?.let { handler.removeCallbacks(it) }
        if (row.key == "settings") {
            showSettings()
            return
        }
        if (row.key != section) showSection(row.key)
        when {
            row.key == "search" -> searchBox.requestFocus()
            hasMiddle(row.key) -> Focus.first(middleList)
            entries.isNotEmpty() || listing != null -> enterFullscreen()
            else -> Focus.first(itemsList)
        }
    }

    private fun onMenuLongClick(row: Row) {
        if (!row.key.startsWith("filter:")) return
        val id = row.key.removePrefix("filter:")
        AlertDialog.Builder(this)
            .setTitle("Ta bort \"${row.title}\"?")
            .setMessage("Det sparade filtret försvinner från menyn.")
            .setPositiveButton("Ta bort") { _, _ ->
                Api.obj("DELETE", "/device/filters/$id") { result ->
                    result.onSuccess {
                        if (section == row.key) section = null
                        loadHome()
                    }.onFailure { Toast.makeText(this, Api.friendly(it), Toast.LENGTH_LONG).show() }
                }
            }
            .setNegativeButton("Avbryt", null)
            .show()
    }

    private fun showSection(key: String, keepFocus: Boolean = false) {
        val data = home ?: return
        section = key
        menuAdapter.select(key)
        info.text = ""
        loadToken++
        val isCategoryView = hasMiddle(key)
        middlePane.visibility = if (isCategoryView && !fullscreen) View.VISIBLE else View.GONE
        searchBox.visibility = if (key == "search") View.VISIBLE else View.GONE
        listing = null
        totalView.text = ""

        when {
            key == "continue" -> {
                paneTitle.text = "Fortsätt titta"
                showEntries(ContinueItem.list(data.optJSONArray("continue")), keepFocus, "Inget påbörjat.")
            }
            key == "search" -> {
                paneTitle.text = "Sök"
                showEntries(lastSearch, keepFocus, "Skriv minst två tecken. Sökningen gäller kanaler, filmer och serier.")
            }
            key == "favorites" -> {
                paneTitle.text = "Favoriter"
                showEntries(Item.list(data.optJSONArray("favorites")), keepFocus,
                    "Inga favoriter ännu. Håll in OK på en kanal, film eller serie.")
            }
            key == "recordings" -> {
                paneTitle.text = "Inspelningar"
                if (!keepFocus) showEntries(emptyList(), false, "Hämtar…")
                val token = loadToken
                Api.arr("/device/recordings") { result ->
                    if (token != loadToken || section != "recordings") return@arr
                    result.onSuccess { array ->
                        showEntries(Rec.list(array), keepFocus,
                            "Inga inspelningar. Öppna programguiden för en kanal (håll in OK på kanalen) och välj ett program. " +
                                "Du kan också planera från mobilen under Inspelningar i webbadminen.")
                    }.onFailure { handleError(it) }
                }
            }
            key == "recent_live" -> {
                paneTitle.text = "Senaste kanaler"
                showEntries(Item.list(data.optJSONArray("recent_live")), keepFocus, "Inga kanaler än.")
            }
            key == "hits" -> {
                paneTitle.text = "Nytt från bevakningar"
                showEntries(Item.list(data.optJSONArray("watch_hits")), keepFocus, "Inget nytt.")
            }
            key == "recent" -> {
                middleTitle.text = "Nytt tillagt"
                paneTitle.text = ""
                categoryKey = null
                val rows = listOf("vod" to "Filmer", "series" to "Serier", "live" to "Kanaler").map { (type, label) ->
                    Row(label, subtitle = "Senast tillagda först", key = "$type:new", payload = Pair(type, "new"))
                }
                middleAdapter.submit(rows)
                middleList.scrollToPosition(0)
                showEntries(emptyList(), false, "Välj Filmer, Serier eller Kanaler. Det nyaste visas först, med datum.")
            }
            key.startsWith("filter:") -> {
                val f = menuAdapter.rows.firstOrNull { it.key == key }?.payload as? JSONObject
                if (f == null) {
                    showEntries(emptyList(), false, "Filtret finns inte längre.")
                } else {
                    val params = HashMap<String, String>()
                    val p = f.optJSONObject("params") ?: JSONObject()
                    for (name in p.keys()) params[name] = p.optString(name)
                    val categoryId = params.remove("category_id")
                    listing = Listing(f.optString("name"), f.optString("type", "vod"), categoryId, params)
                    paneTitle.text = f.optString("name")
                    loadListing(reset = true)
                }
            }
            key.startsWith("group:") -> {
                val id = key.removePrefix("group:").toIntOrNull()
                val groups = data.optJSONArray("groups") ?: JSONArray()
                var group: JSONObject? = null
                for (i in 0 until groups.length()) {
                    if (groups.optJSONObject(i)?.optInt("id") == id) group = groups.optJSONObject(i)
                }
                paneTitle.text = group?.optString("name") ?: "Grupp"
                showEntries(Item.list(group?.optJSONArray("items")), keepFocus, "Gruppen är tom. Lägg till innehåll i webbadminen.")
            }
            isCategoryView -> {
                val type = key.removePrefix("cat:")
                middleTitle.text = typeTitle(type)
                paneTitle.text = ""
                categoryKey = null
                val categories = data.optJSONObject("categories")?.optJSONArray(type) ?: JSONArray()
                val rows = ArrayList<Row>()
                rows.add(Row("Alla ${typeTitle(type).lowercase()}", key = "$type:all", payload = Pair(type, "all")))
                for (i in 0 until categories.length()) {
                    val c = categories.optJSONObject(i) ?: continue
                    rows.add(
                        Row(
                            c.optString("name"),
                            subtitle = countLabel(c.optInt("count")),
                            key = "$type:${c.optString("category_id")}",
                            payload = Pair(type, c.optString("category_id")),
                        )
                    )
                }
                middleAdapter.submit(rows)
                middleList.scrollToPosition(0)
                showEntries(emptyList(), false, "Välj en kategori. OK öppnar i helskärm.")
            }
        }
        updateToolbar()
    }

    private fun typeTitle(type: String) = when (type) {
        "live" -> "Kanaler"
        "vod" -> "Filmer"
        else -> "Serier"
    }

    // ================================================================ kategorier

    private fun onCategoryFocus(row: Row) {
        categoryTask?.let { handler.removeCallbacks(it) }
        if (row.key == categoryKey) return
        val task = Runnable { loadCategory(row, open = false) }
        categoryTask = task
        handler.postDelayed(task, 350)
    }

    private fun loadCategory(row: Row, open: Boolean) {
        categoryTask?.let { handler.removeCallbacks(it) }
        val payload = row.payload as? Pair<*, *> ?: return
        val type = payload.first as String
        val categoryId = payload.second as String
        if (row.key == categoryKey && listing != null) {
            if (open) enterFullscreen()
            return
        }
        categoryKey = row.key
        middleAdapter.select(row.key)
        // Behåll vald sortering och filter när man byter kategori.
        val params = listing?.takeIf { it.type == type }?.params ?: mutableMapOf()
        listing = if (categoryId == "new") {
            Listing("Nytt tillagt: ${row.title.lowercase()}", type, null, mutableMapOf("sort" to "added", "new_only" to "1"))
        } else {
            Listing(row.title, type, if (categoryId == "all") null else categoryId, params)
        }
        paneTitle.text = listing?.title ?: row.title
        updateToolbar()
        if (open) enterFullscreen(focusNow = false)
        loadListing(reset = true, focusFirst = open)
    }

    // ================================================================ listor

    private fun loadListing(reset: Boolean, focusFirst: Boolean = false) {
        val l = listing ?: return
        val token = if (reset) ++loadToken else loadToken
        val offset = if (reset) 0 else entries.size
        if (reset) showEntries(emptyList(), false, "Hämtar…")
        val query = StringBuilder("type=${l.type}&offset=$offset&limit=$PAGE")
        l.categoryId?.let { query.append("&category_id=").append(Api.enc(it)) }
        for ((k, v) in l.params) query.append("&").append(k).append("=").append(Api.enc(v))
        Api.obj("GET", "/device/browse?$query") { result ->
            if (token != loadToken) return@obj
            loadingMore = false
            result.onSuccess { data ->
                val items = Item.list(data.optJSONArray("items"))
                total = data.optInt("total")
                if (reset) {
                    showEntries(items, false, when {
                        l.params["new_only"] == "1" -> "Inget nytt ännu. Här visas det som dykt upp i listan sedan TV Hub började hålla koll, med datum. Listorna uppdateras var 12:e timme."
                        l.params.isEmpty() -> "Inget innehåll här."
                        else -> "Inget matchar filtret."
                    })
                    if (focusFirst) Focus.first(itemsList)
                } else {
                    entries = entries + items
                    itemsAdapter.append(items.map { renderRow(it) })
                }
                totalView.text = if (total > 0) "$total st" else ""
                updateToolbar()
            }.onFailure { handleError(it) }
        }
    }

    private fun isGrid(): Boolean =
        fullscreen && entries.isNotEmpty() && entries.all { it is Item && it.type != "live" }

    private fun renderRow(entry: Any): Row = when (entry) {
        is ContinueItem -> Row(
            title = entry.seriesName?.let { "$it · ${entry.name}" } ?: entry.name,
            subtitle = Format.remaining(entry.positionMs, entry.durationMs),
            icon = entry.icon,
            key = "continue:${entry.type}:${entry.id}",
            payload = entry,
            iconSpace = true,
            poster = false,
        )
        is Item -> itemRow(entry)
        is Rec -> recRow(entry)
        else -> Row(entry.toString())
    }

    private fun itemRow(item: Item): Row {
        val mixed = entries.any { it is Item && it.type != item.type } ||
            section == "search" || section == "favorites" || section == "hits"
        val grid = isGrid()
        val title = if (item.type == "live" && item.num != null && !mixed) "${item.num}   ${item.name}" else item.name
        val newLabel = if (section == "recent" && listing?.params?.get("new_only") == "1") item.added?.let { Format.addedLabel(it) } else null
        val subtitle = when {
            newLabel != null -> listOfNotNull(newLabel, if (item.type == "live") item.categoryName else item.facts.ifEmpty { null }).joinToString(" · ")
            item.type == "live" -> epgNow[item.id]?.ifEmpty { null } ?: if (mixed) item.typeLabel else item.categoryName
            grid -> listOfNotNull(if (item.watched) "✓ Sedd" else null, item.facts.ifEmpty { null }).joinToString(" · ")
            mixed -> listOfNotNull(item.typeLabel, item.facts.ifEmpty { null }).joinToString(" · ")
            else -> listOfNotNull(if (item.watched) "✓" else null, item.facts.ifEmpty { item.categoryName }).joinToString(" ")
        }
        return Row(
            title = title,
            subtitle = subtitle,
            icon = item.icon,
            key = "${item.type}:${item.id}",
            payload = item,
            iconSpace = true,
            star = item.favorite,
            poster = grid,
        )
    }

    private fun recRow(rec: Rec): Row {
        val state = when (rec.status) {
            "scheduled" -> "Planerad"
            "recording" -> if (rec.media) "⬇ Laddar ner · ${Rec.size(rec.localFile?.length() ?: rec.sizeBytes)}" else "● Spelar in"
            "done" -> if (rec.localFile != null) "Klar · ${Rec.size(rec.localFile?.length() ?: rec.sizeBytes)}" else "Klar, men filen finns inte på USB-minnet"
            "failed" -> "Misslyckades"
            else -> rec.status
        }
        return Row(
            title = "${rec.channel} · ${rec.title}",
            subtitle = "${rec.timeLabel}   $state",
            key = "rec:${rec.id}",
            payload = rec,
        )
    }

    private fun showEntries(list: List<Any>, keepFocus: Boolean, emptyText: String) {
        entries = list
        if (listing == null) total = list.size
        applyLayout()
        val rows = list.map { renderRow(it) }
        if (keepFocus) {
            Focus.submitKeeping(itemsList, itemsAdapter, rows)
        } else {
            itemsAdapter.submit(rows)
            itemsList.scrollToPosition(0)
        }
        if (rows.isEmpty()) showStatus(emptyText) else status.visibility = View.GONE
        if (listing == null) totalView.text = if (list.size > 0 && fullscreen) "${list.size} st" else ""
        requestEpgForVisible()
    }

    private fun applyLayout() {
        val grid = isGrid()
        val wanted = if (grid) GRID_COLUMNS else 1
        if (itemsAdapter.columns != wanted || (grid && itemsList.layoutManager !is GridLayoutManager)) {
            itemsList.layoutManager = if (grid) GridLayoutManager(this, GRID_COLUMNS) else LinearLayoutManager(this)
            itemsAdapter.columns = wanted
        }
    }

    private fun rerender() {
        val position = Focus.focusedPosition(itemsList)
        applyLayout()
        itemsAdapter.submit(entries.map { renderRow(it) })
        if (position != RecyclerView.NO_POSITION) Focus.position(itemsList, position)
    }

    private fun showStatus(text: String) {
        status.text = text
        status.visibility = View.VISIBLE
    }

    // ================================================================ helskärm

    private fun enterFullscreen(focusNow: Boolean = true) {
        if (!fullscreen) {
            fullscreen = true
            menuPane.visibility = View.GONE
            middlePane.visibility = View.GONE
            info.visibility = View.GONE
            rerender()
            if (listing == null) totalView.text = if (entries.isNotEmpty()) "${entries.size} st" else ""
        }
        if (focusNow) {
            if (entries.isNotEmpty()) Focus.first(itemsList) else if (toolbar.visibility == View.VISIBLE) sortButton.requestFocus()
        }
    }

    private fun exitFullscreen(restoreFocus: Boolean) {
        if (!fullscreen) return
        fullscreen = false
        menuPane.visibility = View.VISIBLE
        info.visibility = View.VISIBLE
        val inCategories = hasMiddle(section)
        middlePane.visibility = if (inCategories) View.VISIBLE else View.GONE
        rerender()
        if (restoreFocus) {
            if (inCategories) {
                Focus.position(middleList, middleAdapter.indexOf(categoryKey).coerceAtLeast(0))
            } else {
                Focus.position(menuList, menuAdapter.indexOf(section).coerceAtLeast(0))
            }
        }
    }

    /** Höger från listan öppnar helskärm. Vänster från första kolumnen i helskärm går ur. */
    private fun onItemKey(position: Int, keyCode: Int): Boolean {
        if (!fullscreen && keyCode == KeyEvent.KEYCODE_DPAD_RIGHT && entries.isNotEmpty()) {
            enterFullscreen(focusNow = false)
            Focus.position(itemsList, position)
            return true
        }
        if (fullscreen && keyCode == KeyEvent.KEYCODE_DPAD_LEFT && position % itemsAdapter.columns.coerceAtLeast(1) == 0) {
            exitFullscreen(restoreFocus = true)
            return true
        }
        return false
    }

    // ================================================================ innehåll

    private fun onItemClick(row: Row) {
        when (val entry = row.payload) {
            is Rec -> openRecording(entry)
            is ContinueItem -> {
                if (entry.type == "episode" && entry.seriesId != null) {
                    SeriesActivity.start(this, entry.seriesId, entry.seriesName ?: entry.name, resumeEpisodeId = entry.id)
                } else {
                    DetailActivity.start(this, entry.id, entry.name)
                }
            }
            is Item -> when (entry.type) {
                "series" -> SeriesActivity.start(this, entry.id, entry.name)
                "episode" -> playFavoriteEpisode(this, entry)
                "vod" -> DetailActivity.start(this, entry.id, entry.name)
                else -> {
                    val channels = entries.filterIsInstance<Item>().filter { it.type == "live" }
                    PlayerActivity.start(
                        this, type = "live", id = entry.id, name = entry.name, ext = null,
                        queueIds = ArrayList(channels.map { it.id }),
                        queueNames = ArrayList(channels.map { it.name }),
                        queueExts = null,
                        queueIndex = channels.indexOfFirst { it.id == entry.id },
                    )
                }
            }
        }
    }

    private fun openRecording(rec: Rec) {
        val file = rec.localFile
        when {
            rec.media && rec.status == "recording" ->
                Toast.makeText(this, "Laddas ner just nu. Den går att se när den är klar.", Toast.LENGTH_SHORT).show()
            file != null && (rec.status == "done" || rec.status == "recording") ->
                PlayerActivity.start(this, type = "file", id = file.absolutePath, name = rec.title, ext = null)
            rec.status == "scheduled" ->
                Toast.makeText(this, "Planerad: ${rec.timeLabel}. Boxen måste vara påslagen eller i standby.", Toast.LENGTH_LONG).show()
            rec.status == "recording" ->
                Toast.makeText(this, "Inspelningen har precis börjat. Försök igen om en stund.", Toast.LENGTH_SHORT).show()
            rec.status == "failed" -> AlertDialog.Builder(this)
                .setTitle("Inspelningen misslyckades")
                .setMessage(rec.error ?: "Okänt fel.")
                .setPositiveButton("OK", null)
                .show()
            else -> Toast.makeText(this, "Filen finns inte här. Den kan ligga på den andra boxens USB-minne.", Toast.LENGTH_LONG).show()
        }
    }

    private fun onRecordingLongClick(rec: Rec) {
        val options = ArrayList<Pair<String, () -> Unit>>()
        if (rec.localFile != null) options.add("Spela" to { openRecording(rec) })
        if (rec.status == "recording") options.add((if (rec.media) "Avbryt nedladdningen" else "Stoppa inspelningen (behåll det inspelade)") to {
            RecScheduler.send(this, RecScheduler.ACTION_STOP, rec.id)
            Toast.makeText(this, "Inspelningen stoppas.", Toast.LENGTH_SHORT).show()
            handler.postDelayed({ if (section == "recordings") showSection("recordings", keepFocus = true) }, 2_500)
        })
        options.add((if (rec.status == "scheduled") "Ta bort planeringen" else "Radera inspelningen") to {
            AlertDialog.Builder(this)
                .setTitle("Radera \"${rec.title}\"?")
                .setMessage(if (rec.status == "scheduled") "Programmet spelas inte in." else "Filen raderas från USB-minnet.")
                .setPositiveButton("Radera") { _, _ ->
                    RecScheduler.send(this, RecScheduler.ACTION_DELETE, rec.id)
                    handler.postDelayed({ loadHome() }, 1_500)
                }
                .setNegativeButton("Avbryt", null)
                .show()
        })
        AlertDialog.Builder(this)
            .setTitle(rec.title)
            .setItems(options.map { it.first }.toTypedArray()) { _, which -> options[which].second() }
            .show()
    }

    private fun onItemLongClick(row: Row, position: Int) {
        (row.payload as? Rec)?.let {
            onRecordingLongClick(it)
            return
        }
        val item = row.payload as? Item ?: return
        val favoriteLabel = if (item.favorite) "Ta bort från favoriter" else "Lägg till i favoriter"
        val options = ArrayList<Pair<String, () -> Unit>>()
        when (item.type) {
            "live" -> options.add("Programguide och tidigare program" to { GuideActivity.start(this, item.id, item.name) })
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
        if (!privateMode()) options.add(favoriteLabel to { toggleFavorite(item, position) })
        AlertDialog.Builder(this)
            .setTitle(item.name)
            .setItems(options.map { it.first }.toTypedArray()) { _, which -> options[which].second() }
            .show()
    }

    private fun toggleFavorite(item: Item, position: Int) {
        val on = !item.favorite
        val body = JSONObject().put("type", item.type).put("id", item.id).put("on", on)
        Api.obj("POST", "/device/favorites", body) { result ->
            result.onSuccess {
                item.favorite = on
                itemsAdapter.replace(position, renderRow(item))
                Toast.makeText(this, if (on) "★ ${item.name} lagd till i favoriter" else "${item.name} borttagen ur favoriter",
                    Toast.LENGTH_SHORT).show()
                loadHome()
            }.onFailure { Toast.makeText(this, Api.friendly(it), Toast.LENGTH_LONG).show() }
        }
    }

    private fun onItemFocus(row: Row, position: Int) {
        // Hämta nästa sida i tid innan man når slutet.
        if (listing != null && !loadingMore && entries.size < total && position >= entries.size - 30) {
            loadingMore = true
            loadListing(reset = false)
        }
        if (fullscreen) return
        val entry = row.payload
        epgTask?.let { handler.removeCallbacks(it) }
        val token = ++epgToken
        if (entry is ContinueItem) {
            info.text = "${Format.remaining(entry.positionMs, entry.durationMs)}\nOK: fortsätt"
            return
        }
        if (entry is Rec) {
            info.text = (entry.error?.let { "$it\n" } ?: "") + "OK: spela   Håll in OK: stoppa eller radera"
            return
        }
        val item = entry as? Item ?: return
        if (item.type != "live") {
            info.text = listOfNotNull(item.facts.ifEmpty { null }, item.genre).joinToString(" · ") +
                "\nOK: öppna   Håll in OK: fler val   Höger: helskärm"
            return
        }
        info.text = "OK: titta   Håll in OK: programguide, favorit   Höger: helskärm"
        val task = Runnable {
            Api.arr("/device/epg/${Api.enc(item.id)}") { result ->
                if (token != epgToken) return@arr
                result.onSuccess { array -> if (array.length() > 0) info.text = epgText(array) }
            }
        }
        epgTask = task
        handler.postDelayed(task, 450)
    }

    private fun epgText(array: JSONArray): String {
        val now = System.currentTimeMillis() / 1000
        val lines = ArrayList<String>()
        for (i in 0 until array.length()) {
            if (lines.size == 2) break
            val p = array.optJSONObject(i) ?: continue
            val start = p.optLong("start")
            val end = p.optLong("end")
            if (end in 1 until now) continue
            val label = if (start <= now) "Nu" else "Sedan"
            lines.add("$label  ${timeFormat.format(Date(start * 1000))}–${timeFormat.format(Date(end * 1000))}  ${p.optString("title")}")
        }
        return lines.joinToString("\n")
    }

    /** Hämtar "Nu: …" för de kanaler som syns i listan och skriver in det i raderna. */
    private fun requestEpgForVisible() {
        val manager = itemsList.layoutManager as? LinearLayoutManager ?: return
        val first = manager.findFirstVisibleItemPosition().coerceAtLeast(0)
        val last = manager.findLastVisibleItemPosition().let { if (it < 0) first + 15 else it + 5 }
        val ids = ArrayList<String>()
        for (i in first..last.coerceAtMost(entries.size - 1)) {
            val item = entries.getOrNull(i) as? Item ?: continue
            if (item.type == "live" && !epgNow.containsKey(item.id)) ids.add(item.id)
        }
        if (ids.isEmpty()) return
        ids.take(40).forEach { epgNow[it] = epgNow[it] ?: "" }
        Api.obj("GET", "/device/epg-now?ids=${Api.enc(ids.take(40).joinToString(","))}") { result ->
            result.onSuccess { data ->
                for (id in ids.take(40)) {
                    val now = data.optJSONObject(id)?.optJSONObject("now") ?: continue
                    val start = timeFormat.format(Date(now.optLong("start") * 1000))
                    val end = timeFormat.format(Date(now.optLong("end") * 1000))
                    epgNow[id] = "Nu: ${now.optString("title")}  $start–$end"
                    val index = itemsAdapter.indexOf("live:$id")
                    val item = entries.getOrNull(index) as? Item
                    if (index >= 0 && item != null) itemsAdapter.replace(index, renderRow(item))
                }
            }
        }
    }

    // ================================================================ sortering och filter

    private fun sortOptions(type: String): List<Pair<String, String>> = when (type) {
        "live" -> listOf("num" to "Kanalnummer", "name" to "Namn")
        "series" -> listOf("added" to "Senast tillagda", "rating" to "Betyg", "year" to "Årtal", "name" to "Namn")
        else -> listOf(
            "added" to "Senast tillagda", "rating" to "Betyg", "year" to "Årtal",
            "duration" to "Längd, kortast först", "duration_desc" to "Längd, längst först", "name" to "Namn",
        )
    }

    private fun updateToolbar() {
        val l = listing
        if (l == null) {
            toolbar.visibility = View.GONE
            return
        }
        toolbar.visibility = View.VISIBLE
        val options = sortOptions(l.type)
        val sortKey = l.params["sort"] ?: options.first().first
        sortButton.text = "Sortera: ${options.firstOrNull { it.first == sortKey }?.second ?: options.first().second}"
        filterButton.visibility = if (l.type == "live") View.GONE else View.VISIBLE
        filterButton.text = "Filter: ${filterSummary(l.params).ifEmpty { "inga" }}"
    }

    private fun filterSummary(params: Map<String, String>): String {
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

    private fun showSortDialog() {
        val l = listing ?: return
        val options = sortOptions(l.type)
        val current = options.indexOfFirst { it.first == (l.params["sort"] ?: options.first().first) }.coerceAtLeast(0)
        AlertDialog.Builder(this)
            .setTitle("Sortera")
            .setSingleChoiceItems(options.map { it.second }.toTypedArray(), current) { dialog, which ->
                l.params["sort"] = options[which].first
                dialog.dismiss()
                loadListing(reset = true)
            }
            .show()
    }

    private fun showFilterDialog() {
        val l = listing ?: return
        val p = l.params
        val ratingText = p["min_rating"]?.let { "★ $it eller högre" } ?: "Alla"
        val durationText = when {
            p["max_duration"] != null -> "Under ${Format.minutes(p["max_duration"]!!.toInt())}"
            p["min_duration"] != null -> "Över ${Format.minutes(p["min_duration"]!!.toInt())}"
            else -> "Alla"
        }
        val yearText = when {
            p["min_year"] != null && p["max_year"] != null -> "${p["min_year"]}–${p["max_year"]}"
            p["min_year"] != null -> "Från ${p["min_year"]}"
            p["max_year"] != null -> "Före ${p["max_year"]!!.toInt() + 1}"
            else -> "Alla"
        }
        val options = ArrayList<Pair<String, () -> Unit>>()
        options.add("Betyg: $ratingText" to { chooseRating(l) })
        if (l.type == "vod") options.add("Längd: $durationText" to { chooseDuration(l) })
        options.add("Årtal: $yearText" to { chooseYear(l) })
        options.add("Genre: ${p["genre"] ?: "Alla"}" to { chooseGenre(l) })
        options.add("Spara som rad i menyn…" to { saveFilter(l) })
        if (filterSummary(p).isNotEmpty()) {
            options.add("Rensa alla filter" to {
                listOf("min_rating", "min_duration", "max_duration", "min_year", "max_year", "genre").forEach { p.remove(it) }
                loadListing(reset = true)
            })
        }
        AlertDialog.Builder(this)
            .setTitle("Filter")
            .setItems(options.map { it.first }.toTypedArray()) { _, which -> options[which].second() }
            .show()
    }

    /** Visar ett val och öppnar filterlistan igen, så att flera villkor kan sättas i följd. */
    private fun choose(title: String, labels: List<String>, current: Int, apply: (Int) -> Unit) {
        AlertDialog.Builder(this)
            .setTitle(title)
            .setSingleChoiceItems(labels.toTypedArray(), current) { dialog, which ->
                apply(which)
                dialog.dismiss()
                loadListing(reset = true)
                handler.postDelayed({ showFilterDialog() }, 250)
            }
            .show()
    }

    private fun chooseRating(l: Listing) {
        val values = listOf(null, "5", "6", "7", "8")
        val labels = listOf("Alla", "★ 5 eller högre", "★ 6 eller högre", "★ 7 eller högre", "★ 8 eller högre")
        choose("Betyg", labels, values.indexOf(l.params["min_rating"]).coerceAtLeast(0)) { which ->
            values[which]?.let { l.params["min_rating"] = it } ?: l.params.remove("min_rating")
        }
    }

    private fun chooseDuration(l: Listing) {
        val values = listOf(null to null, null to "90", null to "120", "120" to null)
        val labels = listOf("Alla", "Under 1 h 30 min", "Under 2 h", "Över 2 h")
        val current = values.indexOf(l.params["min_duration"] to l.params["max_duration"]).coerceAtLeast(0)
        choose("Längd", labels, current) { which ->
            l.params.remove("min_duration")
            l.params.remove("max_duration")
            values[which].first?.let { l.params["min_duration"] = it }
            values[which].second?.let { l.params["max_duration"] = it }
        }
    }

    private fun chooseYear(l: Listing) {
        val values = listOf(null to null, "2020" to null, "2010" to null, "2000" to null, null to "1999")
        val labels = listOf("Alla", "Från 2020", "Från 2010", "Från 2000", "Före 2000")
        val current = values.indexOf(l.params["min_year"] to l.params["max_year"]).coerceAtLeast(0)
        choose("Årtal", labels, current) { which ->
            l.params.remove("min_year")
            l.params.remove("max_year")
            values[which].first?.let { l.params["min_year"] = it }
            values[which].second?.let { l.params["max_year"] = it }
        }
    }

    private fun chooseGenre(l: Listing) {
        val show = { list: List<String> ->
            val labels = listOf("Alla") + list
            val current = (list.indexOf(l.params["genre"]) + 1).coerceAtLeast(0)
            choose("Genre", labels, current) { which ->
                if (which == 0) l.params.remove("genre") else l.params["genre"] = list[which - 1]
            }
        }
        Api.arr("/device/genres?type=${l.type}") { result ->
            result.onSuccess { array ->
                val list = ArrayList<String>()
                for (i in 0 until array.length()) array.optJSONObject(i)?.optString("name")?.let { list.add(it) }
                genres = list
                if (list.isEmpty()) {
                    Toast.makeText(this, "Genrer hämtas fortfarande i bakgrunden. Prova senare.", Toast.LENGTH_LONG).show()
                } else {
                    show(list)
                }
            }.onFailure { Toast.makeText(this, Api.friendly(it), Toast.LENGTH_LONG).show() }
        }
    }

    private fun saveFilter(l: Listing) {
        val input = EditText(this)
        input.setText(listOf(l.title, filterSummary(l.params)).filter { it.isNotEmpty() }.joinToString(" · ").take(40))
        AlertDialog.Builder(this)
            .setTitle("Namn på raden i menyn")
            .setView(input)
            .setPositiveButton("Spara") { _, _ ->
                val params = JSONObject()
                for ((k, v) in l.params) params.put(k, v)
                l.categoryId?.let { params.put("category_id", it) }
                val body = JSONObject().put("name", input.text.toString().trim()).put("type", l.type).put("params", params)
                Api.obj("POST", "/device/filters", body) { result ->
                    result.onSuccess {
                        Toast.makeText(this, "Sparat som rad i menyn", Toast.LENGTH_SHORT).show()
                        loadHome()
                    }.onFailure { Toast.makeText(this, Api.friendly(it), Toast.LENGTH_LONG).show() }
                }
            }
            .setNegativeButton("Avbryt", null)
            .show()
    }

    // ================================================================ sök

    private fun runSearch() {
        searchTask?.let { handler.removeCallbacks(it) }
        if (section != "search") return
        val query = searchBox.text.toString().trim()
        if (query.length < 2) {
            lastSearch = emptyList()
            showEntries(emptyList(), false, "Skriv minst två tecken.")
            return
        }
        val token = ++loadToken
        Api.arr("/device/search?q=${Api.enc(query)}&limit=150") { result ->
            if (token != loadToken || section != "search") return@arr
            result.onSuccess { array ->
                lastSearch = Item.list(array)
                showEntries(lastSearch, false, "Inga träffar på \"$query\".")
            }.onFailure { handleError(it) }
        }
    }

    // ================================================================ inställningar

    /** Inställningen "Starta på senaste kanalen": spelaren öppnas ovanpå menyn när appen startar. */
    private fun startLastChannel() {
        if (Session.lastChannelStarted) return
        Session.lastChannelStarted = true
        if (!prefs.startOnLastChannel || prefs.adultMode) return
        val (id, name, icon) = prefs.lastChannel ?: return
        var ids: ArrayList<String>? = null
        var names: ArrayList<String>? = null
        try {
            val q = org.json.JSONObject(prefs.lastChannelQueue)
            val a = q.optJSONArray("ids")
            val b = q.optJSONArray("names")
            if (a != null && b != null && a.length() == b.length()) {
                ids = ArrayList((0 until a.length()).map { a.optString(it) })
                names = ArrayList((0 until b.length()).map { b.optString(it) })
            }
        } catch (_: Exception) {
        }
        PlayerActivity.start(this, type = "live", id = id, name = name, ext = null, queueIds = ids, queueNames = names,
            queueIndex = ids?.indexOf(id) ?: -1, icon = icon)
    }

    private fun showSettings() {
        val options = arrayOf(
            "Utseende: Klassiskt (byt till Modernt)",
            "Testa hastigheten i hemnätet",
            "Var inspelningar sparas",
            "Sök efter uppdatering av appen",
            "Hämta listor igen",
            "Para om den här boxen",
            "Om TV Hub",
            "Fjärrkontrollens knappar",
            "Röststyrning när appen är stängd",
            "Mobilläge (för telefon)",
            "Nytt per dag",
            "Starta på senaste kanalen: " + if (prefs.startOnLastChannel) "På" else "Av",
            "Ruta för nästa avsnitt: " + if (prefs.showNextCard) "På" else "Av",
            "Kom ihåg senaste fliken i Filmer och Serier: " + if (prefs.rememberTab) "På" else "Av",
        )
        AlertDialog.Builder(this)
            .setTitle("Inställningar")
            .setItems(options) { _, which ->
                when (which) {
                    0 -> {
                        prefs.modern = true
                        startActivity(Intent(this, HomeActivity::class.java))
                        finish()
                    }
                    1 -> runSpeedTest()
                    2 -> chooseRecordingStorage()
                    3 -> Updater.check(this, prefs, quiet = false)
                    4 -> {
                        home = null
                        section = null
                        loadHome()
                    }
                    5 -> confirmRepair()
                    6 -> showAbout()
                    7 -> Keys.showSettings(this)
                    8 -> Control.showSettings(this)
                    10 -> NewDays.show(this)
                    11 -> {
                        prefs.startOnLastChannel = !prefs.startOnLastChannel
                        Toast.makeText(this, if (prefs.startOnLastChannel) "Appen startar på senaste kanalen" else "Appen startar på Hem",
                            Toast.LENGTH_SHORT).show()
                    }
                    12 -> {
                        prefs.showNextCard = !prefs.showNextCard
                        Toast.makeText(this, if (prefs.showNextCard) "Rutan för nästa avsnitt visas" else "Rutan för nästa avsnitt visas inte (nästa spelas ändå automatiskt)",
                            Toast.LENGTH_LONG).show()
                    }
                    13 -> {
                        prefs.rememberTab = !prefs.rememberTab
                        Toast.makeText(this, if (prefs.rememberTab) "Filmer och Serier öppnar där du senast var" else "Filmer och Serier öppnar på startkategorin",
                            Toast.LENGTH_LONG).show()
                    }
                    9 -> {
                        prefs.layoutMode = "mobile"
                        startActivity(Intent(this, MobileActivity::class.java))
                        finish()
                    }
                }
            }
            .show()
    }

    private fun chooseRecordingStorage() {
        val volumes = RecStorage.volumes(this)
        val current = RecStorage.chosen(this, prefs)
        if (volumes.isEmpty()) {
            Toast.makeText(this, "Ingen lagring hittades.", Toast.LENGTH_LONG).show()
            return
        }
        val labels = volumes.map { it.label }.toTypedArray()
        val selected = volumes.indexOfFirst { it.dir.absolutePath == current?.dir?.absolutePath }
        val note = if (volumes.none { it.removable }) "\n\nInget USB-minne hittades. Sätt i ett och öppna det här igen." else ""
        AlertDialog.Builder(this)
            .setTitle("Var inspelningar sparas")
            .setSingleChoiceItems(labels, selected) { dialog, which ->
                prefs.recordDir = volumes[which].dir.absolutePath
                dialog.dismiss()
                Toast.makeText(this, "Inspelningar sparas på: ${volumes[which].label}", Toast.LENGTH_LONG).show()
            }
            .setNegativeButton("Stäng", null)
            .show()
        if (note.isNotEmpty()) Toast.makeText(this, note.trim(), Toast.LENGTH_LONG).show()
    }

    private fun runSpeedTest() {
        Toast.makeText(this, "Mäter hastigheten mot Home Assistant…", Toast.LENGTH_SHORT).show()
        Api.async({ SpeedTest.runBlocking(prefs, 20) }) { result ->
            result.onSuccess { mbps ->
                Api.report("speedtest", null, JSONObject().put("mbps", mbps).put("trigger", "manual"))
                val verdict = if (mbps >= 30) {
                    "Det räcker gott för Toucans krav på 30 Mbit/s. Hackar det ändå ligger felet utanför hemnätet; se Diagnostik i webbadminen."
                } else {
                    "Det är under Toucans krav på 30 Mbit/s. Wi-Fi eller kabeln till boxen är troligen flaskhalsen. Prova nätverkskabel."
                }
                AlertDialog.Builder(this)
                    .setTitle("$mbps Mbit/s i hemnätet")
                    .setMessage("Mätt mellan boxen och Home Assistant.\n\n$verdict")
                    .setPositiveButton("OK", null)
                    .show()
            }.onFailure { Toast.makeText(this, Api.friendly(it), Toast.LENGTH_LONG).show() }
        }
    }

    private fun confirmRepair() {
        AlertDialog.Builder(this)
            .setTitle("Para om boxen?")
            .setMessage("Du behöver en ny kod från webbadminen.")
            .setPositiveButton("Para om") { _, _ ->
                prefs.clearPairing()
                openSetup()
            }
            .setNegativeButton("Avbryt", null)
            .show()
    }

    private fun showAbout() {
        AlertDialog.Builder(this)
            .setTitle("TV Hub ${BuildConfig.VERSION_NAME}")
            .setMessage(
                "Server: ${prefs.server}\nBox: ${prefs.deviceName}\n\n" +
                    "OK öppnar. Höger öppnar i helskärm, Tillbaka går ur. Håll in OK för fler val. " +
                    "Under uppspelning: upp/ner byter kanal, vänster går till förra kanalen, OK två gånger visar ljud, undertexter och inspelning. " +
                    "Play/paus pausar live. Spola med ◀◀ ▶▶ (eller vänster/höger medan du är pausad eller bakåt i tiden)."
            )
            .setPositiveButton("OK", null)
            .show()
    }

    private fun openSetup() {
        startActivity(Intent(this, SetupActivity::class.java))
        finish()
    }
}
