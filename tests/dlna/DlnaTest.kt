import se.tvhub.box.Dlna
import se.tvhub.box.MediaNames
import java.net.InetAddress
import java.net.InetSocketAddress

var fails = 0
fun check(name: String, ok: Boolean, info: Any? = null) {
    println((if (ok) "  ok   " else "  FEL  ") + name + if (!ok && info != null) "  -> $info" else "")
    if (!ok) fails++
}

fun main(args: Array<String>) {
    val httpPort = args[0].toInt()
    val ssdpPort = args[1].toInt()
    println("DLNA-klienten mot låtsas-Synology")
    val found = Dlna.search(1200, InetSocketAddress(InetAddress.getByName("127.0.0.1"), ssdpPort))
    check("SSDP-sökning ger serverns adress", found == listOf("http://127.0.0.1:$httpPort/desc/device.xml"), found)
    val server = Dlna.describe(found.first())!!
    check("beskrivning: namn och innehållskatalog", server.name == "DiskStation" && server.controlUrl == "http://127.0.0.1:$httpPort/cd/control"
        && server.udn.startsWith("uuid:"), server)
    val guessed = Dlna.findAt("127.0.0.1", 600)
    check("utan svar på SSDP: vanliga Synology-adresser prövas (ingen server på 50001 här)", guessed == null, guessed)
    val root = Dlna.browseAll(server, "0")
    check("roten: två mappar", root.map { it.title } == listOf("Video", "Foto") && root.all { it.container }, root)
    val video = Dlna.browseAll(server, "1")
    check("mappnamn med & avkodas", video.single().title == "Barnfilmer & tecknat" && video.single().childCount == 250, video)
    val films = Dlna.browseAll(server, "20")
    check("250 filmer hämtas i omgångar om 200", films.size == 250 && films.last().title == "Film 249 (2019)", films.size)
    val f = films.first()
    check("film: video, längd, adress, undertext och bild", f.isVideo && !f.isImage && f.durationMs == 5025500L
        && f.mainUrl == "http://127.0.0.1:$httpPort/v/0.mkv" && f.subtitle == "http://127.0.0.1:$httpPort/sub/0.srt"
        && f.thumbUrl == "http://127.0.0.1:$httpPort/thumb/v0.jpg" && f.res.size == 1, f)
    val photos = Dlna.browseAll(server, "2")
    val p = photos.first()
    check("bild: original, visningsstorlek och miniatyr", p.isImage && p.mainUrl!!.endsWith("/p/0.jpg")
        && p.viewUrl!!.endsWith("/p/0_med.jpg") && p.thumbUrl!!.endsWith("/p/0_tn.jpg"), p)
    check("tom mapp", Dlna.browseAll(server, "finns-inte").isEmpty())
    check("längd: 0:00:05", Dlna.parseDuration("0:00:05") == 5000L && Dlna.parseDuration("x") == null)
    println("Filnamn")
    val names = mapOf(
        "The.Matrix.1999.1080p.BluRay.x264.mkv" to "The Matrix (1999)",
        "Frost (2013).mp4" to "Frost (2013)",
        "Bluey.S02E07.720p.WEB-DL.mkv" to "Bluey S02E07",
        "Pippi_Långstrump_[1969].avi" to "Pippi Långstrump (1969)",
        "IMG_1234.MOV" to "IMG 1234",
        "Midsommar hos mormor.mp4" to "Midsommar hos mormor",
        "Film.utan.år.1080p.x265.mkv" to "Film utan år",
    )
    for ((raw, want) in names) check("\"$raw\" -> \"$want\"", MediaNames.cleanTitle(raw) == want, MediaNames.cleanTitle(raw))
    val sorted = listOf("Avsnitt 10", "Avsnitt 2", "avsnitt 1", "Bonus").sortedWith(MediaNames.naturalOrder)
    check("naturlig sortering", sorted == listOf("avsnitt 1", "Avsnitt 2", "Avsnitt 10", "Bonus"), sorted)
    println(if (fails == 0) "\nALLT OK" else "\n$fails FEL")
    kotlin.system.exitProcess(if (fails == 0) 0 else 1)
}
