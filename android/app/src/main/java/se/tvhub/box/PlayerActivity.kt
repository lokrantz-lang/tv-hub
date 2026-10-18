package se.tvhub.box

import android.app.Activity
import android.app.ActivityManager
import android.app.AlertDialog
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.view.KeyEvent
import android.view.View
import android.view.WindowManager
import android.widget.ProgressBar
import android.widget.TextView
import android.widget.Toast
import androidx.media3.common.C
import androidx.media3.common.Format as MediaFormat
import androidx.media3.common.MediaItem
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import androidx.media3.common.TrackSelectionOverride
import androidx.media3.common.util.UnstableApi
import androidx.media3.datasource.DefaultDataSource
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
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * Uppspelning.
 *  - Frågar TV Hub om strömadress (anslutningsvakten kan svara "en annan box tittar").
 *  - Skickar heartbeat med mätvärden och position var tionde sekund. Positionen ger "Fortsätt titta".
 *  - Rapporterar hack och fel så att webbadminen kan säga var problemet ligger.
 *  - Försöker igen en gång, byter sedan portal automatiskt.
 *  - Live: upp/ner byter kanal, vänster (eller 0) går till förra kanalen, OK två gånger ger val.
 *  - Film och avsnitt startar där du slutade. Nästa avsnitt spelas automatiskt.
 *  - Timeshift i live: paus, spola bakåt och framåt. Kanaler med arkiv hos Toucan spolas via
 *    catch-up (så långt arkivet räcker). Övriga kan pausas så länge bufferten i boxen räcker.
 *  - Inspelningar (type "file") spelas från USB-minnet, även medan de spelas in.
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
            startMs: Long = 0L,
            seriesId: String? = null,
            icon: String? = null,
            catchupStart: Long = 0L,
            catchupMinutes: Int = 0,
        ) {
            val intent = Intent(context, PlayerActivity::class.java)
                .putExtra("type", type)
                .putExtra("id", id)
                .putExtra("name", name)
                .putExtra("ext", ext)
                .putExtra("queue_index", queueIndex)
                .putExtra("start_ms", startMs)
                .putExtra("series_id", seriesId)
                .putExtra("icon", icon)
                .putExtra("catchup_start", catchupStart)
                .putExtra("catchup_minutes", catchupMinutes)
            if (queueIds != null) intent.putStringArrayListExtra("queue_ids", queueIds)
            if (queueNames != null) intent.putStringArrayListExtra("queue_names", queueNames)
            if (queueExts != null) intent.putStringArrayListExtra("queue_exts", queueExts)
            context.startActivity(intent)
        }

        private const val DEFAULT_HEARTBEAT_MS = 10_000L
        private const val STALL_REPORT_MIN_MS = 700L
        private const val SEEK_STEP_MS = 60_000L
        private const val LIVE_EDGE_MS = 30_000L
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
    private var seriesId: String? = null
    private var icon: String? = null
    private var startMs = 0L
    private var catchupStart = 0L
    private var catchupMinutes = 0
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
    private var lastPositionMs = 0L

    // Timeshift (live)
    private var archiveDays = 0
    private var shiftStart = 0L        // > 0: spelar från arkivet; väggklockans sekund där catch-up-strömmen börjar
    private var shiftOffsetMs = 0L     // var i catch-up-strömmen uppspelningen ska börja
    private var pausedAt = 0L          // väggklocka (ms) när live pausades
    private var behindMs = 0L          // hur långt efter direktsändningen bufferten ligger
    private var seekTargetMs = 0L      // väggklocka (ms) som spolningen siktar på
    private var seekTask: Runnable? = null
    private var currentProgram: JSONObject? = null
    private lateinit var shiftView: TextView

    private val hideOverlay = Runnable { overlay.visibility = View.GONE }

    private val heartbeat = object : Runnable {
        override fun run() {
            val sid = sessionId ?: return
            prefs.touch()  // vuxenläget ska inte gå ut mitt i en film
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
        shiftView = findViewById(R.id.shift)
        bandwidthMeter = DefaultBandwidthMeter.getSingletonInstance(this)

        type = intent.getStringExtra("type") ?: "live"
        itemId = intent.getStringExtra("id") ?: ""
        itemName = intent.getStringExtra("name") ?: ""
        ext = intent.getStringExtra("ext")
        seriesId = intent.getStringExtra("series_id")
        icon = intent.getStringExtra("icon")
        startMs = intent.getLongExtra("start_ms", 0L)
        catchupStart = intent.getLongExtra("catchup_start", 0L)
        catchupMinutes = intent.getIntExtra("catchup_minutes", 0)
        queueIds = intent.getStringArrayListExtra("queue_ids") ?: emptyList()
        queueNames = intent.getStringArrayListExtra("queue_names") ?: emptyList()
        queueExts = intent.getStringArrayListExtra("queue_exts") ?: emptyList()
        queueIndex = intent.getIntExtra("queue_index", -1)
        if (itemId.isEmpty()) {
            finish()
            return
        }
        if (type == "file") playFile() else requestPlay()
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
        seriesId?.let { body.put("series_id", it) }
        icon?.let { body.put("icon", it) }
        if (type == "catchup") body.put("start", catchupStart).put("duration", catchupMinutes)
        if (type == "live" && shiftStart > 0) {
            val minutes = ((System.currentTimeMillis() / 1000 - shiftStart) / 60 + 2).toInt().coerceIn(1, 600)
            body.put("type", "catchup").put("start", shiftStart).put("duration", minutes)
        }
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
                if (shiftStart == 0L) itemName = response.optStringOrNull("name") ?: itemName
                titleView.text = itemName
                if (type == "live" && shiftStart == 0L) archiveDays = response.optInt("archive_days", archiveDays)
                epgView.text = if (portalName.isNotEmpty()) "via $portalName" else ""
                fatalShown = false
                statusView.visibility = View.GONE
                if (type == "live") rememberChannel()
                // Efter portalbyte: fortsätt där strömmen bröts.
                val position = when {
                    type == "live" && shiftStart > 0 -> shiftOffsetMs
                    lastPositionMs > 0 && type != "live" -> lastPositionMs
                    else -> startMs
                }
                startPlayer(response.getString("url"), position)
                startMs = 0L
                showOverlay(4_000)
                updateShiftView()
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

    private fun rememberChannel() {
        val current = itemId to itemName
        if (Session.currentChannel?.first != current.first) {
            Session.previousChannel = Session.currentChannel
            Session.currentChannel = current
        }
    }

    // ---------------------------------------------------------------- spelare

    /** Hur mycket boxen kan buffra när live pausas, i byte. Z Alpha har mindre minne än Z10 Pro. */
    private fun pauseBufferBytes(): Int {
        val am = getSystemService(Context.ACTIVITY_SERVICE) as ActivityManager
        val heapMb = am.largeMemoryClass.coerceAtLeast(128)
        return (heapMb.toLong() * 1024 * 1024 / 3).coerceAtMost(400L * 1024 * 1024).toInt()
    }

    private fun startPlayer(url: String, positionMs: Long) {
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
        val directLive = type == "live" && shiftStart == 0L
        val loadControl = if (directLive) {
            // Paus i direktsändning: fortsätt ta emot strömmen i bufferten så länge minnet räcker.
            DefaultLoadControl.Builder()
                .setBufferDurationsMs(15_000, 30 * 60_000, 2_500, 5_000)
                .setTargetBufferBytes(pauseBufferBytes())
                .setPrioritizeTimeOverSizeThresholds(false)
                .build()
        } else {
            DefaultLoadControl.Builder()
                .setBufferDurationsMs(15_000, 50_000, 2_500, 5_000)
                .build()
        }
        val renderers = DefaultRenderersFactory(this).setEnableDecoderFallback(true)
        val newPlayer = ExoPlayer.Builder(this, renderers)
            .setMediaSourceFactory(DefaultMediaSourceFactory(DefaultDataSource.Factory(this, http), extractors))
            .setLoadControl(loadControl)
            .setBandwidthMeter(bandwidthMeter)
            .build()
        // Senast valda ljudspråk och undertexter gäller även nästa gång.
        val subtitles = prefs.subtitleLanguage
        newPlayer.trackSelectionParameters = newPlayer.trackSelectionParameters.buildUpon()
            .setPreferredAudioLanguage(prefs.audioLanguage.ifEmpty { null })
            .setPreferredTextLanguage(if (subtitles == "off" || subtitles.isEmpty()) null else subtitles)
            .setTrackTypeDisabled(C.TRACK_TYPE_TEXT, subtitles == "off")
            .build()
        newPlayer.addListener(listener)
        playerView.player = newPlayer
        val live = type == "live"
        playerView.useController = !live
        playerView.isFocusable = !live
        if (!live) playerView.requestFocus()
        if (positionMs > 0 && (type == "vod" || type == "episode" || type == "file" || shiftStart > 0)) {
            newPlayer.setMediaItem(MediaItem.fromUri(url), positionMs)
        } else {
            newPlayer.setMediaItem(MediaItem.fromUri(url))
        }
        newPlayer.playWhenReady = true
        newPlayer.prepare()
        player = newPlayer
        handler.removeCallbacks(heartbeat)
        handler.postDelayed(heartbeat, heartbeatMs)
    }

    private fun releasePlayer() {
        handler.removeCallbacks(heartbeat)
        player?.let {
            if (type != "live" && it.currentPosition > 0) lastPositionMs = it.currentPosition
            if (type == "file" && it.currentPosition > 0) saveFilePosition(it)
            it.removeListener(listener)
            it.release()
        }
        player = null
        playerView.player = null
    }

    private fun stopSession(reason: String, finalMetrics: JSONObject? = null) {
        val sid = sessionId ?: return
        sessionId = null
        val body = JSONObject().put("session_id", sid).put("reason", reason).put("metrics", finalMetrics ?: metrics())
        Api.fire("/device/stop", body)
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
            if (type != "live" && p.duration > 0) o.put("duration_ms", p.duration)
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
        if (type == "file") {
            releasePlayer()
            showFatal("Inspelningen går inte att spela.\n${describe(error)}")
            return
        }
        if (error.errorCode == PlaybackException.ERROR_CODE_BEHIND_LIVE_WINDOW && p != null) {
            p.seekToDefaultPosition()
            p.prepare()
            return
        }
        Api.report(
            "error", sessionId,
            metrics().put("code", error.errorCodeName).put("message", error.message ?: ""),
        )
        if (type == "live" && shiftStart == 0L) {
            // En ny anslutning hamnar i direktsändningen; en paus i bufferten går förlorad.
            pausedAt = 0L
            behindMs = 0L
        }
        if (type == "live" && shiftStart > 0 && retries < 1 && p != null) {
            // Arkivströmmen bröts: hämta en ny från där vi var.
            retries++
            val wall = watchedWallMs()
            shiftStart = wall / 60_000 * 60
            shiftOffsetMs = wall - shiftStart * 1000
            pausedAt = 0L
            releasePlayer()
            requestPlay()
            return
        }
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
        if (type == "file") {
            val p = player
            if (RecordService.isRecording(itemId) && p != null) {
                // Inspelningen pågår: fortsätt när mer har skrivits till filen.
                val position = p.currentPosition
                statusView.text = "Ikapp inspelningen…"
                statusView.visibility = View.VISIBLE
                handler.postDelayed({
                    if (!closing) {
                        statusView.visibility = View.GONE
                        startPlayer(Uri.fromFile(File(itemId)).toString(), (position - 2_000).coerceAtLeast(0))
                    }
                }, 5_000)
                return
            }
            prefs.setRecordingPosition(itemId, 0L)
            finish()
            return
        }
        if (type == "live" && shiftStart > 0) {
            // Slutet av den hämtade arkivbiten. Ligger vi fortfarande efter: hämta nästa bit
            // därifrån. Annars (eller om arkivet inte hunnit längre) fortsätt i direktsändningen.
            val p = player
            val wall = watchedWallMs()
            val played = (p?.currentPosition ?: 0L) - shiftOffsetMs
            if (System.currentTimeMillis() - wall > 2 * 60_000L && played > 30_000L) shiftTo(wall) else goLive()
            return
        }
        if (type == "live") {
            // En liveström ska inte ta slut. Behandla som avbrott.
            releasePlayer()
            statusView.text = "Strömmen bröts, ansluter igen…"
            statusView.visibility = View.VISIBLE
            handler.postDelayed({ requestPlay() }, 1_000)
            return
        }
        // Markera som färdigsedd innan nästa startar.
        val final = metrics()
        player?.duration?.takeIf { it > 0 }?.let { final.put("position_ms", it).put("duration_ms", it) }
        stopSession("ended", final)
        lastPositionMs = 0L
        if (type == "episode" && queueIndex >= 0 && queueIndex + 1 < queueIds.size) {
            moveTo(queueIndex + 1)
            releasePlayer()
            lastPositionMs = 0L
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
        if (type == "file") playFile() else requestPlay()
    }

    // ---------------------------------------------------------------- inspelningar

    private fun playFile() {
        val file = File(itemId)
        titleView.text = itemName
        if (!file.isFile) {
            showFatal("Filen finns inte. Sitter USB-minnet i?", canRetry = true)
            return
        }
        epgView.text = if (RecordService.isRecording(itemId)) "● Spelas in just nu" else "Inspelning"
        val saved = prefs.recordingPosition(itemId)
        startPlayer(Uri.fromFile(file).toString(), saved)
        showOverlay(4_000)
    }

    private fun saveFilePosition(p: ExoPlayer) {
        val duration = p.duration
        val position = p.currentPosition
        val nearEnd = duration > 0 && duration - position < 60_000 && !RecordService.isRecording(itemId)
        prefs.setRecordingPosition(itemId, if (nearEnd) 0L else position)
    }

    private fun recordCurrentProgram() {
        val program = currentProgram
        if (program == null) {
            Toast.makeText(this, "Programguiden saknas för kanalen. Planera i webbadminen i stället.", Toast.LENGTH_LONG).show()
            return
        }
        val title = program.optString("title")
        val body = JSONObject().put("stream_id", itemId).put("title", title)
            .put("start", program.optLong("start")).put("end", program.optLong("end"))
        Api.obj("POST", "/device/recordings", body) { result ->
            result.onSuccess {
                RecScheduler.sync(this)
                Toast.makeText(this, "● Spelar in \"$title\" till USB-minnet.", Toast.LENGTH_LONG).show()
            }.onFailure { Toast.makeText(this, Api.friendly(it), Toast.LENGTH_LONG).show() }
        }
    }

    // ---------------------------------------------------------------- timeshift

    private val shiftTicker = object : Runnable {
        override fun run() {
            updateShiftView()
            if (pausedAt > 0 || shiftStart > 0 || behindMs > 0) handler.postDelayed(this, 1_000)
        }
    }

    /** Väggklockan för det som visas just nu (ms). */
    private fun watchedWallMs(): Long {
        val now = System.currentTimeMillis()
        return when {
            pausedAt > 0 && shiftStart == 0L -> pausedAt - behindMs
            shiftStart > 0 -> shiftStart * 1000 + (player?.currentPosition ?: shiftOffsetMs)
            else -> now - behindMs
        }
    }

    private fun delayMs(): Long = (System.currentTimeMillis() - watchedWallMs()).coerceAtLeast(0)

    private fun updateShiftView() {
        if (type != "live") return
        val delay = delayMs()
        val paused = pausedAt > 0
        if (!paused && delay < LIVE_EDGE_MS && shiftStart == 0L) {
            shiftView.visibility = View.GONE
            return
        }
        val minutes = delay / 60_000
        val behind = if (minutes >= 1) "${Format.minutes(minutes.toInt())} efter direkt" else "strax efter direkt"
        val clock = timeFormat.format(Date(watchedWallMs()))
        val hint = if (archiveDays > 0) "◀◀ ▶▶ spolar   ■ direkt" else "Kanalen saknar arkiv: kan bara pausas"
        shiftView.text = (if (paused) "⏸  Pausad · " else "⏵  ") + "$clock · $behind\n$hint"
        shiftView.visibility = View.VISIBLE
    }

    private fun startTicker() {
        handler.removeCallbacks(shiftTicker)
        handler.post(shiftTicker)
    }

    private fun togglePause(wantPlay: Boolean? = null) {
        val p = player ?: return
        val playing = pausedAt == 0L
        if (wantPlay == true && playing) return
        if (wantPlay == false && !playing) return
        if (playing) {
            p.pause()
            pausedAt = System.currentTimeMillis()
            overlay.visibility = View.VISIBLE
            handler.removeCallbacks(hideOverlay)
            startTicker()
            if (archiveDays == 0 && shiftStart == 0L) {
                val bitrate = (p.videoFormat?.bitrate ?: 0).takeIf { it > 0 } ?: 8_000_000
                val minutes = (pauseBufferBytes().toLong() * 8 / bitrate / 60).coerceAtLeast(1)
                Toast.makeText(this, "Pausad. Kanalen saknar arkiv, så pausen räcker ungefär ${Format.minutes(minutes.toInt())}.", Toast.LENGTH_LONG).show()
            }
            return
        }
        val pausedFor = System.currentTimeMillis() - pausedAt
        val target = pausedAt - behindMs
        pausedAt = 0L
        if (shiftStart == 0L && archiveDays > 0 && pausedFor > p.totalBufferedDuration) {
            // Bufferten räcker inte: fortsätt från arkivet där pausen trycktes.
            shiftTo(target)
            return
        }
        if (shiftStart == 0L) behindMs += pausedFor
        p.play()
        showOverlay(3_000)
        startTicker()
    }

    /** Spola ±delta. Flera tryck i följd läggs ihop och körs efter en kort paus. */
    private fun seekBy(deltaMs: Long) {
        if (archiveDays <= 0) {
            Toast.makeText(this, "Den här kanalen kan inte spolas: Toucan sparar inget arkiv för den. Paus fungerar.", Toast.LENGTH_LONG).show()
            return
        }
        val now = System.currentTimeMillis()
        val base = if (seekTask != null) seekTargetMs else watchedWallMs()
        val oldest = now - archiveDays * 86_400_000L + 60_000
        seekTargetMs = (base + deltaMs).coerceIn(oldest, now)
        val behind = (now - seekTargetMs) / 60_000
        shiftView.text = if (now - seekTargetMs < LIVE_EDGE_MS) "▶▶  Direkt" else
            "${if (deltaMs < 0) "◀◀" else "▶▶"}  ${timeFormat.format(Date(seekTargetMs))} · ${Format.minutes(behind.toInt().coerceAtLeast(1))} efter direkt"
        shiftView.visibility = View.VISIBLE
        overlay.visibility = View.VISIBLE
        handler.removeCallbacks(hideOverlay)
        seekTask?.let { handler.removeCallbacks(it) }
        val task = Runnable {
            seekTask = null
            val target = seekTargetMs
            if (System.currentTimeMillis() - target < LIVE_EDGE_MS) goLive() else shiftTo(target)
        }
        seekTask = task
        handler.postDelayed(task, 900)
    }

    /** Spela från arkivet med start vid väggklockan [targetMs]. */
    private fun shiftTo(targetMs: Long) {
        val p = player
        pausedAt = 0L
        // Redan i arkivet och målet ligger i samma ström: spola direkt om strömmen tillåter det.
        if (shiftStart > 0 && p != null && p.isCurrentMediaItemSeekable) {
            val offset = targetMs - shiftStart * 1000
            val end = if (p.duration > 0) p.duration else p.bufferedPosition
            if (offset in 0..end) {
                p.seekTo(offset)
                p.play()
                showOverlay(3_000)
                startTicker()
                return
            }
        }
        shiftStart = targetMs / 60_000 * 60   // Xtream tar starttid med minutupplösning
        shiftOffsetMs = targetMs - shiftStart * 1000
        behindMs = 0L
        restartStream()
    }

    private fun goLive() {
        seekTask?.let { handler.removeCallbacks(it) }
        seekTask = null
        shiftStart = 0L
        shiftOffsetMs = 0L
        pausedAt = 0L
        behindMs = 0L
        shiftView.visibility = View.GONE
        // Både från arkivet och från en buffrad paus: en ny direktström hamnar på senaste läget.
        restartStream()
    }

    private fun restartStream() {
        releasePlayer()
        retries = 0
        failovers = 0
        excludedPortals.clear()
        fatalShown = false
        requestPlay()
        startTicker()
    }

    private fun resetShift() {
        seekTask?.let { handler.removeCallbacks(it) }
        seekTask = null
        shiftStart = 0L
        shiftOffsetMs = 0L
        pausedAt = 0L
        behindMs = 0L
        archiveDays = 0
        currentProgram = null
        shiftView.visibility = View.GONE
    }

    // ---------------------------------------------------------------- fjärrkontroll

    override fun dispatchKeyEvent(event: KeyEvent): Boolean {
        if (event.action == KeyEvent.ACTION_DOWN) {
            prefs.touch()
            val code = event.keyCode
            val ok = code == KeyEvent.KEYCODE_DPAD_CENTER || code == KeyEvent.KEYCODE_ENTER ||
                code == KeyEvent.KEYCODE_NUMPAD_ENTER
            if (fatalShown && ok) {
                retryAll()
                return true
            }
            if (code == KeyEvent.KEYCODE_MENU) {
                showOptions()
                return true
            }
            if (type == "live") {
                val shifted = pausedAt > 0 || shiftStart > 0 || behindMs > 0
                when (code) {
                    KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE, KeyEvent.KEYCODE_SPACE -> {
                        togglePause()
                        return true
                    }
                    KeyEvent.KEYCODE_MEDIA_PAUSE -> {
                        togglePause(wantPlay = false)
                        return true
                    }
                    KeyEvent.KEYCODE_MEDIA_PLAY -> {
                        togglePause(wantPlay = true)
                        return true
                    }
                    KeyEvent.KEYCODE_MEDIA_REWIND -> {
                        seekBy(-SEEK_STEP_MS)
                        return true
                    }
                    KeyEvent.KEYCODE_MEDIA_FAST_FORWARD -> {
                        seekBy(SEEK_STEP_MS)
                        return true
                    }
                    KeyEvent.KEYCODE_MEDIA_STOP -> {
                        if (shifted) goLive()
                        return true
                    }
                }
                if (shifted) {
                    // I timeshift: vänster/höger spolar och OK fortsätter efter paus.
                    when (code) {
                        KeyEvent.KEYCODE_DPAD_LEFT -> {
                            seekBy(-SEEK_STEP_MS)
                            return true
                        }
                        KeyEvent.KEYCODE_DPAD_RIGHT -> {
                            seekBy(SEEK_STEP_MS)
                            return true
                        }
                        KeyEvent.KEYCODE_DPAD_CENTER, KeyEvent.KEYCODE_ENTER -> if (pausedAt > 0) {
                            togglePause(wantPlay = true)
                            return true
                        }
                    }
                }
                when (code) {
                    KeyEvent.KEYCODE_DPAD_UP, KeyEvent.KEYCODE_CHANNEL_UP, KeyEvent.KEYCODE_PAGE_UP -> {
                        zap(1)
                        return true
                    }
                    KeyEvent.KEYCODE_DPAD_DOWN, KeyEvent.KEYCODE_CHANNEL_DOWN, KeyEvent.KEYCODE_PAGE_DOWN -> {
                        zap(-1)
                        return true
                    }
                    KeyEvent.KEYCODE_DPAD_LEFT, KeyEvent.KEYCODE_LAST_CHANNEL, KeyEvent.KEYCODE_0 -> {
                        previousChannel()
                        return true
                    }
                    KeyEvent.KEYCODE_DPAD_CENTER, KeyEvent.KEYCODE_ENTER, KeyEvent.KEYCODE_INFO -> {
                        if (overlay.visibility == View.VISIBLE && code != KeyEvent.KEYCODE_INFO) {
                            showOptions()
                        } else {
                            showOverlay(6_000)
                            loadEpg()
                        }
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

    /** Byter kanal efter en kort paus, så att man kan bläddra flera steg utan att varje kanal startar. */
    private fun switchSoon() {
        titleView.text = itemName
        epgView.text = ""
        showOverlay(3_000)
        zapTask?.let { handler.removeCallbacks(it) }
        resetShift()
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

    private fun zap(delta: Int) {
        if (queueIds.size < 2) return
        val size = queueIds.size
        val start = if (queueIndex in queueIds.indices) queueIndex else 0
        moveTo(((start + delta) % size + size) % size)
        switchSoon()
    }

    private fun previousChannel() {
        val previous = Session.previousChannel
        if (previous == null || previous.first == itemId) {
            Toast.makeText(this, "Ingen tidigare kanal.", Toast.LENGTH_SHORT).show()
            return
        }
        itemId = previous.first
        itemName = previous.second
        ext = null
        queueIndex = queueIds.indexOf(itemId)
        switchSoon()
    }

    private fun showOverlay(durationMs: Long) {
        overlay.visibility = View.VISIBLE
        handler.removeCallbacks(hideOverlay)
        handler.postDelayed(hideOverlay, durationMs)
    }

    // ---------------------------------------------------------------- val: ljud, undertexter, guide

    private fun showOptions() {
        val options = ArrayList<Pair<String, () -> Unit>>()
        options.add("Ljudspår" to { showTrackDialog(C.TRACK_TYPE_AUDIO) })
        options.add("Undertexter" to { showTrackDialog(C.TRACK_TYPE_TEXT) })
        if (type == "live") {
            val shifted = pausedAt > 0 || shiftStart > 0 || behindMs > 0
            options.add((if (pausedAt > 0) "Fortsätt" else "Pausa") to { togglePause() })
            if (archiveDays > 0) {
                options.add("Spola tillbaka 5 minuter" to { seekBy(-5 * SEEK_STEP_MS) })
                options.add("Spola tillbaka 30 minuter" to { seekBy(-30 * SEEK_STEP_MS) })
                currentProgram?.let { program ->
                    val startMs = program.optLong("start") * 1000
                    if (startMs in 1 until System.currentTimeMillis()) {
                        options.add("Från början av programmet" to { goToProgramStart(startMs) })
                    }
                }
            }
            if (shifted) options.add("Tillbaka till direkt" to { goLive() })
            currentProgram?.let { program ->
                options.add("● Spela in \"${program.optString("title")}\"" to { recordCurrentProgram() })
            }
            Session.previousChannel?.takeIf { it.first != itemId }?.let { prev ->
                options.add("Förra kanalen: ${prev.second}" to { previousChannel() })
            }
        }
        AlertDialog.Builder(this)
            .setTitle(itemName)
            .setItems(options.map { it.first }.toTypedArray()) { _, which -> options[which].second() }
            .show()
    }

    private fun trackLabel(format: MediaFormat, fallback: String): String {
        val language = format.language?.let { code ->
            Locale(code).getDisplayLanguage(Locale("sv", "SE")).replaceFirstChar { it.uppercase() }.ifEmpty { code }
        }
        val channels = when (format.channelCount) {
            1 -> "mono"
            2 -> "stereo"
            6 -> "5.1"
            8 -> "7.1"
            else -> null
        }
        return listOfNotNull(format.label, language, channels).distinct().joinToString(" · ").ifEmpty { fallback }
    }

    private fun showTrackDialog(trackType: Int) {
        val p = player ?: return
        val labels = ArrayList<String>()
        val actions = ArrayList<() -> Unit>()
        if (trackType == C.TRACK_TYPE_TEXT) {
            labels.add("Av")
            actions.add {
                p.trackSelectionParameters = p.trackSelectionParameters.buildUpon()
                    .setTrackTypeDisabled(C.TRACK_TYPE_TEXT, true)
                    .build()
                prefs.subtitleLanguage = "off"
            }
        }
        for (group in p.currentTracks.groups) {
            if (group.type != trackType) continue
            for (i in 0 until group.length) {
                if (!group.isTrackSupported(i)) continue
                val format = group.getTrackFormat(i)
                val selected = group.isTrackSelected(i)
                labels.add(trackLabel(format, "Spår ${labels.size + 1}") + if (selected) "   ✓" else "")
                val mediaGroup = group.mediaTrackGroup
                val language = format.language ?: ""
                actions.add {
                    p.trackSelectionParameters = p.trackSelectionParameters.buildUpon()
                        .setTrackTypeDisabled(trackType, false)
                        .setOverrideForType(TrackSelectionOverride(mediaGroup, i))
                        .build()
                    if (trackType == C.TRACK_TYPE_AUDIO) prefs.audioLanguage = language else prefs.subtitleLanguage = language
                }
            }
        }
        val empty = if (trackType == C.TRACK_TYPE_TEXT) labels.size <= 1 else labels.isEmpty()
        if (empty) {
            Toast.makeText(this, if (trackType == C.TRACK_TYPE_TEXT) "Inga undertexter finns." else "Bara ett ljudspår finns.",
                Toast.LENGTH_SHORT).show()
            return
        }
        AlertDialog.Builder(this)
            .setTitle(if (trackType == C.TRACK_TYPE_AUDIO) "Ljudspår" else "Undertexter")
            .setItems(labels.toTypedArray()) { _, which -> actions[which]() }
            .show()
    }

    private fun goToProgramStart(startMs: Long) {
        seekTask?.let { handler.removeCallbacks(it) }
        seekTask = null
        shiftTo(startMs)
    }

    private fun loadEpg() {
        if (type != "live") return
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
                    if (startTs <= now && lines.isEmpty()) currentProgram = p
                    val label = if (startTs <= now) "Nu" else "Sedan"
                    lines.add("$label  ${timeFormat.format(Date(startTs * 1000))}–${timeFormat.format(Date(endTs * 1000))}  ${p.optString("title")}")
                }
                if (lines.isNotEmpty()) epgView.text = lines.joinToString("\n")
            }
        }
    }
}
