package se.tvhub.box

import android.app.Activity
import android.content.Context
import android.content.pm.ActivityInfo
import android.content.pm.PackageManager
import android.content.res.Configuration
import android.telephony.TelephonyManager

/**
 * TV eller telefon. Samma app: på TV-boxarna det vanliga utseendet, på en telefon mobilläget
 * (stående, touch, flikar längst ner).
 *
 * Automatiskt: mobilläge bara om enheten har mobilnät (en riktig telefon med SIM-funktion)
 * och inte är en TV. TV-boxar har inget mobilnät, så de påverkas inte. Kan ändras under
 * Inställningar (även med fjärrkontrollen, om något skulle bli fel).
 */
object FormFactor {
    const val KEY = "layout_mode"
    @Volatile private var detected: Boolean? = null

    fun isMobile(context: Context): Boolean {
        val mode = context.applicationContext.getSharedPreferences("tvhub", Context.MODE_PRIVATE)
            .getString(KEY, "auto") ?: "auto"
        return when (mode) {
            "mobile" -> true
            "tv" -> false
            else -> detect(context)
        }
    }

    /** Vad den automatiska kontrollen kom fram till (för inställningarna). */
    fun detect(context: Context): Boolean {
        detected?.let { return it }
        val result = try {
            val pm = context.packageManager
            val uiMode = context.resources.configuration.uiMode and Configuration.UI_MODE_TYPE_MASK
            when {
                uiMode == Configuration.UI_MODE_TYPE_TELEVISION -> false
                pm.hasSystemFeature(PackageManager.FEATURE_LEANBACK) -> false
                !pm.hasSystemFeature(PackageManager.FEATURE_TOUCHSCREEN) -> false
                !pm.hasSystemFeature(PackageManager.FEATURE_TELEPHONY) -> false
                else -> {
                    // Vissa boxar påstår att de har telefoni. En riktig telefon har en telefontyp.
                    val tm = context.getSystemService(Context.TELEPHONY_SERVICE) as? TelephonyManager
                    tm != null && tm.phoneType != TelephonyManager.PHONE_TYPE_NONE
                }
            }
        } catch (_: Exception) {
            false
        }
        detected = result
        return result
    }

    /** Listor och detaljsidor på telefonen visas stående. Spelaren får vridas fritt. */
    fun portraitIfMobile(activity: Activity) {
        if (isMobile(activity)) activity.requestedOrientation = ActivityInfo.SCREEN_ORIENTATION_PORTRAIT
    }
}
