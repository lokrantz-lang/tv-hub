package se.tvhub.box

import android.content.Context
import android.net.Uri
import android.os.Handler
import android.os.Looper
import androidx.mediarouter.media.MediaRouteSelector
import androidx.mediarouter.media.MediaRouter
import com.google.android.gms.cast.CastMediaControlIntent
import com.google.android.gms.cast.MediaInfo
import com.google.android.gms.cast.MediaLoadRequestData
import com.google.android.gms.cast.MediaMetadata
import com.google.android.gms.cast.MediaSeekOptions
import com.google.android.gms.cast.framework.CastContext
import com.google.android.gms.cast.framework.CastOptions
import com.google.android.gms.cast.framework.CastSession
import com.google.android.gms.cast.framework.OptionsProvider
import com.google.android.gms.cast.framework.SessionManagerListener
import com.google.android.gms.cast.framework.SessionProvider
import com.google.android.gms.common.images.WebImage

/** Talar om för Googles Cast-bibliotek vilken mottagare som används: Googles vanliga mediespelare. */
class CastOptionsProvider : OptionsProvider {
    override fun getCastOptions(context: Context): CastOptions =
        CastOptions.Builder()
            .setReceiverApplicationId(CastMediaControlIntent.DEFAULT_MEDIA_RECEIVER_APPLICATION_ID)
            .build()

    override fun getAdditionalSessionProviders(context: Context): List<SessionProvider>? = null
}

/**
 * Chromecast från telefonen, för filmer och avsnitt. Chromecasten hämtar videon direkt från
 * Toucan; telefonen styr och håller sessionen hos TV Hub vid liv.
 *
 * Begränsningar: Googles vanliga mottagare spelar MP4 (och ofta MKV på nyare Google TV),
 * inte direktsänd TV från vanliga http-adresser. Utan Google Play-tjänster (vissa boxar) finns
 * ingen Cast-knapp alls.
 */
object Cast {
    private val main = Handler(Looper.getMainLooper())
    private val appId = CastMediaControlIntent.DEFAULT_MEDIA_RECEIVER_APPLICATION_ID
    private var listener: SessionManagerListener<CastSession>? = null

    data class Device(val name: String, val route: MediaRouter.RouteInfo)

    /** CastContext, eller null om Google Play-tjänsterna saknas. Bara från huvudtråden. */
    private fun context(ctx: Context): CastContext? = try {
        @Suppress("DEPRECATION")
        CastContext.getSharedInstance(ctx.applicationContext)
    } catch (_: Throwable) {
        null
    }

    fun available(ctx: Context): Boolean = FormFactor.isMobile(ctx) && context(ctx) != null

    fun session(ctx: Context): CastSession? = try {
        context(ctx)?.sessionManager?.currentCastSession
    } catch (_: Throwable) {
        null
    }

    /** Letar efter Chromecast-enheter i nätet i ett par sekunder och lämnar listan. */
    fun discover(ctx: Context, done: (List<Device>) -> Unit) {
        try {
            val router = MediaRouter.getInstance(ctx.applicationContext)
            val selector = MediaRouteSelector.Builder()
                .addControlCategory(CastMediaControlIntent.categoryForCast(appId))
                .build()
            val callback = object : MediaRouter.Callback() {}
            router.addCallback(selector, callback, MediaRouter.CALLBACK_FLAG_PERFORM_ACTIVE_SCAN)
            main.postDelayed({
                val devices = router.routes
                    .filter { !it.isDefault && it.isEnabled && it.matchesSelector(selector) }
                    .map { Device(it.name.toString(), it) }
                router.removeCallback(callback)
                done(devices)
            }, 3_000)
        } catch (_: Throwable) {
            done(emptyList())
        }
    }

    /** Ansluter till enheten. onStarted får sessionen när Chromecasten är redo. */
    fun connect(ctx: Context, device: Device, onStarted: (CastSession) -> Unit, onEnded: () -> Unit, onFailed: () -> Unit) {
        val cast = context(ctx) ?: return onFailed()
        listener?.let { cast.sessionManager.removeSessionManagerListener(it, CastSession::class.java) }
        val l = object : SessionManagerListener<CastSession> {
            override fun onSessionStarting(session: CastSession) {}
            override fun onSessionStarted(session: CastSession, sessionId: String) = onStarted(session)
            override fun onSessionStartFailed(session: CastSession, error: Int) = onFailed()
            override fun onSessionEnding(session: CastSession) {}
            override fun onSessionEnded(session: CastSession, error: Int) = onEnded()
            override fun onSessionResuming(session: CastSession, sessionId: String) {}
            override fun onSessionResumed(session: CastSession, wasSuspended: Boolean) = onStarted(session)
            override fun onSessionResumeFailed(session: CastSession, error: Int) = onFailed()
            override fun onSessionSuspended(session: CastSession, reason: Int) {}
        }
        listener = l
        cast.sessionManager.addSessionManagerListener(l, CastSession::class.java)
        try {
            MediaRouter.getInstance(ctx.applicationContext).selectRoute(device.route)
        } catch (_: Throwable) {
            onFailed()
        }
    }

    fun contentType(ext: String?, url: String): String = when ((ext ?: url.substringAfterLast('.', "")).lowercase()) {
        "mkv" -> "video/x-matroska"
        "m3u8" -> "application/x-mpegurl"
        "ts" -> "video/mp2t"
        "webm" -> "video/webm"
        "avi" -> "video/x-msvideo"
        else -> "video/mp4"
    }

    /** Skickar filmen till Chromecasten. done(true) när den har börjat, done(false) om den vägrar. */
    fun load(session: CastSession, url: String, ext: String?, title: String, image: String?, positionMs: Long,
             done: (Boolean) -> Unit) {
        val client = session.remoteMediaClient ?: return done(false)
        val meta = MediaMetadata(MediaMetadata.MEDIA_TYPE_MOVIE)
        meta.putString(MediaMetadata.KEY_TITLE, title)
        if (!image.isNullOrBlank()) meta.addImage(WebImage(Uri.parse(image)))
        val info = MediaInfo.Builder(url)
            .setStreamType(MediaInfo.STREAM_TYPE_BUFFERED)
            .setContentType(contentType(ext, url))
            .setMetadata(meta)
            .build()
        val request = MediaLoadRequestData.Builder()
            .setMediaInfo(info)
            .setAutoplay(true)
            .setCurrentTime(positionMs)
            .build()
        try {
            client.load(request).setResultCallback { result -> done(result.status.isSuccess) }
        } catch (_: Throwable) {
            done(false)
        }
    }

    fun deviceName(session: CastSession?): String = session?.castDevice?.friendlyName ?: "Chromecast"

    fun togglePause(ctx: Context) {
        val client = session(ctx)?.remoteMediaClient ?: return
        if (client.isPlaying) client.pause() else client.play()
    }

    fun isPlaying(ctx: Context): Boolean = session(ctx)?.remoteMediaClient?.isPlaying ?: false

    fun seekBy(ctx: Context, deltaMs: Long) {
        val client = session(ctx)?.remoteMediaClient ?: return
        val target = (client.approximateStreamPosition + deltaMs).coerceAtLeast(0)
        client.seek(MediaSeekOptions.Builder().setPosition(target).build())
    }

    /** (position, längd) i millisekunder, eller null. */
    fun position(ctx: Context): Pair<Long, Long>? {
        val client = session(ctx)?.remoteMediaClient ?: return null
        return client.approximateStreamPosition to client.streamDuration
    }

    /** Spelaren stängs: sluta lyssna på sessionen (annars hålls den gamla spelaren kvar i minnet). */
    fun release(ctx: Context) {
        val l = listener ?: return
        listener = null
        try {
            context(ctx)?.sessionManager?.removeSessionManagerListener(l, CastSession::class.java)
        } catch (_: Throwable) {
        }
    }

    fun end(ctx: Context) {
        try {
            context(ctx)?.sessionManager?.endCurrentSession(true)
        } catch (_: Throwable) {
        }
    }
}
