package com.batteryhub.agent

import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.BatteryManager
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

object Reporter {
    private const val TIMEOUT_MS = 10_000

    /** Valor devolvido por [pair] quando a ativação funcionou. */
    const val PAIRED = "ok"

    /** Lê o estado da bateria (intent "sticky": não liga sensor nem acorda o rádio). */
    private fun readBattery(context: Context): JSONObject? {
        val intent = context.registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED))
            ?: return null
        val level = intent.getIntExtra(BatteryManager.EXTRA_LEVEL, -1)
        val scale = intent.getIntExtra(BatteryManager.EXTRA_SCALE, -1)
        if (level < 0 || scale <= 0) return null

        return JSONObject().apply {
            put("level", level * 100 / scale)
            put("charging", intent.getIntExtra(BatteryManager.EXTRA_PLUGGED, 0) != 0)
            put("temperature", intent.getIntExtra(BatteryManager.EXTRA_TEMPERATURE, 0) / 10.0)
            put("voltage", intent.getIntExtra(BatteryManager.EXTRA_VOLTAGE, 0) / 1000.0)
        }
    }

    /** Envia uma leitura ao PC e devolve um texto de resultado (também gravado nas configurações). */
    fun send(context: Context): String {
        val settings = Settings(context)
        val result = try {
            when {
                !settings.isConfigured -> "Servidor não configurado ou dispositivo não ativado"
                else -> post(settings, readBattery(context) ?: return record(settings, "Bateria indisponível"))
            }
        } catch (e: Exception) {
            "Falha: ${e.message ?: e.javaClass.simpleName}"
        }
        return record(settings, result)
    }

    /** Troca o código de ativação (123-456) pelo token, que fica guardado para os envios seguintes. */
    fun pair(context: Context, code: String): String {
        val settings = Settings(context)
        return try {
            val connection = URL("http://${settings.host}:${settings.port}/pair")
                .openConnection() as HttpURLConnection
            try {
                connection.requestMethod = "POST"
                connection.connectTimeout = TIMEOUT_MS
                connection.readTimeout = TIMEOUT_MS
                connection.doOutput = true
                connection.setRequestProperty("Content-Type", "application/json")
                val body = JSONObject().put("code", code).toString()
                connection.outputStream.use { it.write(body.toByteArray()) }

                when (val status = connection.responseCode) {
                    200 -> {
                        val json = JSONObject(connection.inputStream.bufferedReader().use { it.readText() })
                        settings.token = json.getString("token")
                        settings.deviceName = json.optString("name")
                        PAIRED
                    }
                    404 -> "Código inválido ou expirado"
                    429 -> "Muitas tentativas. Aguarde 1 minuto"
                    else -> "Servidor respondeu HTTP $status"
                }
            } finally {
                connection.disconnect()
            }
        } catch (e: Exception) {
            "Falha: ${e.message ?: e.javaClass.simpleName}"
        }
    }

    /** Avisa o PC que o aparelho começou (true) ou parou (false) de tocar. Falhas são ignoradas. */
    fun ackFind(context: Context, ringing: Boolean) {
        val settings = Settings(context)
        if (!settings.isConfigured) return
        try {
            val connection = URL("http://${settings.host}:${settings.port}/find/ack")
                .openConnection() as HttpURLConnection
            try {
                connection.requestMethod = "POST"
                connection.connectTimeout = TIMEOUT_MS
                connection.readTimeout = TIMEOUT_MS
                connection.doOutput = true
                connection.setRequestProperty("Content-Type", "application/json")
                connection.setRequestProperty("Authorization", "Bearer ${settings.token}")
                connection.outputStream.use { it.write(JSONObject().put("ringing", ringing).toString().toByteArray()) }
                connection.responseCode
            } finally {
                connection.disconnect()
            }
        } catch (e: Exception) {
            // O PC não confirmou; o estado dele expira sozinho.
        }
    }

    private fun record(settings: Settings, result: String): String {
        settings.lastResult = result
        settings.lastAttemptMillis = System.currentTimeMillis()
        return result
    }

    private fun post(settings: Settings, body: JSONObject): String {
        val connection = URL("http://${settings.host}:${settings.port}/battery")
            .openConnection() as HttpURLConnection
        try {
            connection.requestMethod = "POST"
            connection.connectTimeout = TIMEOUT_MS
            connection.readTimeout = TIMEOUT_MS
            connection.doOutput = true
            connection.setRequestProperty("Content-Type", "application/json")
            connection.setRequestProperty("Authorization", "Bearer ${settings.token}")
            connection.outputStream.use { it.write(body.toString().toByteArray()) }

            return when (val code = connection.responseCode) {
                200 -> "Enviado (${body.getInt("level")}%)"
                401 -> "Dispositivo não reconhecido pelo PC. Ative novamente com um novo código"
                else -> "Servidor respondeu HTTP $code"
            }
        } finally {
            connection.disconnect()
        }
    }
}
