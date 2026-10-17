package se.tvhub.box

import android.app.Activity
import android.app.AlertDialog
import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.view.KeyEvent
import android.view.View
import android.view.WindowManager
import android.widget.ProgressBar
import android.widget.TextView
import androidx.media3.common.MediaItem
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import androidx.media3.common.util.UnstableApi
import androidx.media3.datasource.DefaultHttpDataSource
import androidx.media3.exoplayer.DefaultLoadControl
import androidx.media3.exoplayer.DefaultRenderersFactory
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.source.DefaultMediaSourceFactory
import androidx.media3.exoplayer.upstream.DefaultBandwidthMeter
import androidx.media3.extractor.DefaultExtractorsFactory
import androidx.media3.extractor.ts.DefaultTsPayloadReaderFactory
import androidx.media3.ui.PlayerView
import org.json.JSONArray
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * Uppspelning.
 *  - Frågar TV Hub om strömadress (anslutningsvakten kan svara "en annan box tittar").
 *  - Skickar heartbeat med mätvärden var tionde sekund och stoppar om en annan box tar över.
 *  - Rapporterar hack och fel så att webbadminen kan säga var problemet ligger.
 *  - Försöker igen en gång, byter sedan portal automatiskt.
 *  - Live: upp/ner byter kanal. Avsnitt: nästa spelas automatiskt.
 */
@androidx.annotation.OptIn(markerClass = [UnstableApi::class])
class PlayerActivity : Activity() {
    companion object {
        fun start(
            context: Context,
            type: String,
            id: String,
            name: String,
            ext: String?,
            queueIds: ArrayList<String>? = null,
            queueNames: ArrayList<String>? = null,
            queueExts: ArrayList<String>? = null,
            queueIndex: Int = -1,
        ) {
            val intent = Intent(context, PlayerActivity::class.java)
                .putExtra("type", type)
                .putExtra("id", id)
                .putExtra("name", name)
                .putExtra("ext", ext)
                .putExtra("queue_index", queueIndex)
            if (queueIds != null) intent.putStringArrayListExtra("queue_ids", queueIds)
            if (queueNames != null) intent.putStringArrayListExtra("queue_names", queueNames)
            if (queueExts != null) intent.putStringArrayListExtra("queue_exts", queueExts)
            context.startActivity(intent)
        }

        private const val DEFAULT_HEARTBEAT_MS = 10_000L
        private const val STALL_REPORT_MIN_MS = 700L
    }

    private lateinit var prefs: Prefs
    private lateinit var playerView: PlayerView
    private lateinit var overlay: View
    private lateinit var titleView: TextView
    private lateinit var epgView: TextView
    private lateinit var statusView: TextView
    private lateinit var spinner: ProgressBar
    private lateinit var bandwidthMeter: DefaultBandwidthMeter
    private val handler = Handler(Looper.getMainLooper())
    private val timeFormat = SimpleDateFormat("HH:mm", Locale("sv", "SE"))
    private var player: ExoPlayer? = null

    // Vad som spelas
    private var type = "live"
    private var itemId = ""
    private var itemName = ""
    private var ext: String? = null
    private var queueIds: List<String> = emptyList()
    private var queueNames: List<String> = emptyList()
    private var queueExts: List<String> = emptyList()
    private var queueIndex = -1

    // Session hos TV Hub
    private var sessionId: String? = null
    private var portalId = -1
    private var portalName = ""
    private val excludedPortals = ArrayList<Int>()
    private var heartbeatMs = DEFAULT_HEARTBEAT_MS
    private var userAgent = "IPTVSmartersPlayer"
    private var requestGeneration = 0
    private var closing = false
    private var fatalShown = false
    private var conflictDialog: AlertDialog? = null

    // Mätvärden
    private var playRequestedAt = 0L
    private var wasReady = false
    private var stallStart = 0L
    private var stalls = 0
    private var stallMsTotal = 0L
    private var retries = 0
    private var failovers = 0
    private var speedTested = false
    private var zapTask: Runnable? = null

    private val hideOverlay = Runnable { overlay.visibility = View.GONE }

    private val heartbeat = object : Runnable {
        override fun run() {
            val sid = sessionId ?: return
            val body = JSONObject().put("session_id", sid).put("metrics", metrics())
            Api.async({ Api.requestBlocking("POST", "/device/heartbeat", body) as? JSONObject }) { result ->
                if (closing || sid != sessionId) return@async
                result.onSuccess { response ->
                    if (response != null && response.optBoolean("stop")) {
                        onRemoteStop(response.optString("reason"), response.optString("message"))
                    }
                }
            }
            handler.postDelayed(this, heartbeatMs)
        }
    }

    private val listener = object : Player.Listener {
        override fun onPlaybackStateChanged(playbackState: Int) {
            val now = SystemClock.elapsedRealtime()
            when (playbackState) {
                Player.STATE_BUFFERING -> {
                    spinner.visibility = View.VISIBLE
                    if (wasReady && stallStart == 0L) stallStart = now
                }
                Player.STATE_READY -> {
                    spinner.visibility = View.GONE
                    if (!fatalShown) statusView.visibility = View.GONE
                    if (!wasReady) {
                        wasReady = true
                        retries = 0
                        Api.report(
                            "start", sessionId,
                            JSONObject().put("startup_ms", now - playRequestedAt).put("portal", portalName),
                        )
                    }
                    if (stallStart != 0L) {
                        val ms = now - stallStart
                        stallStart = 0L
                        if (ms >= STALL_REPORT_MIN_MS) onStall(ms)
                    }
                }
                Player.STATE_ENDED -> onEnded()
                else -> Unit
            }
        }

        override fun onPlayerError(error: PlaybackException) {
            onError(error)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        setContentView(R.layout.activity_player)
        prefs = Prefs(this)
        Api.init(prefs)
        playerView = findViewById(R.id.player_view)
        overlay = findViewById(R.id.overlay)
        titleView = findViewById(R.id.title)
        epgView = findViewById(R.id.epg)
        statusView = findViewById(R.id.status)
        spinner = findViewById(R.id.spinner)
        bandwidthMeter = DefaultBandwidthMeter.getSingletonInstance(this)

        type = intent.getStringExtra("type") ?: "live"
        itemId = intent.getStringExtra("id") ?: ""
        itemName = intent.getStringExtra("name") ?: ""
        ext = intent.getStringExtra("ext")
        queueIds = intent.getStringArrayListExtra("queue_ids") ?: emptyList()
        queueNames = intent.getStringArrayListExtra("queue_names") ?: emptyList()
        queueExts = intent.getStringArrayListExtra("queue_exts") ?: emptyList()
        queueIndex = intent.getIntExtra("queue_index", -1)
        if (itemId.isEmpty()) {
            finish()
            return
        }
        requestPlay()
    }

    override fun onStop() {
        super.onStop()
        // Lämnar man spelaren (Tillbaka, Hem) stängs strömmen direkt, så att den
        // inte ligger kvar som en andra anslutning hos Toucan.
        close("closed")
        finish()
    }

    override fun onDestroy() {
        close("closed")
        super.onDestroy()
    }

    private fun close(reason: String) {
        if (closing) return
        closing = true
        conflictDialog?.dismiss()
        stopSession(reason)
        releasePlayer()
        handler.removeCallbacksAndMessages(null)
    }

    // ---------------------------------------------------------------- begära ström

    private fun requestPlay(force: Boolean = false, failoverFrom: String? = null, reason: String? = null) {
        if (closing) return
        val generation = ++requestGeneration
        playRequestedAt = SystemClock.elapsedRealtime()
        spinner.visibility = View.VISIBLE
        titleView.text = itemName
        val body = JSONObject()
            .put("type", type)
            .put("id", itemId)
            .put("name", itemName)
            .put("force", force)
            .put("exclude_portals", JSONArray(excludedPortals))
        ext?.let { body.put("ext", it) }
        if (failoverFrom != null) body.put("failover_from", failoverFrom).put("reason", reason ?: "")
        Api.obj("POST", "/device/play", body) { result ->
            if (closing || generation != requestGeneration) return@obj
            result.onSuccess { handlePlayResponse(it) }
                .onFailure { error ->
                    if (error is ApiException && error.status == 401) {
                        showFatal("Boxen är inte längre parad. Para om under Inställningar.", canRetry = false)
                    } else {
                        showFatal(Api.friendly(error))
                    }
                }
        }
    }

    private fun handlePlayResponse(response: JSONObject) {
        when (response.optString("status")) {
            "ok" -> {
                sessionId = response.optString("session_id")
                portalId = response.optInt("portal_id", -1)
                portalName = response.optString("portal_name")
                heartbeatMs = response.optLong("heartbeat_ms", DEFAULT_HEARTBEAT_MS).coerceAtLeast(3_000L)
                response.optStringOrNull("user_agent")?.let { userAgent = it }
                itemName = response.optStringOrNull("name") ?: itemName
                titleView.text = itemName
                epgView.text = if (portalName.isNotEmpty()) "via $portalName" else ""
                fatalShown = false
                statusView.visibility = View.GONE
                startPlayer(response.getString("url"))
                showOverlay(4_000)
                if (type == "live") loadEpg()
            }
            "conflict" -> {
                spinner.visibility = View.GONE
                conflictDialog = AlertDialog.Builder(this)
                    .setTitle("En annan box tittar")
                    .setMessage("${response.optString("message")}\n\nVill du ta över? Den andra boxen stoppas innan den här startar.")
                    .setPositiveButton("Ta över") { _, _ -> requestPlay(force = true) }
                    .setNegativeButton("Avbryt") { _, _ -> finish() }
                    .setOnCancelListener { finish() }
                    .show()
            }
            "waiting" -> {
                statusView.text = response.optString("message", "Väntar…")
                statusView.visibility = View.VISIBLE
                handler.postDelayed({ requestPlay(force = true) }, response.optLong("retry_ms", 1_000L))
            }
            else -> showFatal(response.optString("error", "Okänt fel från TV Hub."))
        }
    }

    // ---------------------------------------------------------------- spelare

    private fun startPlayer(url: String) {
        releasePlayer()
        wasReady = false
        stallStart = 0L
        val http = DefaultHttpDataSource.Factory()
            .setUserAgent(userAgent)
            .setAllowCrossProtocolRedirects(true)
            .setConnectTimeoutMs(10_000)
            .setReadTimeoutMs(20_000)
        val extractors = DefaultExtractorsFactory()
            .setTsExtractorFlags(DefaultTsPayloadReaderFactory.FLAG_ALLOW_NON_IDR_KEYFRAMES)
        val loadControl = DefaultLoadControl.Builder()
            .setBufferDurationsMs(15_000, 50_000, 2_500, 5_000)
            .build()
        val renderers = DefaultRenderersFactory(this).setEnableDecoderFallback(true)
        val newPlayer = ExoPlayer.Builder(this, renderers)
            .setMediaSourceFactory(DefaultMediaSourceFactory(http, extractors))
            .setLoadControl(loadControl)
            .setBandwidthMeter(bandwidthMeter)
            .build()
        newPlayer.addListener(listener)
        playerView.player = newPlayer
        val live = type == "live"
        playerView.useController = !live
        playerView.isFocusable = !live
        if (!live) playerView.requestFocus()
        newPlayer.setMediaItem(MediaItem.fromUri(url))
        newPlayer.playWhenReady = true
        newPlayer.prepare()
        player = newPlayer
        handler.removeCallbacks(heartbeat)
        handler.postDelayed(heartbeat, heartbeatMs)
    }

    private fun releasePlayer() {
        handler.removeCallbacks(heartbeat)
        player?.let {
            it.removeListener(listener)
            it.release()
        }
        player = null
        playerView.player = null
    }

    private fun stopSession(reason: String) {
        val sid = sessionId ?: return
        sessionId = null
        Api.fire("/device/stop", JSONObject().put("session_id", sid).put("reason", reason))
    }

    private fun metrics(): JSONObject {
        val o = JSONObject()
        o.put("bandwidth_bps", bandwidthMeter.bitrateEstimate)
        o.put("stalls", stalls)
        o.put("stall_ms", stallMsTotal)
        o.put("portal", portalName)
        player?.let { p ->
            o.put("buffered_ms", p.totalBufferedDuration)
            o.put("position_ms", p.currentPosition)
            p.videoFormat?.let { f ->
                if (f.bitrate > 0) o.put("bitrate_bps", f.bitrate)
                if (f.width > 0) o.put("resolution", "${f.width}x${f.height}")
            }
        }
        return o
    }

    // ---------------------------------------------------------------- problem

    private fun onStall(ms: Long) {
        stalls++
        stallMsTotal += ms
        Api.report("stall", sessionId, metrics().put("ms", ms))
        // Efter andra hacket: mät hemnätet en gång, så att diagnostiken kan utesluta Wi-Fi.
        if (stalls >= 2 && !speedTested) {
            speedTested = true
            val sid = sessionId
            Api.async({ SpeedTest.runBlocking(prefs, 5) }) { result ->
                result.onSuccess { mbps ->
                    Api.report("speedtest", sid, JSONObject().put("mbps", mbps).put("trigger", "stall"))
                }
            }
        }
    }

    private fun onError(error: PlaybackException) {
        val p = player
        if (error.errorCode == PlaybackException.ERROR_CODE_BEHIND_LIVE_WINDOW && p != null) {
            p.seekToDefaultPosition()
            p.prepare()
            return
        }
        Api.report(
            "error", sessionId,
            metrics().put("code", error.errorCodeName).put("message", error.message ?: ""),
        )
        if (retries < 1 && p != null) {
            retries++
            statusView.text = "Tappade strömmen, försöker igen…"
            statusView.visibility = View.VISIBLE
            handler.postDelayed({
                if (!closing) player?.let {
                    it.prepare()
                    it.playWhenReady = true
                }
            }, 1_500)
            return
        }
        if (failovers < 2 && portalId > 0) {
            failovers++
            retries = 0
            excludedPortals.add(portalId)
            val from = portalName
            statusView.text = "Byter till en annan portal…"
            statusView.visibility = View.VISIBLE
            releasePlayer()
            requestPlay(failoverFrom = from, reason = error.errorCodeName)
            return
        }
        releasePlayer()
        showFatal("Det går inte att spela just nu.\n${describe(error)}")
    }

    private fun describe(error: PlaybackException): String = when (error.errorCode) {
        PlaybackException.ERROR_CODE_IO_BAD_HTTP_STATUS -> "Källan nekade strömmen."
        PlaybackException.ERROR_CODE_IO_NETWORK_CONNECTION_FAILED,
        PlaybackException.ERROR_CODE_IO_NETWORK_CONNECTION_TIMEOUT -> "Ingen kontakt med källan."
        PlaybackException.ERROR_CODE_PARSING_CONTAINER_UNSUPPORTED,
        PlaybackException.ERROR_CODE_PARSING_CONTAINER_MALFORMED,
        PlaybackException.ERROR_CODE_DECODER_INIT_FAILED,
        PlaybackException.ERROR_CODE_DECODING_FORMAT_UNSUPPORTED -> "Boxen kan inte spela det här formatet."
        else -> error.errorCodeName
    }

    private fun onEnded() {
        if (type == "live") {
            // En liveström ska inte ta slut. Behandla som avbrott.
            releasePlayer()
            statusView.text = "Strömmen bröts, ansluter igen…"
            statusView.visibility = View.VISIBLE
            handler.postDelayed({ requestPlay() }, 1_000)
            return
        }
        if (type == "episode" && queueIndex >= 0 && queueIndex + 1 < queueIds.size) {
            moveTo(queueIndex + 1)
            releasePlayer()
            requestPlay()
            return
        }
        finish()
    }

    private fun onRemoteStop(reason: String, message: String) {
        stopSession(if (reason == "takeover") "takeover" else "remote_stop")
        releasePlayer()
        val text = if (reason == "takeover") {
            "En annan box tog över.\nToucan tillåter bara en ström åt gången."
        } else {
            message.ifEmpty { "Uppspelningen stoppades av TV Hub." }
        }
        showFatal(text)
    }

    private fun showFatal(text: String, canRetry: Boolean = true) {
        fatalShown = true
        spinner.visibility = View.GONE
        statusView.text = if (canRetry) "$text\n\nOK: försök igen   Tillbaka: avsluta" else text
        statusView.visibility = View.VISIBLE
    }

    private fun retryAll() {
        fatalShown = false
        retries = 0
        failovers = 0
        excludedPortals.clear()
        statusView.visibility = View.GONE
        requestPlay()
    }

    // ---------------------------------------------------------------- fjärrkontroll

    override fun dispatchKeyEvent(event: KeyEvent): Boolean {
        if (event.action == KeyEvent.ACTION_DOWN) {
            val code = event.keyCode
            val ok = code == KeyEvent.KEYCODE_DPAD_CENTER || code == KeyEvent.KEYCODE_ENTER ||
                code == KeyEvent.KEYCODE_NUMPAD_ENTER
            if (fatalShown && ok) {
                retryAll()
                return true
            }
            if (type == "live") {
                when (code) {
                    KeyEvent.KEYCODE_DPAD_UP, KeyEvent.KEYCODE_CHANNEL_UP, KeyEvent.KEYCODE_PAGE_UP -> {
                        zap(1)
                        return true
                    }
                    KeyEvent.KEYCODE_DPAD_DOWN, KeyEvent.KEYCODE_CHANNEL_DOWN, KeyEvent.KEYCODE_PAGE_DOWN -> {
                        zap(-1)
                        return true
                    }
                    KeyEvent.KEYCODE_DPAD_CENTER, KeyEvent.KEYCODE_ENTER, KeyEvent.KEYCODE_INFO -> {
                        showOverlay(6_000)
                        if (code != KeyEvent.KEYCODE_INFO) loadEpg()
                        return true
                    }
                }
            }
        }
        return super.dispatchKeyEvent(event)
    }

    private fun moveTo(index: Int) {
        queueIndex = index
        itemId = queueIds[index]
        itemName = queueNames.getOrNull(index) ?: itemId
        ext = queueExts.getOrNull(index)
    }

    private fun zap(delta: Int) {
        if (queueIds.size < 2) return
        val size = queueIds.size
        val start = if (queueIndex in queueIds.indices) queueIndex else 0
        moveTo(((start + delta) % size + size) % size)
        titleView.text = itemName
        epgView.text = ""
        showOverlay(3_000)
        zapTask?.let { handler.removeCallbacks(it) }
        val task = Runnable {
            // Stäng gamla strömmen innan den nya begärs: aldrig två samtidigt hos Toucan.
            releasePlayer()
            retries = 0
            failovers = 0
            stalls = 0
            stallMsTotal = 0L
            excludedPortals.clear()
            fatalShown = false
            requestPlay()
        }
        zapTask = task
        handler.postDelayed(task, 650)
    }

    private fun showOverlay(durationMs: Long) {
        overlay.visibility = View.VISIBLE
        handler.removeCallbacks(hideOverlay)
        handler.postDelayed(hideOverlay, durationMs)
    }

    private fun loadEpg() {
        val id = itemId
        Api.arr("/device/epg/${Api.enc(id)}") { result ->
            if (id != itemId) return@arr
            result.onSuccess { array ->
                val now = System.currentTimeMillis() / 1000
                val lines = ArrayList<String>()
                for (i in 0 until array.length()) {
                    if (lines.size == 2) break
                    val p = array.optJSONObject(i) ?: continue
                    val startTs = p.optLong("start")
                    val endTs = p.optLong("end")
                    if (endTs in 1 until now) continue
                    val label = if (startTs <= now) "Nu" else "Sedan"
                    lines.add("$label  ${timeFormat.format(Date(startTs * 1000))}–${timeFormat.format(Date(endTs * 1000))}  ${p.optString("title")}")
                }
                if (lines.isNotEmpty()) epgView.text = lines.joinToString("\n")
            }
        }
    }
}
