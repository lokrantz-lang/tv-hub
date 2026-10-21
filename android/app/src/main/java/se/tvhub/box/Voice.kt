package se.tvhub.box

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.speech.RecognizerIntent
import android.widget.Toast

/**
 * Röstsök med Googles röstinmatning (samma ruta med mikrofon som Google-knappen ger).
 * Det man säger kommer tillbaka som text till TV Hubs sökruta. Finns ingen taligenkänning på
 * enheten visas ingen mikrofon.
 */
object Voice {
    const val REQUEST = 4721

    private fun intent(): Intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH)
        .putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
        .putExtra(RecognizerIntent.EXTRA_LANGUAGE, "sv-SE")
        .putExtra(RecognizerIntent.EXTRA_PROMPT, "Säg vad du vill se")
        .putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 1)

    fun available(context: Context): Boolean = try {
        context.packageManager.queryIntentActivities(intent(), 0).isNotEmpty()
    } catch (_: Exception) {
        false
    }

    fun start(activity: Activity) {
        try {
            activity.startActivityForResult(intent(), REQUEST)
        } catch (_: Exception) {
            Toast.makeText(activity, "Röstsök fungerar inte på den här enheten.", Toast.LENGTH_LONG).show()
        }
    }

    /** Texten som sades, eller null (avbrutet eller annan förfrågan). */
    fun result(requestCode: Int, resultCode: Int, data: Intent?): String? {
        if (requestCode != REQUEST || resultCode != Activity.RESULT_OK) return null
        return data?.getStringArrayListExtra(RecognizerIntent.EXTRA_RESULTS)?.firstOrNull()?.trim()?.takeIf { it.isNotEmpty() }
    }
}
