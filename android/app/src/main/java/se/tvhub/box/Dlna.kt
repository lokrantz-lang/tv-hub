package se.tvhub.box

import org.w3c.dom.Element
import org.w3c.dom.Node
import java.io.ByteArrayInputStream
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.HttpURLConnection
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.SocketTimeoutException
import java.net.URL
import javax.xml.parsers.DocumentBuilderFactory

/**
 * DLNA/UPnP-klient för mediaservrar i hemnätet (t.ex. Synologys Media Server).
 *
 * Ren Java/Kotlin utan Android-klasser, så att den kan testas på en vanlig dator mot en låtsasserver.
 * Allt här blockerar: anropa från en bakgrundstråd.
 */
object Dlna {
    data class Server(val location: String, val name: String, val udn: String, val controlUrl: String)

    data class Res(val url: String, val mime: String, val resolution: String?, val size: Long?, val durationMs: Long?, val profile: String?)

    data class Entry(
        val id: String,
        val title: String,
        val container: Boolean,
        val upnpClass: String,
        val childCount: Int?,
        val res: List<Res>,
        val art: String?,
        val date: String?,
        val subtitle: String?,
    ) {
        val isVideo: Boolean get() = upnpClass.startsWith("object.item.videoItem") || res.any { it.mime.startsWith("video/") }
        val isImage: Boolean get() = upnpClass.startsWith("object.item.imageItem") || (!isVideo && res.any { it.mime.startsWith("image/") })

        /** Adressen att spela upp: första videon (eller originalbilden för bilder, den största). */
        val mainUrl: String?
            get() = when {
                isVideo -> res.firstOrNull { it.mime.startsWith("video/") }?.url ?: res.firstOrNull()?.url
                isImage -> res.filter { it.mime.startsWith("image/") }.maxByOrNull { it.pixels() }?.url
                else -> res.firstOrNull()?.url
            }

        /** Bild för bildspelet: högst ungefär 1920 bred om servern har en sådan, annars originalet. */
        val viewUrl: String?
            get() {
                val images = res.filter { it.mime.startsWith("image/") }
                return images.filter { it.pixels() in 1..(2600L * 2600) }.maxByOrNull { it.pixels() }?.url ?: mainUrl
            }

        /** Liten bild: albumArt, annars minsta bilden servern erbjuder. */
        val thumbUrl: String?
            get() = art ?: res.filter { it.mime.startsWith("image/") }.minByOrNull { it.pixels().takeIf { p -> p > 0 } ?: Long.MAX_VALUE }?.url

        val durationMs: Long? get() = res.firstNotNullOfOrNull { it.durationMs }
    }

    private fun Res.pixels(): Long {
        val m = Regex("(\\d+)x(\\d+)").find(resolution ?: return 0) ?: return 0
        return m.groupValues[1].toLong() * m.groupValues[2].toLong()
    }

    data class Page(val entries: List<Entry>, val total: Int)

    private const val SSDP_HOST = "239.255.255.250"
    private const val SSDP_PORT = 1900
    private val TARGETS = listOf("urn:schemas-upnp-org:service:ContentDirectory:1", "urn:schemas-upnp-org:device:MediaServer:1")

    // ---------------------------------------------------------------- hitta servrar

    /**
     * Skickar en SSDP-sökning och samlar svarens LOCATION-adresser.
     * [target] = vart sökningen skickas (multicast i hemnätet, eller en bestämd adress).
     */
    fun search(timeoutMs: Int = 2500, target: InetSocketAddress = InetSocketAddress(InetAddress.getByName(SSDP_HOST), SSDP_PORT)): List<String> {
        val found = LinkedHashSet<String>()
        DatagramSocket().use { socket ->
            socket.soTimeout = 300
            val host = if (target.port == SSDP_PORT) "$SSDP_HOST:$SSDP_PORT" else "${target.hostString}:${target.port}"
            repeat(2) {
                for (st in TARGETS) {
                    val msg = "M-SEARCH * HTTP/1.1\r\nHOST: $host\r\nMAN: \"ssdp:discover\"\r\nMX: 2\r\nST: $st\r\n\r\n".toByteArray()
                    try {
                        socket.send(DatagramPacket(msg, msg.size, target))
                    } catch (_: Exception) {
                    }
                }
            }
            val end = System.currentTimeMillis() + timeoutMs
            val buf = ByteArray(4096)
            while (System.currentTimeMillis() < end) {
                val packet = DatagramPacket(buf, buf.size)
                try {
                    socket.receive(packet)
                } catch (_: SocketTimeoutException) {
                    continue
                } catch (_: Exception) {
                    break
                }
                val text = String(packet.data, 0, packet.length, Charsets.UTF_8)
                text.lineSequence().firstOrNull { it.lowercase().startsWith("location:") }
                    ?.substringAfter(':')?.trim()?.takeIf { it.startsWith("http") }?.let { found.add(it) }
            }
        }
        return found.toList()
    }

    /** Hittar mediaservrar i hemnätet. Bara de som har en innehållskatalog (ContentDirectory) räknas. */
    fun discover(timeoutMs: Int = 2500): List<Server> =
        search(timeoutMs).mapNotNull { describeOrNull(it) }.distinctBy { it.udn.ifEmpty { it.location } }

    /**
     * En server på en bestämd IP-adress (om den inte hittas av sig själv):
     * frågar adressen direkt, sedan hela nätet, sedan vanliga adresser för Synology och MiniDLNA.
     */
    fun findAt(ip: String, timeoutMs: Int = 2500): Server? {
        val host = ip.trim().removePrefix("http://").substringBefore('/').substringBefore(':')
        if (host.isEmpty()) return null
        val direct = try {
            search(timeoutMs / 2, InetSocketAddress(InetAddress.getByName(host), SSDP_PORT))
        } catch (_: Exception) {
            emptyList()
        }
        val all = direct + try {
            search(timeoutMs).filter { URL(it).host == host }
        } catch (_: Exception) {
            emptyList()
        }
        val guesses = listOf(
            "http://$host:50001/desc/device.xml", "http://$host:50001/desc.xml", "http://$host:50001/",
            "http://$host:8200/rootDesc.xml",
        )
        for (location in (all + guesses).distinct()) describeOrNull(location)?.let { return it }
        return null
    }

    fun describeOrNull(location: String): Server? = try {
        describe(location)
    } catch (_: Exception) {
        null
    }

    fun describe(location: String): Server? {
        val doc = parse(http("GET", location, null, null))
        val root = doc.documentElement
        val base = firstText(root, "URLBase")?.trim()?.takeIf { it.startsWith("http") } ?: location
        val device = firstChild(root, "device") ?: return null
        var control: String? = null
        var name = firstText(device, "friendlyName") ?: "Mediaserver"
        var udn = firstText(device, "UDN") ?: ""
        // Tjänsten kan ligga i en underenhet.
        for (service in elements(root, "service")) {
            val type = firstText(service, "serviceType") ?: continue
            if (type.contains("ContentDirectory")) {
                control = firstText(service, "controlURL")
                val owner = (service.parentNode as? Element)?.parentNode as? Element
                if (owner != null && owner.localNameOrName() == "device") {
                    firstText(owner, "friendlyName")?.let { name = it }
                    firstText(owner, "UDN")?.let { udn = it }
                }
                break
            }
        }
        val url = control ?: return null
        return Server(location, name.trim(), udn.trim(), URL(URL(base), url.trim()).toString())
    }

    // ---------------------------------------------------------------- bläddra

    fun browse(server: Server, objectId: String, start: Int, count: Int): Page {
        val body = """<?xml version="1.0" encoding="utf-8"?>
<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">
<s:Body><u:Browse xmlns:u="urn:schemas-upnp-org:service:ContentDirectory:1">
<ObjectID>${xmlEscape(objectId)}</ObjectID><BrowseFlag>BrowseDirectChildren</BrowseFlag><Filter>*</Filter>
<StartingIndex>$start</StartingIndex><RequestedCount>$count</RequestedCount><SortCriteria></SortCriteria>
</u:Browse></s:Body></s:Envelope>"""
        val response = parse(http("POST", server.controlUrl, body, "\"urn:schemas-upnp-org:service:ContentDirectory:1#Browse\""))
        val result = firstText(response.documentElement, "Result") ?: return Page(emptyList(), 0)
        val total = firstText(response.documentElement, "TotalMatches")?.trim()?.toIntOrNull() ?: 0
        return Page(parseDidl(result, server.controlUrl), total)
    }

    /** Alla poster i en mapp (i omgångar om 200), högst [max]. */
    fun browseAll(server: Server, objectId: String, max: Int = 5000): List<Entry> {
        val out = ArrayList<Entry>()
        var start = 0
        while (out.size < max) {
            val page = browse(server, objectId, start, 200)
            out.addAll(page.entries)
            start += page.entries.size
            if (page.entries.isEmpty() || start >= page.total) break
        }
        return out
    }

    fun parseDidl(xml: String, baseUrl: String): List<Entry> {
        if (xml.isBlank()) return emptyList()
        val doc = parse(xml.toByteArray(Charsets.UTF_8))
        val out = ArrayList<Entry>()
        val children = doc.documentElement.childNodes
        for (i in 0 until children.length) {
            val el = children.item(i) as? Element ?: continue
            val tag = el.localNameOrName()
            if (tag != "container" && tag != "item") continue
            val res = ArrayList<Res>()
            var subtitle: String? = null
            for (r in directChildren(el, "res")) {
                val url = absolute(r.textContent.trim(), baseUrl) ?: continue
                val protocol = r.getAttribute("protocolInfo")
                val mime = protocol.split(':').getOrNull(2)?.lowercase() ?: ""
                if (mime.contains("srt") || mime.contains("subrip") || mime.startsWith("text/")) {
                    if (subtitle == null) subtitle = url
                    continue
                }
                val profile = Regex("DLNA\\.ORG_PN=([A-Za-z0-9_]+)").find(protocol)?.groupValues?.get(1)
                res.add(Res(url, mime, r.getAttribute("resolution").ifEmpty { null }, r.getAttribute("size").toLongOrNull(),
                    parseDuration(r.getAttribute("duration")), profile))
            }
            for (c in directChildren(el, "CaptionInfoEx") + directChildren(el, "CaptionInfo")) {
                if (subtitle == null) subtitle = absolute(c.textContent.trim(), baseUrl)
            }
            out.add(
                Entry(
                    id = el.getAttribute("id"),
                    title = directChildren(el, "title").firstOrNull()?.textContent?.trim().orEmpty().ifEmpty { "Utan namn" },
                    container = tag == "container",
                    upnpClass = directChildren(el, "class").firstOrNull()?.textContent?.trim().orEmpty(),
                    childCount = el.getAttribute("childCount").toIntOrNull(),
                    res = res,
                    art = directChildren(el, "albumArtURI").firstOrNull()?.textContent?.trim()?.let { absolute(it, baseUrl) },
                    date = directChildren(el, "date").firstOrNull()?.textContent?.trim(),
                    subtitle = subtitle,
                ),
            )
        }
        return out
    }

    /** "1:23:45.000" -> millisekunder. */
    fun parseDuration(value: String?): Long? {
        val parts = value?.trim()?.split(':') ?: return null
        if (parts.size != 3) return null
        val h = parts[0].toLongOrNull() ?: return null
        val m = parts[1].toLongOrNull() ?: return null
        val s = parts[2].toDoubleOrNull() ?: return null
        return ((h * 3600 + m * 60) * 1000 + (s * 1000).toLong()).takeIf { it > 0 }
    }

    // ---------------------------------------------------------------- hjälp

    private fun http(method: String, url: String, body: String?, soapAction: String?): ByteArray {
        val conn = URL(url).openConnection() as HttpURLConnection
        conn.connectTimeout = 5_000
        conn.readTimeout = 15_000
        conn.requestMethod = method
        conn.setRequestProperty("User-Agent", "TVHub/1.0 UPnP/1.0 DLNADOC/1.50")
        if (body != null) {
            conn.doOutput = true
            conn.setRequestProperty("Content-Type", "text/xml; charset=\"utf-8\"")
            soapAction?.let { conn.setRequestProperty("SOAPACTION", it) }
            conn.outputStream.use { it.write(body.toByteArray(Charsets.UTF_8)) }
        }
        try {
            val code = conn.responseCode
            if (code !in 200..299) throw java.io.IOException("HTTP $code från $url")
            return conn.inputStream.use { it.readBytes() }
        } finally {
            conn.disconnect()
        }
    }

    private fun parse(bytes: ByteArray): org.w3c.dom.Document {
        val factory = DocumentBuilderFactory.newInstance()
        factory.isNamespaceAware = true
        try {
            factory.setFeature("http://apache.org/xml/features/disallow-doctype-decl", true)
        } catch (_: Exception) {
        }
        return factory.newDocumentBuilder().parse(ByteArrayInputStream(bytes))
    }

    private fun Node.localNameOrName(): String = localName ?: nodeName.substringAfter(':')

    private fun elements(root: Element, name: String): List<Element> {
        val list = root.getElementsByTagNameNS("*", name)
        return (0 until list.length).mapNotNull { list.item(it) as? Element }
    }

    private fun directChildren(el: Element, name: String): List<Element> {
        val out = ArrayList<Element>()
        val nodes = el.childNodes
        for (i in 0 until nodes.length) {
            val c = nodes.item(i) as? Element ?: continue
            if (c.localNameOrName() == name) out.add(c)
        }
        return out
    }

    private fun firstChild(el: Element, name: String): Element? = directChildren(el, name).firstOrNull()

    private fun firstText(el: Element, name: String): String? = elements(el, name).firstOrNull()?.textContent

    private fun absolute(url: String, base: String): String? {
        if (url.isEmpty()) return null
        return try {
            URL(URL(base), url).toString()
        } catch (_: Exception) {
            null
        }
    }

    private fun xmlEscape(s: String) = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\"", "&quot;")
}
