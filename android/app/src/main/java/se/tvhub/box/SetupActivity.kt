package se.tvhub.box

import android.app.Activity
import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.view.inputmethod.EditorInfo
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import org.json.JSONObject

/** Första start: ange Home Assistants adress och parningskoden från webbadminen. */
class SetupActivity : Activity() {
    private lateinit var prefs: Prefs
    private lateinit var server: EditText
    private lateinit var code: EditText
    private lateinit var pair: Button
    private lateinit var status: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_setup)
        prefs = Prefs(this)
        Api.init(prefs)
        server = findViewById(R.id.server)
        code = findViewById(R.id.code)
        pair = findViewById(R.id.pair)
        status = findViewById(R.id.status)

        server.setText(prefs.server.ifEmpty { "http://192.168.1." })
        server.setSelection(server.text.length)
        code.setOnEditorActionListener { _, actionId, _ ->
            if (actionId == EditorInfo.IME_ACTION_DONE) {
                startPairing()
                true
            } else {
                false
            }
        }
        pair.setOnClickListener { startPairing() }
        if (prefs.server.isNotEmpty()) code.requestFocus() else server.requestFocus()
    }

    private fun normalizeServer(raw: String): String {
        var value = raw.trim().trimEnd('/')
        if (!value.startsWith("http://") && !value.startsWith("https://")) value = "http://$value"
        val hostPart = value.substringAfter("://")
        if (!hostPart.contains(":")) value = "$value:8099"
        return value
    }

    private fun startPairing() {
        val base = normalizeServer(server.text.toString())
        val pairingCode = code.text.toString().filter { it.isDigit() }
        if (pairingCode.length != 6) {
            status.text = "Koden ska vara sex siffror."
            return
        }
        pair.isEnabled = false
        status.setTextColor(getColor(R.color.muted))
        status.text = "Ansluter…"
        val name = Build.MODEL ?: "TV-box"
        val body = JSONObject()
            .put("code", pairingCode)
            .put("name", name)
            .put("model", "${Build.MANUFACTURER} ${Build.MODEL} (Android ${Build.VERSION.RELEASE})")
            .put("app_version", BuildConfig.VERSION_NAME)

        prefs.clearPairing()
        Api.async({
            val ping = Api.requestBlocking("GET", "/device/ping", base = base) as? JSONObject
            if (ping?.optString("app") != "tvhub") throw ApiException(0, "Adressen svarar, men det är inte TV Hub.")
            Api.requestBlocking("POST", "/device/pair", body, base = base) as JSONObject
        }) { result ->
            pair.isEnabled = true
            result.onSuccess { response ->
                prefs.server = base
                prefs.token = response.getString("token")
                prefs.deviceName = response.optString("name", name)
                startActivity(Intent(this, MainActivity::class.java))
                finish()
            }.onFailure { error ->
                status.setTextColor(getColor(R.color.bad))
                status.text = Api.friendly(error)
            }
        }
    }
}
