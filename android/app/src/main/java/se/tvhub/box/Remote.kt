package se.tvhub.box

import android.content.Context
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.os.SystemClock
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.io.IOException
import java.io.InputStream
import java.io.OutputStream
import java.net.InetSocketAddress
import java.net.Socket
import java.net.URI
import java.security.MessageDigest
import java.security.SecureRandom
import javax.net.ssl.HttpsURLConnection
import javax.net.ssl.SNIHostName
import javax.net.ssl.SSLSocket
import javax.net.ssl.SSLSocketFactory

/**
 * Utanför hemmet: TV Hub nås via Nabu Casa, samma väg som Home Assistants egen webbsida
 * använder för tillägg (Ingress). Hemma används den vanliga adressen (port 8099).
 *
 * Valet görs automatiskt vid varje anrop:
 *  - Inte på wifi/kabel → Nabu Casa direkt.
 *  - På wifi → hemadressen först. Svarar den inte inom 2,5 s används Nabu Casa,
 *    och valet sparas i fem minuter (eller tills nätet byts).
 *
 * Uppgifterna (adress, ingress-väg och Home Assistant-nyckel) hämtas från TV Hub när
 * telefonen är hemma. De ställs in i webbadminen under Boxar.
 */
object Remote {
    private const val HOME_PROBE_MS = 2_500
    private const val REMOTE_STICKY_MS = 5 * 60 * 1000L

    private lateinit var appContext: Context
    private lateinit var prefs: Prefs

    @Volatile private var remoteSince = 0L
    @Volatile private var networkKey: String? = null
    @Volatile private var session: String? = null
    private val sessionLock = Any()

    /** Senast använda vägen, för visning i appen. */
    @Volatile var lastRoute: String = "home"
        private set

    fun init(context: Context, p: Prefs) {
        appContext = context.applicationContext
        prefs = p
    }

    /** Är vägen via Nabu Casa inställd (och appen i mobilläge)? */
    fun enabled(): Boolean = ::prefs.isInitialized && prefs.remoteReady && FormFactor.isMobile(appContext)

    /** Ett mål för ett anrop: bas-URL och hur anropet ska märkas. */
    class Target(val root: String, val remote: Boolean, val session: String?, val connectTimeoutMs: Int)

    /** Fel vid själva uppkopplingen: inget har skickats, så anropet kan göras om via en annan väg. */
    class ConnectFailed(cause: IOException) : IOException(cause.message, cause)

    /** Ingress-sessionen har gått ut (Home Assistant svarade 401 utan TV Hubs eget felmeddelande). */
    class SessionExpired : IOException("Ingress-sessionen har gått ut")

    fun homeTarget(timeoutMs: Int = 8_000) = Target(prefs.server.trimEnd('/'), false, null, timeoutMs)

    /** Kör [block] mot hemadressen eller via Nabu Casa. Blockerande. */
    fun <T> call(block: (Target) -> T): T {
        if (!enabled()) return block(homeTarget())
        val now = SystemClock.elapsedRealtime()
        val net = currentNetwork()
        if (net.key != networkKey) {
            networkKey = net.key
            remoteSince = 0L
        }
        val sticky = remoteSince != 0L && now - remoteSince < REMOTE_STICKY_MS
        if (net.local && !sticky) {
            try {
                val result = block(homeTarget(HOME_PROBE_MS))
                remoteSince = 0L
                lastRoute = "home"
                return result
            } catch (e: ConnectFailed) {
                remoteSince = now
            }
        }
        return viaRemote(block)
    }

    private fun <T> viaRemote(block: (Target) -> T): T {
        val root = prefs.remoteUrl.trimEnd('/') + prefs.remoteIngress
        lastRoute = "remote"
        val first = ensureSession(force = false)
        return try {
            block(Target(root, true, first, 10_000))
        } catch (e: SessionExpired) {
            val fresh = ensureSession(force = true)
            try {
                block(Target(root, true, fresh, 10_000))
            } catch (again: SessionExpired) {
                throw ApiException(401, "Home Assistant släpper inte igenom appen. Tryck Testa vägen i TV Hub-adminen.")
            }
        } catch (e: ConnectFailed) {
            throw ApiException(0, "Når varken TV Hub hemma eller via Nabu Casa. Har telefonen internet?")
        }
    }

    private fun ensureSession(force: Boolean): String {
        synchronized(sessionLock) {
            val current = session
            if (!force && current != null) return current
            val fresh = try {
                HaWs.ingressSession(prefs.remoteUrl, prefs.remoteHaToken)
            } catch (e: HaWs.AuthFailed) {
                throw ApiException(401, "Home Assistant godtar inte appens nyckel. Kontrollera den i TV Hub-adminen under Boxar.")
            } catch (e: IOException) {
                throw ApiException(0, "Når inte Home Assistant via Nabu Casa (${e.message ?: "nätverksfel"}).")
            }
            session = fresh
            return fresh
        }
    }

    /** Hemma: hämta uppgifterna för Nabu Casa från TV Hub. Körs i bakgrunden när appen startar. */
    fun refreshConfig() {
        if (!::prefs.isInitialized || !prefs.paired || !FormFactor.isMobile(appContext)) return
        Api.async({
            Api.requestBlocking("GET", "/device/remote", base = prefs.server, connectTimeoutMs = HOME_PROBE_MS) as? JSONObject
        }) { result ->
            result.onSuccess { cfg ->
                if (cfg == null) return@onSuccess
                if (cfg.optBoolean("available")) {
                    val changed = prefs.remoteHaToken != cfg.optString("ha_token") || prefs.remoteUrl != cfg.optString("url")
                    prefs.remoteUrl = cfg.optString("url")
                    prefs.remoteIngress = cfg.optString("ingress")
                    prefs.remoteHaToken = cfg.optString("ha_token")
                    if (changed) synchronized(sessionLock) { session = null }
                } else {
                    prefs.remoteUrl = ""
                    prefs.remoteIngress = ""
                    prefs.remoteHaToken = ""
                }
            }
            // Misslyckas det är telefonen inte hemma: behåll det som finns.
        }
    }

    private class Net(val key: String, val local: Boolean)

    private fun currentNetwork(): Net {
        return try {
            val cm = appContext.getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
            val network = cm.activeNetwork ?: return Net("none", false)
            val caps = cm.getNetworkCapabilities(network) ?: return Net(network.toString(), true)
            val local = caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) ||
                caps.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET)
            Net(network.toString(), local)
        } catch (_: Exception) {
            Net("unknown", true)
        }
    }

    /** Text till inställningarna. */
    fun describe(): String = when {
        !prefs.remoteReady -> "Inte inställt. Fyll i Nabu Casa i TV Hub-adminen (Boxar) och öppna appen hemma en gång."
        lastRoute == "remote" -> "Ansluten via Nabu Casa (${prefs.remoteUrl.removePrefix("https://")})"
        else -> "Hemma. Utanför hemmet används ${prefs.remoteUrl.removePrefix("https://")}"
    }
}

/**
 * Minimal websocket-klient för Home Assistant: logga in med nyckeln och be om en
 * ingress-session. Ingen tredjepartskod. Bara https/wss.
 */
object HaWs {
    class AuthFailed : IOException("Nyckeln godtogs inte")

    /** Returnerar en ingress-session (giltig 15 min, förlängs vid varje anrop). */
    fun ingressSession(baseUrl: String, token: String, factory: SSLSocketFactory? = null): String {
        val uri = URI(baseUrl.trimEnd('/') + "/api/websocket")
        Conn.open(uri, factory).use { ws ->
            val first = JSONObject(ws.recvText())
            if (first.optString("type") != "auth_required") throw IOException("Oväntat svar från Home Assistant")
            ws.sendText(JSONObject().put("type", "auth").put("access_token", token).toString())
            val auth = JSONObject(ws.recvText())
            if (auth.optString("type") != "auth_ok") throw AuthFailed()
            ws.sendText(JSONObject().put("id", 1).put("type", "supervisor/api")
                .put("endpoint", "/ingress/session").put("method", "post").toString())
            while (true) {
                val msg = JSONObject(ws.recvText())
                if (msg.optInt("id") != 1 || msg.optString("type") != "result") continue
                if (!msg.optBoolean("success")) {
                    val err = msg.optJSONObject("error")?.optString("message") ?: "okänt fel"
                    throw IOException("Home Assistant gav ingen session: $err")
                }
                val session = msg.optJSONObject("result")?.optString("session") ?: ""
                if (session.isEmpty()) throw IOException("Home Assistant gav ingen session")
                return session
            }
        }
    }

    /** En websocket-anslutning (RFC 6455). Klientens ramar maskas, server-ramar läses med fragment och ping. */
    class Conn private constructor(private val socket: Socket) : AutoCloseable {
        private val input: InputStream = socket.getInputStream().buffered()
        private val output: OutputStream = socket.getOutputStream()
        private val random = SecureRandom()

        companion object {
            fun open(uri: URI, factory: SSLSocketFactory? = null, timeoutMs: Int = 10_000): Conn {
                val host = uri.host ?: throw IOException("Ogiltig adress")
                val secure = uri.scheme == "https" || uri.scheme == "wss"
                val port = if (uri.port > 0) uri.port else if (secure) 443 else 80
                val raw = Socket()
                raw.connect(InetSocketAddress(host, port), timeoutMs)
                raw.soTimeout = timeoutMs
                val socket: Socket = if (secure) {
                    val f = factory ?: (SSLSocketFactory.getDefault() as SSLSocketFactory)
                    val ssl = f.createSocket(raw, host, port, true) as SSLSocket
                    // Nabu Casa väljer rätt Home Assistant utifrån namnet (SNI), så det måste skickas.
                    val params = ssl.sslParameters
                    params.serverNames = listOf(SNIHostName(host))
                    ssl.sslParameters = params
                    ssl.startHandshake()
                    if (!HttpsURLConnection.getDefaultHostnameVerifier().verify(host, ssl.session)) {
                        ssl.close()
                        throw IOException("Certifikatet gäller inte $host")
                    }
                    ssl
                } else {
                    raw
                }
                val conn = Conn(socket)
                try {
                    conn.handshake(host, port, uri.rawPath ?: "/", secure)
                } catch (e: IOException) {
                    conn.close()
                    throw e
                }
                return conn
            }
        }

        private fun handshake(host: String, port: Int, path: String, secure: Boolean) {
            val keyBytes = ByteArray(16).also { random.nextBytes(it) }
            val key = b64(keyBytes)
            val hostHeader = if ((secure && port == 443) || (!secure && port == 80)) host else "$host:$port"
            val request = "GET $path HTTP/1.1\r\nHost: $hostHeader\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n" +
                "Sec-WebSocket-Key: $key\r\nSec-WebSocket-Version: 13\r\nUser-Agent: TVHub\r\n\r\n"
            output.write(request.toByteArray(Charsets.ISO_8859_1))
            output.flush()
            val head = readHead()
            val status = head.substringBefore("\r\n")
            if (!status.contains(" 101")) throw IOException("Home Assistant svarade: ${status.ifBlank { "inget svar" }}")
            val expect = b64(MessageDigest.getInstance("SHA-1")
                .digest((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").toByteArray(Charsets.ISO_8859_1)))
            if (!head.contains(expect)) throw IOException("Ogiltigt websocket-svar")
        }

        /** Base64 utan radbrytningar (java.util.Base64 finns inte på Android 7). */
        private fun b64(data: ByteArray): String {
            val alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
            val out = StringBuilder()
            var i = 0
            while (i < data.size) {
                val b0 = data[i].toInt() and 0xFF
                val b1 = if (i + 1 < data.size) data[i + 1].toInt() and 0xFF else -1
                val b2 = if (i + 2 < data.size) data[i + 2].toInt() and 0xFF else -1
                out.append(alphabet[b0 ushr 2])
                out.append(alphabet[((b0 and 3) shl 4) or (if (b1 >= 0) b1 ushr 4 else 0)])
                out.append(if (b1 >= 0) alphabet[((b1 and 15) shl 2) or (if (b2 >= 0) b2 ushr 6 else 0)] else '=')
                out.append(if (b2 >= 0) alphabet[b2 and 63] else '=')
                i += 3
            }
            return out.toString()
        }

        private fun readHead(): String {
            val buf = ByteArrayOutputStream()
            var matched = 0
            val end = byteArrayOf(13, 10, 13, 10)
            while (matched < 4) {
                val b = input.read()
                if (b < 0) throw IOException("Anslutningen stängdes")
                buf.write(b)
                matched = if (b.toByte() == end[matched]) matched + 1 else if (b == 13) 1 else 0
                if (buf.size() > 65536) throw IOException("För stort svar")
            }
            return buf.toString("ISO-8859-1")
        }

        private fun readFully(n: Int): ByteArray {
            val out = ByteArray(n)
            var off = 0
            while (off < n) {
                val r = input.read(out, off, n - off)
                if (r < 0) throw IOException("Anslutningen stängdes")
                off += r
            }
            return out
        }

        fun sendText(text: String) = sendFrame(0x1, text.toByteArray(Charsets.UTF_8))

        private fun sendFrame(opcode: Int, payload: ByteArray) {
            val head = ByteArrayOutputStream()
            head.write(0x80 or opcode)
            val n = payload.size
            when {
                n < 126 -> head.write(0x80 or n)
                n < 65536 -> {
                    head.write(0x80 or 126)
                    head.write(n ushr 8); head.write(n and 0xFF)
                }
                else -> {
                    head.write(0x80 or 127)
                    for (i in 7 downTo 0) head.write(((n.toLong() ushr (8 * i)) and 0xFF).toInt())
                }
            }
            val mask = ByteArray(4).also { random.nextBytes(it) }
            head.write(mask)
            val masked = ByteArray(n) { i -> (payload[i].toInt() xor mask[i % 4].toInt()).toByte() }
            synchronized(output) {
                output.write(head.toByteArray())
                output.write(masked)
                output.flush()
            }
        }

        fun recvText(): String {
            val message = ByteArrayOutputStream()
            while (true) {
                val h = readFully(2)
                val fin = h[0].toInt() and 0x80 != 0
                val opcode = h[0].toInt() and 0x0F
                var n = (h[1].toInt() and 0x7F).toLong()
                if (n == 126L) {
                    val e = readFully(2)
                    n = ((e[0].toInt() and 0xFF) shl 8 or (e[1].toInt() and 0xFF)).toLong()
                } else if (n == 127L) {
                    val e = readFully(8)
                    n = 0
                    for (b in e) n = (n shl 8) or (b.toLong() and 0xFF)
                }
                if (n > 16L * 1024 * 1024) throw IOException("För stort meddelande")
                val mask = if (h[1].toInt() and 0x80 != 0) readFully(4) else null
                val data = readFully(n.toInt())
                if (mask != null) for (i in data.indices) data[i] = (data[i].toInt() xor mask[i % 4].toInt()).toByte()
                when (opcode) {
                    0x8 -> throw IOException("Home Assistant stängde anslutningen")
                    0x9 -> { sendFrame(0xA, data); continue }
                    0xA -> continue
                }
                message.write(data)
                if (fin) return message.toString("UTF-8")
            }
        }

        override fun close() {
            try {
                sendFrame(0x8, byteArrayOf(0x03, 0xE8.toByte()))
            } catch (_: Exception) {
            }
            try {
                socket.close()
            } catch (_: Exception) {
            }
        }
    }
}
