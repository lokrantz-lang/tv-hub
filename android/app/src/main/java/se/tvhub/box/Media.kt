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
                        e.durationMs, parseDate(e.date))
                    e.isImage -> Entry(key, e.title, false, "image", e.id, e.mainUrl, e.viewUrl, e.thumbUrl, null, null, parseDate(e.date))
                    else -> null
                }
            }.filter { it.url != null || it.folder }
        }
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
                            own ?: "frame:${f.absolutePath}", sub, null, f.lastModified() / 1000))
                    }
                    ext in IMAGE && name.lowercase() !in setOf("poster.jpg", "folder.jpg", "cover.jpg") -> {
                        // En bild med samma namn som en video är videons omslag, inte ett foto.
                        if (VIDEO.any { File(dir, "$base.$it").isFile }) continue
                        out.add(Entry("$id:${f.absolutePath}", name, false, "image", f.absolutePath, f.absolutePath, f.absolutePath,
                            f.absolutePath, null, null, f.lastModified() / 1000))
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

    /** Mappar först, sedan efter namn (naturlig ordning) eller datum (nyast först). */
    fun sort(list: List<Entry>, byDate: Boolean): List<Entry> {
        val folders = list.filter { it.folder }.sortedWith(compareBy(naturalOrder) { it.name })
        val rest = list.filter { !it.folder }
        val sorted = if (byDate) rest.sortedByDescending { it.date ?: 0 } else rest.sortedWith(compareBy(naturalOrder) { it.name })
        return folders + sorted
    }
}
