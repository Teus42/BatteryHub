package com.batteryhub.agent

import android.content.Context

class Settings(context: Context) {
    private val prefs = context.applicationContext
        .getSharedPreferences("settings", Context.MODE_PRIVATE)

    var host: String
        get() = prefs.getString("host", "") ?: ""
        set(value) = prefs.edit().putString("host", value).apply()

    var port: Int
        get() = prefs.getInt("port", 8765)
        set(value) = prefs.edit().putInt("port", value).apply()

    var token: String
        get() = prefs.getString("token", "") ?: ""
        set(value) = prefs.edit().putString("token", value).apply()

    var deviceName: String
        get() = prefs.getString("device_name", "") ?: ""
        set(value) = prefs.edit().putString("device_name", value).apply()

    /** Intervalo entre envios, em minutos. */
    var intervalMinutes: Int
        get() = prefs.getInt("interval_minutes", DEFAULT_INTERVAL_MINUTES)
        set(value) = prefs.edit().putInt("interval_minutes", value).apply()

    var lastResult: String
        get() = prefs.getString("last_result", "") ?: ""
        set(value) = prefs.edit().putString("last_result", value).apply()

    var lastAttemptMillis: Long
        get() = prefs.getLong("last_attempt", 0L)
        set(value) = prefs.edit().putLong("last_attempt", value).apply()

    val intervalMillis: Long
        get() = intervalMinutes * 60_000L

    val isActivated: Boolean
        get() = token.isNotBlank()

    val isConfigured: Boolean
        get() = host.isNotBlank() && isActivated

    companion object {
        const val DEFAULT_INTERVAL_MINUTES = 5
        const val MIN_INTERVAL_MINUTES = 1
        const val MAX_INTERVAL_MINUTES = 1440
    }
}
