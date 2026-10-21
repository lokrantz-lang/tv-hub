package se.tvhub.box

import android.app.Activity
import android.app.AlertDialog
import android.text.InputType
import android.widget.EditText
import android.widget.Toast
import org.json.JSONObject

/**
 * Slår vuxenläget av och på. Har TV Hub en PIN-kod (webbadminen, Boxar) frågar appen efter den
 * och får en upplåsning som gäller i högst 3 timmar. Utan kod fungerar det som förut.
 */
object AdultGate {
    fun toggle(activity: Activity, onChanged: () -> Unit) {
        val prefs = Prefs(activity)
        if (prefs.adultMode) {
            prefs.adultMode = false
            Api.fire("/device/adult-lock", JSONObject())
            Toast.makeText(activity, "Läge ändrat", Toast.LENGTH_SHORT).show()
            onChanged()
            return
        }
        Api.obj("POST", "/device/adult-unlock", JSONObject()) { result ->
            result.onSuccess { r ->
                if (!r.optBoolean("pin_required")) {
                    enable(activity, "", onChanged)
                } else {
                    askPin(activity, onChanged)
                }
            }.onFailure { Toast.makeText(activity, Api.friendly(it), Toast.LENGTH_LONG).show() }
        }
    }

    private fun enable(activity: Activity, token: String, onChanged: () -> Unit) {
        val prefs = Prefs(activity)
        prefs.adultMode = true
        prefs.adultUnlock = token
        Toast.makeText(activity, "Läge ändrat", Toast.LENGTH_SHORT).show()
        onChanged()
    }

    private fun askPin(activity: Activity, onChanged: () -> Unit) {
        if (activity.isFinishing) return
        val input = EditText(activity)
        input.inputType = InputType.TYPE_CLASS_NUMBER or InputType.TYPE_NUMBER_VARIATION_PASSWORD
        input.hint = "Kod"
        val pad = Look.dp(activity, 20)
        input.setPadding(pad, pad / 2, pad, pad / 2)
        AlertDialog.Builder(activity)
            .setTitle("Ange kod")
            .setView(input)
            .setPositiveButton("OK") { _, _ ->
                val pin = input.text.toString().trim()
                if (pin.isEmpty()) return@setPositiveButton
                Api.obj("POST", "/device/adult-unlock", JSONObject().put("pin", pin)) { result ->
                    result.onSuccess { r -> enable(activity, r.optString("token"), onChanged) }
                        .onFailure { Toast.makeText(activity, Api.friendly(it), Toast.LENGTH_LONG).show() }
                }
            }
            .setNegativeButton("Avbryt", null)
            .show()
        input.requestFocus()
    }
}
