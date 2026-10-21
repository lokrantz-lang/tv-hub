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
        if (url.isNullOrBlank() || !(url.startsWith("http://") || url.startsWith("https://") || url.startsWith("/") || url.startsWith("frame:"))) {
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

    /** Mina media: bilder på USB-disken ("/…") och en bild ur en video ("frame:/…"). */
    var appContext: android.content.Context? = null

    private fun fetch(url: String, maxSide: Int): Bitmap? {
        if (url.startsWith("frame:")) return frame(url.removePrefix("frame:"), maxSide)
        if (url.startsWith("/")) return decode(java.io.File(url).readBytes(), maxSide)
        val conn = URL(url).openConnection() as HttpURLConnection
        conn.connectTimeout = 5_000
        conn.readTimeout = 8_000
        conn.instanceFollowRedirects = true
        try {
            if (conn.responseCode != 200) return null
            val bytes = conn.inputStream.use { it.readBytes() }
            return decode(bytes, maxSide)
        } finally {
            conn.disconnect()
        }
    }

    private fun decode(bytes: ByteArray, maxSide: Int): Bitmap? {
        run {
            val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
            BitmapFactory.decodeByteArray(bytes, 0, bytes.size, bounds)
            if (bounds.outWidth <= 0 || bounds.outHeight <= 0) return null
            var sample = 1
            while (bounds.outWidth / (sample * 2) >= maxSide || bounds.outHeight / (sample * 2) >= maxSide) sample *= 2
            val options = BitmapFactory.Options().apply {
                inSampleSize = sample
                inPreferredConfig = if (maxSide > MAX_SIDE) Bitmap.Config.RGB_565 else Bitmap.Config.ARGB_8888
            }
            val bitmap = BitmapFactory.decodeByteArray(bytes, 0, bytes.size, options) ?: return null
            return rotate(bitmap, bytes)
        }
    }

    /** Foton från mobilen är ofta lagrade på sidan med en EXIF-uppgift om hur de ska vändas. */
    private fun rotate(bitmap: Bitmap, bytes: ByteArray): Bitmap {
        val degrees = try {
            when (android.media.ExifInterface(java.io.ByteArrayInputStream(bytes))
                .getAttributeInt(android.media.ExifInterface.TAG_ORIENTATION, android.media.ExifInterface.ORIENTATION_NORMAL)) {
                android.media.ExifInterface.ORIENTATION_ROTATE_90 -> 90f
                android.media.ExifInterface.ORIENTATION_ROTATE_180 -> 180f
                android.media.ExifInterface.ORIENTATION_ROTATE_270 -> 270f
                else -> 0f
            }
        } catch (_: Exception) {
            0f
        }
        if (degrees == 0f) return bitmap
        val m = android.graphics.Matrix().apply { postRotate(degrees) }
        return Bitmap.createBitmap(bitmap, 0, 0, bitmap.width, bitmap.height, m, true)
    }

    /** En bild en bit in i videon. Sparas på boxen så att den bara görs en gång. */
    private fun frame(path: String, maxSide: Int): Bitmap? {
        val dir = appContext?.cacheDir?.let { java.io.File(it, "frames").also { d -> d.mkdirs() } }
        val cached = dir?.let { java.io.File(it, Integer.toHexString(path.hashCode()) + ".jpg") }
        if (cached != null && cached.isFile) return decode(cached.readBytes(), maxSide)
        val retriever = android.media.MediaMetadataRetriever()
        try {
            retriever.setDataSource(path)
            val durationMs = retriever.extractMetadata(android.media.MediaMetadataRetriever.METADATA_KEY_DURATION)?.toLongOrNull() ?: 0L
            val at = if (durationMs > 0) (durationMs / 10).coerceAtMost(5 * 60_000L) else 10_000L
            val frame = retriever.getFrameAtTime(at * 1000, android.media.MediaMetadataRetriever.OPTION_CLOSEST_SYNC) ?: return null
            val scale = maxSide.toFloat() / maxOf(frame.width, frame.height)
            val small = if (scale < 1f) Bitmap.createScaledBitmap(frame, (frame.width * scale).toInt(), (frame.height * scale).toInt(), true) else frame
            if (cached != null) {
                try {
                    java.io.FileOutputStream(cached).use { small.compress(Bitmap.CompressFormat.JPEG, 82, it) }
                } catch (_: Exception) {
                }
            }
            return small
        } catch (_: Throwable) {
            return null
        } finally {
            try {
                retriever.release()
            } catch (_: Throwable) {
            }
        }
    }
}
