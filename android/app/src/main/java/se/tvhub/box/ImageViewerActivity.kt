package se.tvhub.box

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.Gravity
import android.view.KeyEvent
import android.view.View
import android.view.ViewGroup
import android.view.WindowManager
import android.widget.FrameLayout
import android.widget.ImageView
import android.widget.TextView
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * Bilder från Mina media. Vänster/höger: föregående/nästa. OK: starta eller pausa bildspelet.
 * Upp/ner: visa eller dölj texten. Tillbaka: stäng.
 */
class ImageViewerActivity : Activity() {
    companion object {
        /** Bilderna i mappen (sätts precis innan start). */
        var images: List<Media.Entry> = emptyList()

        fun start(context: Context, list: List<Media.Entry>, index: Int, slideshow: Boolean = false) {
            images = list
            context.startActivity(Intent(context, ImageViewerActivity::class.java).putExtra("index", index).putExtra("play", slideshow))
        }

        private const val SLIDE_MS = 6_000L
        private const val SIZE = 1920
    }

    private lateinit var image: ImageView
    private lateinit var caption: TextView
    private val handler = Handler(Looper.getMainLooper())
    private var index = 0
    private var playing = false
    private val dateFormat = SimpleDateFormat("d MMMM yyyy", Locale("sv", "SE"))
    private val next = object : Runnable {
        override fun run() {
            if (!playing) return
            show((index + 1) % images.size)
            handler.postDelayed(this, SLIDE_MS)
        }
    }
    private val hideCaption = Runnable { caption.visibility = View.GONE }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        Prefs(this).touch()
        if (images.isEmpty()) {
            finish()
            return
        }
        val root = FrameLayout(this)
        root.setBackgroundColor(Color.BLACK)
        image = ImageView(this)
        image.scaleType = ImageView.ScaleType.FIT_CENTER
        root.addView(image, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
        caption = TextView(this)
        caption.setTextColor(Color.WHITE)
        caption.textSize = 16f
        caption.setBackgroundColor(Color.parseColor("#99000000"))
        val pad = Look.dp(this, 16)
        caption.setPadding(pad * 2, pad, pad * 2, pad)
        root.addView(caption, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.BOTTOM))
        setContentView(root)
        show(intent.getIntExtra("index", 0).coerceIn(0, images.size - 1))
        if (intent.getBooleanExtra("play", false)) toggle()
    }

    override fun onDestroy() {
        handler.removeCallbacksAndMessages(null)
        super.onDestroy()
    }

    override fun onStop() {
        super.onStop()
        playing = false
        handler.removeCallbacks(next)
    }

    private fun url(e: Media.Entry) = e.viewUrl ?: e.url

    private fun show(i: Int) {
        index = i
        val e = images[i]
        ImageLoader.load(image, url(e), SIZE, fade = true)
        // Nästa och föregående i förväg, så att bläddringen går fort.
        images.getOrNull(i + 1)?.let { ImageLoader.prefetch(url(it), SIZE) }
        images.getOrNull(i - 1)?.let { ImageLoader.prefetch(url(it), SIZE) }
        val date = e.date?.let { dateFormat.format(Date(it * 1000)) }
        caption.text = listOfNotNull(e.name, date, "${i + 1} av ${images.size}", if (playing) "▶ Bildspel" else null)
            .joinToString("   ·   ") + "\n" + "◀ ▶ bläddra   ·   OK: " + (if (playing) "pausa bildspelet" else "bildspel") + "   ·   Tillbaka: stäng"
        caption.visibility = View.VISIBLE
        handler.removeCallbacks(hideCaption)
        handler.postDelayed(hideCaption, 4_000)
    }

    private fun toggle() {
        playing = !playing
        handler.removeCallbacks(next)
        if (playing) handler.postDelayed(next, SLIDE_MS)
        show(index)
    }

    override fun dispatchKeyEvent(event: KeyEvent): Boolean {
        if (event.action != KeyEvent.ACTION_DOWN) return super.dispatchKeyEvent(event)
        Prefs(this).touch()
        when (event.keyCode) {
            KeyEvent.KEYCODE_DPAD_RIGHT, KeyEvent.KEYCODE_MEDIA_FAST_FORWARD, KeyEvent.KEYCODE_CHANNEL_UP -> {
                if (index + 1 < images.size) show(index + 1) else show(0)
                restartTimer()
            }
            KeyEvent.KEYCODE_DPAD_LEFT, KeyEvent.KEYCODE_MEDIA_REWIND, KeyEvent.KEYCODE_CHANNEL_DOWN -> {
                show(if (index > 0) index - 1 else images.size - 1)
                restartTimer()
            }
            KeyEvent.KEYCODE_DPAD_CENTER, KeyEvent.KEYCODE_ENTER, KeyEvent.KEYCODE_NUMPAD_ENTER,
            KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE, KeyEvent.KEYCODE_SPACE -> toggle()
            KeyEvent.KEYCODE_DPAD_UP, KeyEvent.KEYCODE_DPAD_DOWN, KeyEvent.KEYCODE_INFO -> {
                if (caption.visibility == View.VISIBLE) caption.visibility = View.GONE else show(index)
            }
            else -> return super.dispatchKeyEvent(event)
        }
        return true
    }

    private fun restartTimer() {
        if (!playing) return
        handler.removeCallbacks(next)
        handler.postDelayed(next, SLIDE_MS)
    }
}
