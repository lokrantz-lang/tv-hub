package se.tvhub.box

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.widget.Toast
import androidx.core.content.FileProvider
import java.io.File

/**
 * Reserv för filer i Mina media som boxens egen avkodning inte klarar (t.ex. gamla DivX/Xvid-filmer):
 * de öppnas i VLC-appen, som har egen avkodning. Filer som en gång krävt VLC öppnas där direkt nästa gång.
 */
object ExternalPlayer {
    private const val VLC = "org.videolan.vlc"

    fun vlcInstalled(context: Context): Boolean = try {
        context.packageManager.getPackageInfo(VLC, 0)
        true
    } catch (_: Exception) {
        false
    }

    private fun prefs(context: Context) = context.getSharedPreferences("media", Context.MODE_PRIVATE)

    fun needsVlc(context: Context, url: String): Boolean = prefs(context).getBoolean("vlc:$url", false)

    fun markNeedsVlc(context: Context, url: String) {
        prefs(context).edit().putBoolean("vlc:$url", true).apply()
    }

    private fun shareable(path: String): Boolean = try {
        val real = File(path).canonicalPath
        real.startsWith("/storage/") || real.startsWith("/mnt/")
    } catch (_: Exception) {
        false
    }

    /** Öppnar filen (sökväg eller http-adress) i VLC. false om VLC saknas eller inte kunde startas. */
    fun openInVlc(activity: Activity, url: String, title: String, positionMs: Long = 0L): Boolean {
        if (!vlcInstalled(activity)) return false
        val uri = if (url.startsWith("/")) {
            // Bara filer på USB/lagring delas med VLC, aldrig appens egna privata filer.
            if (!shareable(url)) return false
            try {
                FileProvider.getUriForFile(activity, "${activity.packageName}.files", File(url))
            } catch (_: Exception) {
                return false
            }
        } else {
            Uri.parse(url)
        }
        val intent = Intent(Intent.ACTION_VIEW)
            .setDataAndType(uri, "video/*")
            .setPackage(VLC)
            .putExtra("title", title)
            .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        if (positionMs > 0) intent.putExtra("position", positionMs)
        return try {
            activity.startActivity(intent)
            Toast.makeText(activity, "Spelas i VLC (boxen kan inte avkoda filen själv).", Toast.LENGTH_LONG).show()
            true
        } catch (_: Exception) {
            false
        }
    }
}
