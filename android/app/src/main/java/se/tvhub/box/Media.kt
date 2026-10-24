package se.tvhub.box

import android.Manifest
import android.app.Activity
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import android.os.storage.StorageManager
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

/**
 * Mina media: filmer, hemvideo och bilder från NAS:en (DLNA, t.ex. Synologys Media Server) och från
 * en USB-disk i boxen. Bara TV-boxarna. Inget härifrån hamnar i Fortsätt titta på Hem.
 *
 * Dolda mappar (håll in OK → Dölj) och mappar med XXX, 18+, Adult eller Porn i namnet visas bara i
 * vuxenläget.
 */
object Media {
    /** En mapp, film eller bild. ref = sökväg (USB) eller DLNA-objektets id. */
    data class Entry(
        val key: String,
        val name: String,
        val folder: Boolean,
        val kind: String,          // "folder", "video", "image"
        val ref: String,
        val url: String?,          // spelbar adress: sökväg eller http
        val viewUrl: String?,      // bilder: storlek för bildspelet
        val thumb: String?,        // http, sökväg, eller "frame:<sökväg>" (bild ur videon)
        val subtitle: String?,
        val durationMs: Long?,
        val date: Long?,           // sekunder
        val hidden: Boolean = false,
        /** "Alla videor": mappen filen ligger i. */
        val parent: String? = null,
        /** Filstorlek i byte (NAS: ur serverns svar, USB: filens storlek). */
        val sizeBytes: Long? = null,
    )

    interface Source {
        val id: String
        val label: String
        val rootRef: String
        /** Blockerar: anropa i bakgrunden. */
        fun list(ref: String): List<Entry>
    }

    private val VIDEO = MediaNames.VIDEO
    private val IMAGE = MediaNames.IMAGE
    private val SUB = listOf("srt", "sv.srt", "swe.srt", "en.srt")
    private val SKIP = setOf("android", "lost.dir", "system volume information", "\$recycle.bin", "@eadir", "#recycle", ".privat")
    private val ADULT = Regex("(?i)(xxx|18\\+|adult|porn)")

    fun isAdultName(name: String) = ADULT.containsMatchIn(name)

    fun cleanTitle(raw: String): String = MediaNames.cleanTitle(raw)

    val naturalOrder: Comparator<String> get() = MediaNames.naturalOrder

    // ---------------------------------------------------------------- dolt och sett

    private fun prefs(context: Context) = context.getSharedPreferences("media", Context.MODE_PRIVATE)

    fun hiddenKeys(context: Context): Set<String> = prefs(context).getStringSet("hidden", emptySet()) ?: emptySet()

    fun setHidden(context: Context, key: String, hidden: Boolean) {
        val set = HashSet(hiddenKeys(context))
        if (hidden) set.add(key) else set.remove(key)
        prefs(context).edit().putStringSet("hidden", set).apply()
    }

    /** Får mappen visas i läget boxen är i? */
    fun visible(context: Context, e: Entry): Boolean = Prefs(context).adultMode || !e.hidden

    fun isSeen(context: Context, url: String): Boolean = prefs(context).getBoolean("seen:$url", false)

    fun setSeen(context: Context, url: String, seen: Boolean) {
        prefs(context).edit().putBoolean("seen:$url", seen).apply()
        if (seen) Prefs(context).setRecordingPosition(url, 0L)
    }

    // ---------------------------------------------------------------- favoriter

    /**
     * Favoriter från Mina media. Sparas bara på boxen (inte i Home Assistant).
     * NAS-favoriter syns som andra favoriter. USB-favoriter bara i vuxenläget med historik påslagen.
     */
    data class Fav(val entry: Entry, val sourceId: String, val sourceLabel: String, val usb: Boolean)

    private fun favJson(f: Fav): JSONObject {
        val e = f.entry
        return JSONObject().put("key", e.key).put("name", e.name).put("folder", e.folder).put("kind", e.kind).put("ref", e.ref)
            .put("url", e.url).put("view", e.viewUrl).put("thumb", e.thumb).put("sub", e.subtitle).put("dur", e.durationMs ?: 0L)
            .put("date", e.date ?: 0L).put("source", f.sourceId).put("label", f.sourceLabel).put("usb", f.usb)
            .put("hidden", e.hidden)
    }

    private fun favFrom(o: JSONObject): Fav {
        fun str(k: String) = o.optString(k).takeIf { it.isNotEmpty() && it != "null" }
        val e = Entry(
            key = o.optString("key"), name = o.optString("name"), folder = o.optBoolean("folder"), kind = o.optString("kind"),
            ref = o.optString("ref"), url = str("url"), viewUrl = str("view"), thumb = str("thumb"), subtitle = str("sub"),
            durationMs = o.optLong("dur").takeIf { it > 0 }, date = o.optLong("date").takeIf { it > 0 },
            hidden = o.optBoolean("hidden"),
        )
        return Fav(e, o.optString("source"), o.optString("label"), o.optBoolean("usb"))
    }

    fun favorites(context: Context): List<Fav> = try {
        val a = JSONArray(prefs(context).getString("favorites", "[]"))
        (0 until a.length()).mapNotNull { a.optJSONObject(it) }.map { favFrom(it) }
    } catch (_: Exception) {
        emptyList()
    }

    private fun saveFavorites(context: Context, list: List<Fav>) {
        val a = JSONArray()
        list.forEach { a.put(favJson(it)) }
        prefs(context).edit().putString("favorites", a.toString()).apply()
    }

    fun isFavorite(context: Context, key: String): Boolean = favorites(context).any { it.entry.key == key }

    /** Lägger till eller tar bort. Returnerar true om den nu är favorit. */
    fun toggleFavorite(context: Context, entry: Entry, source: Source): Boolean {
        val list = favorites(context).toMutableList()
        val existing = list.indexOfFirst { it.entry.key == entry.key }
        if (existing >= 0) {
            list.removeAt(existing)
            saveFavorites(context, list)
            return false
        }
        // Det som sparas som favorit i vuxenläget (eller är dolt/vuxenmärkt) förblir dolt i vanligt läge.
        val hide = entry.hidden || Prefs(context).adultMode
        list.add(0, Fav(entry.copy(parent = null, hidden = hide), source.id, source.label, source is UsbSource))
        saveFavorites(context, list)
        return true
    }

    fun removeFavorite(context: Context, key: String) {
        saveFavorites(context, favorites(context).filter { it.entry.key != key })
    }

    /** USB-disken visas bara i vuxenläget. */
    fun usbAllowed(context: Context): Boolean = Prefs(context).adultMode

    /** Favoriterna som får visas nu. usb = USB-disken, annars NAS:en. */
    fun visibleFavorites(context: Context, usb: Boolean): List<Fav> {
        val p = Prefs(context)
        val ok = if (usb) p.adultMode && p.adultHistory else (!p.adultMode || p.adultHistory)
        if (!ok) return emptyList()
        val hiddenNow = hiddenKeys(context)
        // Återvalidera mot dagens dolda mappar och vuxennamn: en gammal sparad kopia litar vi inte på.
        return favorites(context).filter { it.usb == usb }.filter { f ->
            p.adultMode || !(f.entry.hidden || f.entry.key in hiddenNow || isAdultName(f.entry.name))
        }
    }

    // ---------------------------------------------------------------- NAS (DLNA)

    class DlnaSource(private val context: Context, val server: Dlna.Server) : Source {
        override val id = "dlna:" + server.udn.ifEmpty { server.location }
        override val label = server.name
        override val rootRef = "0"

        override fun list(ref: String): List<Entry> {
            val hidden = hiddenKeys(context)
            return Dlna.browseAll(server, ref).mapNotNull { e ->
                val key = "$id:${e.id}"
                when {
                    e.container -> Entry(key, e.title, true, "folder", e.id, null, null, e.art, null, null, null,
                        hidden = key in hidden || isAdultName(e.title))
                    e.isVideo -> Entry(key, cleanTitle(e.title), false, "video", e.id, e.mainUrl, null, e.thumbUrl, e.subtitle,
                        e.durationMs, nameDate(e.title) ?: parseDate(e.date),
                        sizeBytes = e.res.firstNotNullOfOrNull { it.size })
                    e.isImage -> Entry(key, e.title, false, "image", e.id, e.mainUrl, e.viewUrl, e.thumbUrl, null, null,
                        nameDate(e.title) ?: parseDate(e.date))
                    else -> null
                }
            }.filter { it.url != null || it.folder }
        }
    }

    /** Datum i filnamnet (kameror och mobiler) går före filens datum, som ofta är när den kopierades. */
    fun nameDate(name: String): Long? {
        val (y, m, d) = MediaNames.dateFromName(name) ?: return null
        val cal = java.util.Calendar.getInstance()
        cal.set(y, m - 1, d, 12, 0, 0)
        return cal.timeInMillis / 1000
    }

    private fun parseDate(s: String?): Long? {
        val m = Regex("^(\\d{4})-(\\d{2})-(\\d{2})").find(s ?: return null) ?: return null
        val cal = java.util.Calendar.getInstance()
        cal.set(m.groupValues[1].toInt(), m.groupValues[2].toInt() - 1, m.groupValues[3].toInt(), 12, 0, 0)
        return cal.timeInMillis / 1000
    }

    /** Kända servrar sparas så att de syns direkt nästa gång (sökningen i nätet tar ett par sekunder). */
    fun savedServers(context: Context): List<Dlna.Server> = try {
        val a = JSONArray(prefs(context).getString("servers", "[]"))
        (0 until a.length()).mapNotNull { a.optJSONObject(it) }.map {
            Dlna.Server(it.optString("location"), it.optString("name"), it.optString("udn"), it.optString("control"))
        }
    } catch (_: Exception) {
        emptyList()
    }

    fun saveServers(context: Context, servers: List<Dlna.Server>) {
        val a = JSONArray()
        servers.distinctBy { it.udn.ifEmpty { it.location } }.forEach {
            a.put(JSONObject().put("location", it.location).put("name", it.name).put("udn", it.udn).put("control", it.controlUrl))
        }
        prefs(context).edit().putString("servers", a.toString()).apply()
    }

    fun forgetServer(context: Context, server: Dlna.Server) {
        saveServers(context, savedServers(context).filter { it.udn != server.udn || it.location != server.location })
    }

    // ---------------------------------------------------------------- USB-disk

    /** USB-diskens rot (…/Android/data/<app>/files → fyra nivåer upp). */
    data class Usb(val root: File, val label: String)

    fun usbDrives(context: Context): List<Usb> {
        val out = ArrayList<Usb>()
        for (volume in RecStorage.volumes(context)) {
            if (!volume.removable) continue
            val root = volume.dir.parentFile?.parentFile?.parentFile?.parentFile ?: continue
            val label = try {
                val sm = context.getSystemService(Context.STORAGE_SERVICE) as StorageManager
                sm.getStorageVolume(root)?.getDescription(context)
            } catch (_: Exception) {
                null
            } ?: "USB-disk"
            out.add(Usb(root, label))
        }
        return out
    }

    fun hasReadPermission(context: Context): Boolean =
        Build.VERSION.SDK_INT < 23 || context.checkSelfPermission(Manifest.permission.READ_EXTERNAL_STORAGE) == PackageManager.PERMISSION_GRANTED

    const val PERMISSION_REQUEST = 4731

    fun requestReadPermission(activity: Activity) {
        if (Build.VERSION.SDK_INT >= 23) activity.requestPermissions(arrayOf(Manifest.permission.READ_EXTERNAL_STORAGE), PERMISSION_REQUEST)
    }

    class UsbSource(private val context: Context, val drive: Usb) : Source {
        override val id = "usb:" + drive.root.absolutePath
        override val label = drive.label
        override val rootRef: String = drive.root.absolutePath

        override fun list(ref: String): List<Entry> {
            val dir = File(ref)
            val files = dir.listFiles() ?: return emptyList()
            val hidden = hiddenKeys(context)
            val folderArt = files.firstOrNull { it.name.lowercase() in setOf("poster.jpg", "folder.jpg", "cover.jpg") }?.absolutePath
            val out = ArrayList<Entry>()
            for (f in files) {
                val name = f.name
                if (name.startsWith(".") || name.lowercase() in SKIP) continue
                if (f.isDirectory) {
                    val key = "$id:${f.absolutePath}"
                    val art = listOf("poster.jpg", "folder.jpg", "cover.jpg").map { File(f, it) }.firstOrNull { it.isFile }?.absolutePath
                    out.add(Entry(key, name, true, "folder", f.absolutePath, null, null, art, null, null, f.lastModified() / 1000,
                        hidden = key in hidden || isAdultName(name)))
                    continue
                }
                val ext = name.substringAfterLast('.', "").lowercase()
                val base = name.substringBeforeLast('.')
                when {
                    ext in VIDEO -> {
                        val own = listOf("jpg", "png").map { File(dir, "$base.$it") }.firstOrNull { it.isFile }?.absolutePath
                        val sub = SUB.map { File(dir, "$base.$it") }.firstOrNull { it.isFile }?.absolutePath
                        out.add(Entry("$id:${f.absolutePath}", cleanTitle(name), false, "video", f.absolutePath, f.absolutePath, null,
                            own ?: "frame:${f.absolutePath}", sub, null, nameDate(name) ?: (f.lastModified() / 1000),
                            sizeBytes = f.length()))
                    }
                    ext in IMAGE && name.lowercase() !in setOf("poster.jpg", "folder.jpg", "cover.jpg") -> {
                        // En bild med samma namn som en video är videons omslag, inte ett foto.
                        if (VIDEO.any { File(dir, "$base.$it").isFile }) continue
                        out.add(Entry("$id:${f.absolutePath}", name, false, "image", f.absolutePath, f.absolutePath, f.absolutePath,
                            f.absolutePath, null, null, nameDate(name) ?: (f.lastModified() / 1000)))
                    }
                }
            }
            if (folderArt != null) {
                // Mappens omslag gäller videor utan egen bild.
                for (i in out.indices) {
                    val e = out[i]
                    if (e.kind == "video" && e.thumb?.startsWith("frame:") == true && out.count { it.kind == "video" } == 1) {
                        out[i] = e.copy(thumb = folderArt)
                    }
                }
            }
            return out
        }
    }

    /**
     * "Alla videor": videorna i mappen och alla undermappar (dolda mappar bara i vuxenläget), nyast först.
     * Blockerar. progress(klara mappar, kända mappar).
     */
    fun allVideos(context: Context, source: Source, ref: String, progress: (Int, Int) -> Unit): List<Entry> {
        val adult = Prefs(context).adultMode
        val queue = ArrayDeque<Pair<String, String?>>()
        queue.add(ref to null)
        val videos = ArrayList<Entry>()
        var done = 0
        var known = 1
        while (queue.isNotEmpty() && done < 400 && videos.size < 5000) {
            val (folder, folderName) = queue.removeFirst()
            val list = try {
                source.list(folder)
            } catch (_: Exception) {
                emptyList()
            }
            for (e in list) {
                if (e.folder) {
                    if (adult || !e.hidden) {
                        queue.add(e.ref to e.name)
                        known++
                    }
                } else if (e.kind == "video" && e.url != null) {
                    videos.add(e.copy(parent = folderName))
                }
            }
            done++
            progress(done, known)
        }
        return videos.distinctBy { it.url }.sortedByDescending { it.date ?: 0L }
    }

    /** Mappar först, sedan efter namn (naturlig ordning) eller datum (nyast först). */
    /** Sortering: "name", "date" (nyast först), "size" (störst först) eller "length" (längst först). Mappar först, alltid på namn. */
    fun sort(list: List<Entry>, mode: String): List<Entry> {
        val folders = list.filter { it.folder }.sortedWith(compareBy(naturalOrder) { it.name })
        val rest = list.filter { !it.folder }
        val sorted = when (mode) {
            "date" -> rest.sortedByDescending { it.date ?: 0 }
            "size" -> rest.sortedWith(compareByDescending<Entry> { it.sizeBytes ?: -1L }.then(compareBy(naturalOrder) { it.name }))
            "length" -> rest.sortedWith(compareByDescending<Entry> { it.durationMs ?: -1L }.then(compareBy(naturalOrder) { it.name }))
            else -> rest.sortedWith(compareBy(naturalOrder) { it.name })
        }
        return folders + sorted
    }

    /**
     * Längd för filer på USB-disken (där finns ingen server som anger den). Läses ur filen och sparas, så att
     * samma fil bara läses en gång. Blockerar: anropa i bakgrunden. 0 sparas för "kan inte läsas" (försöks inte igen).
     */
    fun fileDuration(context: Context, f: File): Long? {
        val key = "dur:${f.absolutePath}|${f.length()}|${f.lastModified()}"
        val sp = prefs(context)
        if (sp.contains(key)) return sp.getLong(key, 0L).takeIf { it > 0 }
        var ms = 0L
        val retriever = android.media.MediaMetadataRetriever()
        try {
            retriever.setDataSource(f.absolutePath)
            ms = retriever.extractMetadata(android.media.MediaMetadataRetriever.METADATA_KEY_DURATION)?.toLongOrNull() ?: 0L
        } catch (_: Exception) {
            ms = 0L
        } finally {
            try { retriever.release() } catch (_: Exception) {}
        }
        sp.edit().putLong(key, ms).apply()
        return ms.takeIf { it > 0 }
    }

    fun sort(list: List<Entry>, byDate: Boolean): List<Entry> {
        val folders = list.filter { it.folder }.sortedWith(compareBy(naturalOrder) { it.name })
        val rest = list.filter { !it.folder }
        val sorted = if (byDate) rest.sortedByDescending { it.date ?: 0 } else rest.sortedWith(compareBy(naturalOrder) { it.name })
        return folders + sorted
    }
}
