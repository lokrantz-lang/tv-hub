package se.tvhub.box

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.os.Handler
import android.os.Looper
import android.util.LruCache
import android.widget.ImageView
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.Executors

/**
 * Enkel bildladdare med minnescache. Bilderna skalas ner till ungefär den storlek de visas i:
 * affischer och loggor 320 px, bakgrundsbilder högst 960 px. Bilder som laddas tonas in.
 */
object ImageLoader {
    private const val MAX_SIDE = 320
    const val BACKDROP = 960
    private val cache = object : LruCache<String, Bitmap>(cacheBytes()) {
        override fun sizeOf(key: String, value: Bitmap): Int = value.byteCount
    }
    private val failed = HashSet<String>()
    private val pool = Executors.newFixedThreadPool(3)
    private val main = Handler(Looper.getMainLooper())

    private fun cacheBytes(): Int {
        val max = Runtime.getRuntime().maxMemory()
        return (max / 8).coerceIn(16L * 1024 * 1024, 64L * 1024 * 1024).toInt()
    }

    fun load(view: ImageView, url: String?, maxSide: Int = MAX_SIDE, fade: Boolean = false, onDone: ((Boolean) -> Unit)? = null) {
        view.tag = url
        view.setImageDrawable(null)
        if (url.isNullOrBlank() || !(url.startsWith("http://") || url.startsWith("https://"))) {
            onDone?.invoke(false)
            return
        }
        val key = "$maxSide|$url"
        val cached = cache.get(key)
        if (cached != null) {
            view.alpha = 1f
            view.setImageBitmap(cached)
            onDone?.invoke(true)
            return
        }
        synchronized(failed) {
            if (url in failed) {
                onDone?.invoke(false)
                return
            }
        }
        pool.execute {
            val bitmap = try {
                fetch(url, maxSide)
            } catch (_: Throwable) {
                null
            }
            if (bitmap != null) cache.put(key, bitmap) else synchronized(failed) { failed.add(url) }
            main.post {
                if (view.tag != url) return@post
                if (bitmap != null) {
                    view.setImageBitmap(bitmap)
                    if (fade && !Look.lite) {
                        view.alpha = 0f
                        view.animate().alpha(1f).setDuration(220).start()
                    } else {
                        view.alpha = 1f
                    }
                }
                onDone?.invoke(bitmap != null)
            }
        }
    }

    /** Hämtar i förväg så att nästa rad visas direkt. */
    fun prefetch(url: String?, maxSide: Int = MAX_SIDE) {
        if (url.isNullOrBlank() || cache.get("$maxSide|$url") != null) return
        pool.execute {
            try {
                fetch(url, maxSide)?.let { cache.put("$maxSide|$url", it) }
            } catch (_: Throwable) {
            }
        }
    }

    private fun fetch(url: String, maxSide: Int): Bitmap? {
        val conn = URL(url).openConnection() as HttpURLConnection
        conn.connectTimeout = 5_000
        conn.readTimeout = 8_000
        conn.instanceFollowRedirects = true
        try {
            if (conn.responseCode != 200) return null
            val bytes = conn.inputStream.use { it.readBytes() }
            val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
            BitmapFactory.decodeByteArray(bytes, 0, bytes.size, bounds)
            if (bounds.outWidth <= 0 || bounds.outHeight <= 0) return null
            var sample = 1
            while (bounds.outWidth / (sample * 2) >= maxSide || bounds.outHeight / (sample * 2) >= maxSide) sample *= 2
            val options = BitmapFactory.Options().apply {
                inSampleSize = sample
                inPreferredConfig = if (maxSide > MAX_SIDE) Bitmap.Config.RGB_565 else Bitmap.Config.ARGB_8888
            }
            return BitmapFactory.decodeByteArray(bytes, 0, bytes.size, options)
        } finally {
            conn.disconnect()
        }
    }
}
