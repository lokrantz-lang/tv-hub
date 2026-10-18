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
    }

    fun enc(value: String): String = URLEncoder.encode(value, "UTF-8")

    /** Blockerande anrop. Får bara köras i en bakgrundstråd. */
    fun requestBlocking(
        method: String,
        path: String,
        body: JSONObject? = null,
        base: String? = null,
        readTimeoutMs: Int = 20_000,
    ): Any? {
        val root = (base ?: prefs.server).trimEnd('/')
        val conn = URL(root + path).openConnection() as HttpURLConnection
        try {
            conn.requestMethod = method
            conn.connectTimeout = 8_000
            conn.readTimeout = readTimeoutMs
            conn.useCaches = false
            conn.setRequestProperty("Accept", "application/json")
            val token = prefs.token
            if (token.isNotEmpty()) conn.setRequestProperty("Authorization", "Bearer $token")
            conn.setRequestProperty("X-TVHub-Mode", if (prefs.adultMode) "adult" else "normal")
            if (body != null) {
                conn.doOutput = true
                conn.setRequestProperty("Content-Type", "application/json; charset=utf-8")
                conn.outputStream.use { it.write(body.toString().toByteArray(Charsets.UTF_8)) }
            }
            val code = conn.responseCode
            val stream = if (code >= 400) conn.errorStream else conn.inputStream
            val text = stream?.bufferedReader(Charsets.UTF_8)?.use { it.readText() } ?: ""
            val parsed: Any? = if (text.isBlank()) null else JSONTokener(text).nextValue()
            if (code >= 400) {
                val message = (parsed as? JSONObject)?.optString("error")?.takeIf { it.isNotEmpty() } ?: "HTTP $code"
                throw ApiException(code, message)
            }
            return parsed
        } finally {
            conn.disconnect()
        }
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
        is SocketTimeoutException -> "TV Hub svarar inte. Är Home Assistant igång?"
        is ConnectException -> "Når inte TV Hub. Kontrollera adressen och att tillägget körs i Home Assistant."
        is UnknownHostException -> "Okänd adress. Använd Home Assistants IP-adress, t.ex. http://192.168.1.10:8099"
        is IOException -> "Nätverksfel: ${e.message ?: e.javaClass.simpleName}"
        else -> e.message ?: e.toString()
    }
}
