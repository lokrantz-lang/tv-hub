package se.tvhub.box

import android.os.Handler
import android.os.Looper
import org.json.JSONArray
import org.json.JSONObject
import org.json.JSONTokener
import java.io.IOException
import java.net.ConnectException
import java.net.HttpURLConnection
import java.net.SocketTimeoutException
import java.net.URL
import java.net.URLEncoder
import java.net.UnknownHostException
import java.util.concurrent.Executors

class ApiException(val status: Int, message: String) : Exception(message)

/** Anrop mot TV Hub i Home Assistant. Allt nätverk körs i bakgrunden. */
object Api {
    private val pool = Executors.newFixedThreadPool(4)
    private val main = Handler(Looper.getMainLooper())
    private lateinit var prefs: Prefs

    fun init(p: Prefs) {
        prefs = p
        Remote.init(p.appContext, p)
    }

    fun enc(value: String): String = URLEncoder.encode(value, "UTF-8")

    /**
     * Blockerande anrop. Får bara köras i en bakgrundstråd.
     * [base] = en bestämd adress (parning). Annars väljer [Remote] hemadressen eller Nabu Casa.
     */
    fun requestBlocking(
        method: String,
        path: String,
        body: JSONObject? = null,
        base: String? = null,
        readTimeoutMs: Int = 20_000,
        connectTimeoutMs: Int = 8_000,
    ): Any? {
        if (base != null) {
            return try {
                send(Remote.Target(base.trimEnd('/'), false, null, connectTimeoutMs), method, path, body, readTimeoutMs)
            } catch (e: Remote.ConnectFailed) {
                throw e.cause ?: e
            }
        }
        if (!Remote.enabled()) {
            return try {
                send(Remote.homeTarget(connectTimeoutMs), method, path, body, readTimeoutMs)
            } catch (e: Remote.ConnectFailed) {
                throw e.cause ?: e
            }
        }
        return Remote.call { target -> send(target, method, path, body, readTimeoutMs) }
    }

    /** Öppnar en anslutning med rätt adress och nycklar (t.ex. för att hämta en fil). Blockerande. */
    fun openBlocking(path: String, readTimeoutMs: Int = 120_000): HttpURLConnection {
        val open = { target: Remote.Target ->
            val conn = prepare(target, "GET", path, readTimeoutMs)
            try {
                conn.connect()
            } catch (e: IOException) {
                conn.disconnect()
                throw Remote.ConnectFailed(e)
            }
            if (target.remote && conn.responseCode == 401 && !isTvHubError(conn)) {
                conn.disconnect()
                throw Remote.SessionExpired()
            }
            conn
        }
        return if (Remote.enabled()) Remote.call(open) else try {
            open(Remote.homeTarget())
        } catch (e: Remote.ConnectFailed) {
            throw e.cause ?: e
        }
    }

    private fun prepare(target: Remote.Target, method: String, path: String, readTimeoutMs: Int): HttpURLConnection {
        val conn = URL(target.root + path).openConnection() as HttpURLConnection
        conn.requestMethod = method
        conn.connectTimeout = target.connectTimeoutMs
        conn.readTimeout = readTimeoutMs
        conn.useCaches = false
        conn.setRequestProperty("Accept", "application/json")
        val token = prefs.token
        if (target.remote) {
            // Via Nabu Casa: Home Assistants ingress-session som kaka, boxens nyckel i egen rubrik.
            conn.instanceFollowRedirects = false
            conn.setRequestProperty("Cookie", "ingress_session=${target.session}")
            if (token.isNotEmpty()) conn.setRequestProperty("X-TVHub-Token", token)
        } else if (token.isNotEmpty()) {
            conn.setRequestProperty("Authorization", "Bearer $token")
        }
        val adult = prefs.adultMode
        conn.setRequestProperty("X-TVHub-Mode", if (adult) "adult" else "normal")
        conn.setRequestProperty("X-TVHub-History", if (!adult || prefs.adultHistory) "show" else "hide")
        return conn
    }

    /** TV Hubs egna fel är JSON med "error". Home Assistants svar när sessionen gått ut är det inte. */
    private fun isTvHubError(conn: HttpURLConnection): Boolean =
        (conn.contentType ?: "").startsWith("application/json")

    private fun send(target: Remote.Target, method: String, path: String, body: JSONObject?, readTimeoutMs: Int): Any? {
        val conn = prepare(target, method, path, readTimeoutMs)
        try {
            try {
                if (body != null) {
                    conn.doOutput = true
                    conn.setRequestProperty("Content-Type", "application/json; charset=utf-8")
                }
                conn.connect()
            } catch (e: IOException) {
                throw Remote.ConnectFailed(e)
            }
            if (body != null) conn.outputStream.use { it.write(body.toString().toByteArray(Charsets.UTF_8)) }
            val code = conn.responseCode
            if (target.remote && code == 401 && !isTvHubError(conn)) throw Remote.SessionExpired()
            val stream = if (code >= 400) conn.errorStream else conn.inputStream
            val text = stream?.bufferedReader(Charsets.UTF_8)?.use { it.readText() } ?: ""
            val parsed: Any? = if (text.isBlank()) null else try {
                JSONTokener(text).nextValue()
            } catch (e: Exception) {
                if (code >= 400) null else throw e
            }
            if (code >= 400) {
                val message = (parsed as? JSONObject)?.optString("error")?.takeIf { it.isNotEmpty() }
                    ?: if (target.remote) remoteHttpError(code) else "HTTP $code"
                throw ApiException(code, message)
            }
            return parsed
        } finally {
            conn.disconnect()
        }
    }

    private fun remoteHttpError(code: Int): String = when (code) {
        403 -> "Home Assistant nekar appen (HTTP 403). Uppdatera TV Hub-tillägget och tryck Testa vägen i adminen."
        502, 503, 504 -> "TV Hub svarar inte via Nabu Casa. Är tillägget igång?"
        else -> "Fel via Nabu Casa (HTTP $code)"
    }

    /** Kör [work] i bakgrunden och lämnar resultatet på huvudtråden. */
    fun <T> async(work: () -> T, done: (Result<T>) -> Unit) {
        pool.execute {
            val result = try {
                Result.success(work())
            } catch (e: Exception) {
                Result.failure(e)
            }
            main.post { done(result) }
        }
    }

    fun obj(method: String, path: String, body: JSONObject? = null, done: (Result<JSONObject>) -> Unit) {
        async({ requestBlocking(method, path, body) as? JSONObject ?: JSONObject() }, done)
    }

    fun arr(path: String, done: (Result<JSONArray>) -> Unit) {
        async({ requestBlocking("GET", path) as? JSONArray ?: JSONArray() }, done)
    }

    /** Skicka och glöm (rapporter, stopp). */
    fun fire(path: String, body: JSONObject) {
        pool.execute {
            try {
                requestBlocking("POST", path, body)
            } catch (_: Exception) {
            }
        }
    }

    fun report(kind: String, sessionId: String?, detail: JSONObject) {
        val body = JSONObject().put("kind", kind).put("detail", detail)
        if (sessionId != null) body.put("session_id", sessionId)
        fire("/device/report", body)
    }

    fun friendly(e: Throwable): String = when (e) {
        is ApiException -> e.message ?: "Fel från TV Hub"
        is Remote.ConnectFailed -> friendly(e.cause ?: e)
        is SocketTimeoutException -> "TV Hub svarar inte. Är Home Assistant igång?"
        is ConnectException -> "Når inte TV Hub. Kontrollera adressen och att tillägget körs i Home Assistant."
        is UnknownHostException -> "Okänd adress. Använd Home Assistants IP-adress, t.ex. http://192.168.1.10:8099"
        is IOException -> "Nätverksfel: ${e.message ?: e.javaClass.simpleName}"
        else -> e.message ?: e.toString()
    }
}
