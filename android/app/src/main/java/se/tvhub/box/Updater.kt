package se.tvhub.box

import android.app.Activity
import android.app.AlertDialog
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.provider.Settings
import android.widget.Toast
import androidx.core.content.FileProvider
import org.json.JSONObject
import java.io.File

/**
 * Självuppdatering. Home Assistant hämtar den nya versionen från GitHub och lämnar den
 * till boxen i hemnätet. Android visar sedan sin vanliga installationsruta.
 */
object Updater {

    /** Frågar TV Hub om en nyare version finns. quiet = visa inget om allt är aktuellt. */
    fun check(activity: Activity, prefs: Prefs, quiet: Boolean) {
        // Söker man själv frågar TV Hub GitHub direkt (annars högst var 30:e minut).
        val force = if (quiet) "" else "&force=1"
        Api.obj("GET", "/device/app-update?code=${BuildConfig.VERSION_CODE}$force") { result ->
            if (activity.isFinishing) return@obj
            result.onSuccess { response ->
                val latest = response.optJSONObject("latest")
                if (response.optBoolean("available") && latest != null) {
                    offer(activity, prefs, latest)
                } else if (!quiet) {
                    Toast.makeText(activity, "Appen är redan senaste versionen (${BuildConfig.VERSION_NAME}).",
                        Toast.LENGTH_LONG).show()
                }
            }.onFailure {
                if (!quiet) Toast.makeText(activity, Api.friendly(it), Toast.LENGTH_LONG).show()
            }
        }
    }

    private fun offer(activity: Activity, prefs: Prefs, latest: JSONObject) {
        AlertDialog.Builder(activity)
            .setTitle("Ny version av TV Hub")
            .setMessage("Version ${latest.optString("version_name")} finns. Du har ${BuildConfig.VERSION_NAME}.\n\nUppdatera nu? Det tar under en minut.")
            .setPositiveButton("Uppdatera") { _, _ -> download(activity, prefs) }
            .setNegativeButton("Senare", null)
            .show()
    }

    private fun download(activity: Activity, prefs: Prefs) {
        if (!canInstall(activity)) return
        Toast.makeText(activity, "Hämtar uppdateringen…", Toast.LENGTH_LONG).show()
        val dir = File(activity.cacheDir, "updates")
        val file = File(dir, "tvhub.apk")
        Api.async({
            dir.mkdirs()
            // Hemma direkt från Home Assistant, annars via Nabu Casa (telefonen).
            val conn = Api.openBlocking("/device/app.apk", readTimeoutMs = 120_000)
            try {
                if (conn.responseCode != 200) throw ApiException(conn.responseCode, "Uppdateringen kunde inte hämtas (HTTP ${conn.responseCode}).")
                conn.inputStream.use { input -> file.outputStream().use { output -> input.copyTo(output) } }
            } finally {
                conn.disconnect()
            }
            file
        }) { result ->
            result.onSuccess { install(activity, it) }
                .onFailure { Toast.makeText(activity, Api.friendly(it), Toast.LENGTH_LONG).show() }
        }
    }

    /** Android 8 och senare kräver att TV Hub får installera appar. Android 7 använder "Okända källor". */
    private fun canInstall(activity: Activity): Boolean {
        if (Build.VERSION.SDK_INT >= 26 && !activity.packageManager.canRequestPackageInstalls()) {
            AlertDialog.Builder(activity)
                .setTitle("Tillåt uppdateringar")
                .setMessage("TV Hub behöver tillstånd att installera sin egen uppdatering. Slå på \"Tillåt från den här källan\" på nästa skärm, gå tillbaka och välj Uppdatera igen.")
                .setPositiveButton("Öppna inställningar") { _, _ ->
                    try {
                        activity.startActivity(
                            Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:${activity.packageName}"))
                        )
                    } catch (e: Exception) {
                        Toast.makeText(activity, "Öppna Inställningar → Appar → TV Hub och tillåt installation av appar.", Toast.LENGTH_LONG).show()
                    }
                }
                .setNegativeButton("Avbryt", null)
                .show()
            return false
        }
        return true
    }

    private fun install(activity: Activity, file: File) {
        val uri = FileProvider.getUriForFile(activity, "${activity.packageName}.files", file)
        val intent = Intent(Intent.ACTION_VIEW)
            .setDataAndType(uri, "application/vnd.android.package-archive")
            .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)
        try {
            activity.startActivity(intent)
        } catch (e: Exception) {
            Toast.makeText(activity, "Installationen kunde inte startas: ${e.message}", Toast.LENGTH_LONG).show()
        }
    }
}
