package se.tvhub.box

import android.content.Context

/** Sparade inställningar: serveradress och boxens nyckel. */
class Prefs(context: Context) {
    private val sp = context.applicationContext.getSharedPreferences("tvhub", Context.MODE_PRIVATE)

    var server: String
        get() = sp.getString("server", "") ?: ""
        set(value) {
            sp.edit().putString("server", value).apply()
        }

    var token: String
        get() = sp.getString("token", "") ?: ""
        set(value) {
            sp.edit().putString("token", value).apply()
        }

    var deviceName: String
        get() = sp.getString("device_name", "") ?: ""
        set(value) {
            sp.edit().putString("device_name", value).apply()
        }

    val paired: Boolean
        get() = server.isNotEmpty() && token.isNotEmpty()

    fun clearPairing() {
        sp.edit().remove("token").apply()
    }
}
