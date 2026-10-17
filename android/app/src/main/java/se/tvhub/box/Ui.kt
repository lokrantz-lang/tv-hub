package se.tvhub.box

import android.os.SystemClock
import android.view.View
import androidx.recyclerview.widget.RecyclerView
import java.net.HttpURLConnection
import java.net.URL

/** Hjälpfunktioner för fokus i listor. */
object Focus {
    fun position(list: RecyclerView, position: Int) {
        val count = list.adapter?.itemCount ?: 0
        if (count == 0 || position < 0) return
        val target = position.coerceAtMost(count - 1)
        list.scrollToPosition(target)
        list.post {
            val view = list.findViewHolderForAdapterPosition(target)?.itemView
            if (view != null) view.requestFocus() else list.requestFocus()
        }
    }

    fun first(list: RecyclerView) {
        val adapter = list.adapter as? RowAdapter
        val index = adapter?.rows?.indexOfFirst { !it.header } ?: 0
        position(list, if (index < 0) 0 else index)
    }

    fun focusedPosition(list: RecyclerView): Int {
        val child = list.focusedChild ?: return RecyclerView.NO_POSITION
        return list.getChildAdapterPosition(child)
    }

    /** Byter innehåll men behåller fokus på ungefär samma rad. */
    fun submitKeeping(list: RecyclerView, adapter: RowAdapter, rows: List<Row>) {
        val focused = focusedPosition(list)
        adapter.submit(rows)
        if (focused != RecyclerView.NO_POSITION && rows.isNotEmpty()) position(list, focused)
    }

    fun contains(list: RecyclerView, view: View?): Boolean =
        view != null && list.findContainingItemView(view) != null
}

/** Mäter hastigheten mellan boxen och Home Assistant (hemnätet). */
object SpeedTest {
    fun runBlocking(prefs: Prefs, megabytes: Int): Double {
        val url = URL(prefs.server.trimEnd('/') + "/device/speedtest?mb=$megabytes")
        val conn = url.openConnection() as HttpURLConnection
        conn.setRequestProperty("Authorization", "Bearer ${prefs.token}")
        conn.connectTimeout = 5_000
        conn.readTimeout = 15_000
        try {
            if (conn.responseCode != 200) throw ApiException(conn.responseCode, "Hastighetstestet nekades")
            val buffer = ByteArray(64 * 1024)
            var total = 0L
            val start = SystemClock.elapsedRealtime()
            conn.inputStream.use { input ->
                while (true) {
                    val n = input.read(buffer)
                    if (n < 0) break
                    total += n
                }
            }
            val seconds = ((SystemClock.elapsedRealtime() - start) / 1000.0).coerceAtLeast(0.001)
            return Math.round(total * 8 / seconds / 1_000_000.0 * 10) / 10.0
        } finally {
            conn.disconnect()
        }
    }
}
