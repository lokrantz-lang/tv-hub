package se.tvhub.box

import android.app.Activity
import android.app.AlertDialog
import android.content.Intent
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.text.Editable
import android.text.TextWatcher
import android.view.View
import android.view.inputmethod.EditorInfo
import android.widget.EditText
import android.widget.TextView
import android.widget.Toast
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import org.json.JSONArray
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * Huvudskärm med tre kolumner:
 *   meny (Sök, Favoriter, egna grupper, Kanaler, Filmer, Serier …)
 *   kategorier (bara för Kanaler/Filmer/Serier)
 *   innehåll
 * Fokus väljer, OK går vidare åt höger, håll in OK växlar favorit, Tillbaka går åt vänster.
 */
class MainActivity : Activity() {
    private lateinit var prefs: Prefs
    private lateinit var menuList: RecyclerView
    private lateinit var middlePane: View
    private lateinit var middleTitle: TextView
    private lateinit var middleList: RecyclerView
    private lateinit var itemsList: RecyclerView
    private lateinit var paneTitle: TextView
    private lateinit var searchBox: EditText
    private lateinit var status: TextView
    private lateinit var info: TextView
    private lateinit var menuAdapter: RowAdapter
    private lateinit var middleAdapter: RowAdapter
    private lateinit var itemsAdapter: RowAdapter

    private val handler = Handler(Looper.getMainLooper())
    private var home: JSONObject? = null
    private var section: String? = null
    private var categoryKey: String? = null
    private var currentItems: List<Item> = emptyList()
    private var lastSearch: List<Item> = emptyList()
    private var loadToken = 0
    private var epgToken = 0
    private var menuTask: Runnable? = null
    private var categoryTask: Runnable? = null
    private var epgTask: Runnable? = null
    private var searchTask: Runnable? = null
    private var retryTask: Runnable? = null
    private val timeFormat = SimpleDateFormat("HH:mm", Locale("sv", "SE"))

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        prefs = Prefs(this)
        Api.init(prefs)
        if (!prefs.paired) {
            openSetup()
            return
        }
        setContentView(R.layout.activity_main)
        menuList = findViewById(R.id.menu)
        middlePane = findViewById(R.id.middle_pane)
        middleTitle = findViewById(R.id.middle_title)
        middleList = findViewById(R.id.middle)
        itemsList = findViewById(R.id.items)
        paneTitle = findViewById(R.id.pane_title)
        searchBox = findViewById(R.id.search)
        status = findViewById(R.id.status)
        info = findViewById(R.id.info)

        menuAdapter = RowAdapter(
            onClick = { row, _ -> onMenuClick(row) },
            onFocus = { row, _ -> onMenuFocus(row) },
        )
        middleAdapter = RowAdapter(
            onClick = { row, _ -> loadCategory(row, focusItems = true) },
            onFocus = { row, _ -> onCategoryFocus(row) },
        )
        itemsAdapter = RowAdapter(
            onClick = { row, position -> onItemClick(row, position) },
            onFocus = { row, _ -> onItemFocus(row) },
            onLongClick = { row, position -> toggleFavorite(row, position) },
        )
        for ((list, adapter) in listOf(menuList to menuAdapter, middleList to middleAdapter, itemsList to itemsAdapter)) {
            list.layoutManager = LinearLayoutManager(this)
            list.adapter = adapter
            list.itemAnimator = null
        }

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
        loadHome()
    }

    override fun onDestroy() {
        handler.removeCallbacksAndMessages(null)
        super.onDestroy()
    }

    @Deprecated("Deprecated in Java")
    override fun onBackPressed() {
        if (!::menuList.isInitialized) {
            super.onBackPressed()
            return
        }
        val focused = currentFocus
        if (Focus.contains(itemsList, focused) && middlePane.visibility == View.VISIBLE) {
            Focus.position(middleList, middleAdapter.indexOf(categoryKey).coerceAtLeast(0))
            return
        }
        if (Focus.contains(itemsList, focused) || Focus.contains(middleList, focused) || focused == searchBox) {
            Focus.position(menuList, menuAdapter.indexOf(section).coerceAtLeast(0))
            return
        }
        super.onBackPressed()
    }

    // ---------------------------------------------------------------- data

    private fun loadHome() {
        Api.obj("GET", "/device/home") { result ->
            result.onSuccess { data ->
                val first = home == null
                home = data
                retryTask?.let { handler.removeCallbacks(it) }
                buildMenu()
                val current = section
                if (first || current == null) {
                    val favorites = data.optJSONArray("favorites")?.length() ?: 0
                    val start = if (favorites > 0) "favorites" else "cat:live"
                    showSection(start)
                    Focus.position(menuList, menuAdapter.indexOf(start))
                } else if (current == "favorites" || current == "recent" || current.startsWith("group:")) {
                    showSection(current, keepFocus = true)
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
        rows.add(Row("Sök", key = "search"))
        rows.add(Row("Favoriter", subtitle = countLabel(data.optJSONArray("favorites")?.length()), key = "favorites"))
        val groups = data.optJSONArray("groups") ?: JSONArray()
        for (i in 0 until groups.length()) {
            val g = groups.optJSONObject(i) ?: continue
            rows.add(Row(g.optString("name"), subtitle = countLabel(g.optInt("count")), key = "group:${g.optInt("id")}"))
        }
        val counts = data.optJSONObject("counts")
        rows.add(Row("Kanaler", subtitle = countLabel(counts?.optJSONObject("live")?.optInt("items")), key = "cat:live"))
        rows.add(Row("Filmer", subtitle = countLabel(counts?.optJSONObject("vod")?.optInt("items")), key = "cat:vod"))
        rows.add(Row("Serier", subtitle = countLabel(counts?.optJSONObject("series")?.optInt("items")), key = "cat:series"))
        rows.add(Row("Nya filmer", key = "recent"))
        rows.add(Row("Inställningar", key = "settings"))
        Focus.submitKeeping(menuList, menuAdapter, rows)
        menuAdapter.select(section)
    }

    // ---------------------------------------------------------------- meny

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
            row.key.startsWith("cat:") -> Focus.first(middleList)
            else -> Focus.first(itemsList)
        }
    }

    private fun showSection(key: String, keepFocus: Boolean = false) {
        val data = home ?: return
        section = key
        menuAdapter.select(key)
        info.text = ""
        val isCategoryView = key.startsWith("cat:")
        middlePane.visibility = if (isCategoryView) View.VISIBLE else View.GONE
        searchBox.visibility = if (key == "search") View.VISIBLE else View.GONE
        loadToken++

        when {
            key == "search" -> {
                paneTitle.text = "Sök"
                showItems(lastSearch, keepFocus, "Skriv minst två tecken. Sökningen gäller kanaler, filmer och serier.")
            }
            key == "favorites" -> {
                paneTitle.text = "Favoriter"
                showItems(
                    Item.list(data.optJSONArray("favorites")), keepFocus,
                    "Inga favoriter ännu. Håll in OK på en kanal eller film, eller markera i webbadminen.",
                )
            }
            key == "recent" -> {
                paneTitle.text = "Nya filmer"
                showItems(Item.list(data.optJSONArray("recent_vod")), keepFocus, "Inga filmer.")
            }
            key.startsWith("group:") -> {
                val id = key.removePrefix("group:").toIntOrNull()
                val groups = data.optJSONArray("groups") ?: JSONArray()
                var group: JSONObject? = null
                for (i in 0 until groups.length()) {
                    if (groups.optJSONObject(i)?.optInt("id") == id) group = groups.optJSONObject(i)
                }
                paneTitle.text = group?.optString("name") ?: "Grupp"
                showItems(Item.list(group?.optJSONArray("items")), keepFocus, "Gruppen är tom. Lägg till innehåll i webbadminen.")
            }
            isCategoryView -> {
                val type = key.removePrefix("cat:")
                middleTitle.text = when (type) {
                    "live" -> "Kanaler"
                    "vod" -> "Filmer"
                    else -> "Serier"
                }
                paneTitle.text = ""
                categoryKey = null
                val categories = data.optJSONObject("categories")?.optJSONArray(type) ?: JSONArray()
                val rows = ArrayList<Row>()
                for (i in 0 until categories.length()) {
                    val c = categories.optJSONObject(i) ?: continue
                    if (c.optInt("count") == 0) continue
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
                showItems(emptyList(), false, if (rows.isEmpty()) "Inga kategorier. Uppdatera listorna i webbadminen." else "Välj en kategori.")
            }
        }
    }

    // ---------------------------------------------------------------- kategorier

    private fun onCategoryFocus(row: Row) {
        categoryTask?.let { handler.removeCallbacks(it) }
        if (row.key == categoryKey) return
        val task = Runnable { loadCategory(row, focusItems = false) }
        categoryTask = task
        handler.postDelayed(task, 350)
    }

    private fun loadCategory(row: Row, focusItems: Boolean) {
        categoryTask?.let { handler.removeCallbacks(it) }
        val payload = row.payload as? Pair<*, *> ?: return
        val type = payload.first as String
        val categoryId = payload.second as String
        if (row.key == categoryKey) {
            if (focusItems) Focus.first(itemsList)
            return
        }
        categoryKey = row.key
        middleAdapter.select(row.key)
        paneTitle.text = row.title
        val token = ++loadToken
        showItems(emptyList(), false, "Hämtar…")
        Api.arr("/device/items?type=$type&category_id=${Api.enc(categoryId)}&limit=2000") { result ->
            if (token != loadToken) return@arr
            result.onSuccess { array ->
                showItems(Item.list(array), false, "Kategorin är tom.")
                if (focusItems) Focus.first(itemsList)
            }.onFailure { handleError(it) }
        }
    }

    // ---------------------------------------------------------------- innehåll

    private fun itemRow(item: Item, mixed: Boolean): Row {
        val title = if (item.type == "live" && item.num != null && !mixed) "${item.num}   ${item.name}" else item.name
        val subtitle = when {
            mixed -> listOfNotNull(item.typeLabel, item.categoryName).joinToString(" · ")
            else -> item.categoryName
        }
        return Row(
            title = title,
            subtitle = subtitle,
            icon = item.icon,
            key = "${item.type}:${item.id}",
            payload = item,
            iconSpace = true,
            star = item.favorite,
        )
    }

    private fun showItems(items: List<Item>, keepFocus: Boolean, emptyText: String) {
        currentItems = items
        val mixed = items.map { it.type }.distinct().size > 1 || section == "search" || section == "favorites"
        val rows = items.map { itemRow(it, mixed) }
        if (keepFocus) {
            Focus.submitKeeping(itemsList, itemsAdapter, rows)
        } else {
            itemsAdapter.submit(rows)
            itemsList.scrollToPosition(0)
        }
        if (rows.isEmpty()) showStatus(emptyText) else status.visibility = View.GONE
    }

    private fun showStatus(text: String) {
        status.text = text
        status.visibility = View.VISIBLE
    }

    private fun onItemClick(row: Row, position: Int) {
        val item = row.payload as? Item ?: return
        when (item.type) {
            "series" -> SeriesActivity.start(this, item.id, item.name)
            "live" -> {
                val channels = currentItems.filter { it.type == "live" }
                PlayerActivity.start(
                    this,
                    type = "live",
                    id = item.id,
                    name = item.name,
                    ext = null,
                    queueIds = ArrayList(channels.map { it.id }),
                    queueNames = ArrayList(channels.map { it.name }),
                    queueExts = null,
                    queueIndex = channels.indexOfFirst { it.id == item.id },
                )
            }
            else -> PlayerActivity.start(this, type = item.type, id = item.id, name = item.name, ext = item.ext)
        }
    }

    private fun toggleFavorite(row: Row, position: Int) {
        val item = row.payload as? Item ?: return
        val on = !item.favorite
        val body = JSONObject().put("type", item.type).put("id", item.id).put("on", on)
        Api.obj("POST", "/device/favorites", body) { result ->
            result.onSuccess {
                item.favorite = on
                val mixed = section == "search" || section == "favorites"
                itemsAdapter.replace(position, itemRow(item, mixed))
                Toast.makeText(this, if (on) "★ ${item.name} lagd till i favoriter" else "${item.name} borttagen ur favoriter", Toast.LENGTH_SHORT).show()
                loadHome()
            }.onFailure { Toast.makeText(this, Api.friendly(it), Toast.LENGTH_LONG).show() }
        }
    }

    private fun onItemFocus(row: Row) {
        val item = row.payload as? Item ?: return
        epgTask?.let { handler.removeCallbacks(it) }
        val token = ++epgToken
        if (item.type != "live") {
            info.text = listOfNotNull(item.typeLabel, item.categoryName).joinToString(" · ") +
                "\nOK: spela   Håll in OK: favorit"
            return
        }
        info.text = "OK: titta   Håll in OK: favorit"
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

    // ---------------------------------------------------------------- sök

    private fun runSearch() {
        searchTask?.let { handler.removeCallbacks(it) }
        if (section != "search") return
        val query = searchBox.text.toString().trim()
        if (query.length < 2) {
            lastSearch = emptyList()
            showItems(emptyList(), false, "Skriv minst två tecken.")
            return
        }
        val token = ++loadToken
        Api.arr("/device/search?q=${Api.enc(query)}&limit=150") { result ->
            if (token != loadToken || section != "search") return@arr
            result.onSuccess { array ->
                lastSearch = Item.list(array)
                showItems(lastSearch, false, "Inga träffar på \"$query\".")
            }.onFailure { handleError(it) }
        }
    }

    // ---------------------------------------------------------------- inställningar

    private fun showSettings() {
        val options = arrayOf(
            "Testa hastigheten i hemnätet",
            "Hämta listor igen",
            "Para om den här boxen",
            "Om TV Hub",
        )
        AlertDialog.Builder(this)
            .setTitle("Inställningar")
            .setItems(options) { _, which ->
                when (which) {
                    0 -> runSpeedTest()
                    1 -> {
                        home = null
                        section = null
                        loadHome()
                    }
                    2 -> confirmRepair()
                    3 -> showAbout()
                }
            }
            .show()
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
            .setMessage("Server: ${prefs.server}\nBox: ${prefs.deviceName}\n\nOK väljer, håll in OK för favorit, Tillbaka går ett steg tillbaka. Under uppspelning byter upp/ner kanal.")
            .setPositiveButton("OK", null)
            .show()
    }

    private fun openSetup() {
        startActivity(Intent(this, SetupActivity::class.java))
        finish()
    }
}
