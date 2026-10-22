package com.batteryhub.agent

import android.content.Context
import android.os.SystemClock
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit

/**
 * Canal aberto com o PC (GET /events) por onde ele manda comandos, como o de localizar o aparelho.
 *
 * É uma única conexão HTTP parada: sem tráfego ela não gasta bateria, e o PC escreve nela na
 * hora que precisa. Se cair (PC desligado, fora do Wi-Fi), reconecta com espera crescente;
 * [nudge] antecipa a tentativa quando o envio periódico da bateria funciona de novo.
 */
class FindChannel(private val context: Context) : Thread("find-channel") {
    @Volatile private var running = true
    @Volatile private var current: HttpURLConnection? = null
    private val wake = LinkedBlockingQueue<Unit>() // acorda a espera entre tentativas

    override fun run() {
        var attempt = 0
        while (running) {
            wake.clear()
            val settings = Settings(context)
            val startedAt = SystemClock.elapsedRealtime()
            if (settings.isConfigured) listen(settings)
            if (!running) break

            // Uma conexão que durou bastante era boa: recomeça a espera do zero.
            attempt = if (SystemClock.elapsedRealtime() - startedAt > STABLE_MS) 0 else minOf(attempt + 1, BACKOFF_MS.size - 1)
            if (running) wake.poll(BACKOFF_MS[attempt], TimeUnit.MILLISECONDS)
        }
    }

    /** Conecta e processa comandos até a conexão acabar. */
    private fun listen(settings: Settings) {
        val connection = try {
            URL("http://${settings.host}:${settings.port}/events").openConnection() as HttpURLConnection
        } catch (e: IOException) {
            return
        }
        current = connection
        try {
            connection.connectTimeout = CONNECT_TIMEOUT_MS
            connection.readTimeout = READ_TIMEOUT_MS
            connection.setRequestProperty("Authorization", "Bearer ${settings.token}")
            connection.setRequestProperty("Accept", "text/event-stream")
            if (connection.responseCode != 200) return

            connection.inputStream.bufferedReader().use { reader ->
                while (running) {
                    val line = reader.readLine() ?: break
                    if (line.startsWith("data:")) handle(line.substring(5).trim())
                }
            }
        } catch (e: IOException) {
            // PC fora do ar, Wi-Fi caiu ou o PC parou de responder: o laço de fora reconecta.
        } finally {
            connection.disconnect()
            current = null
        }
    }

    private fun handle(command: String) {
        when (command) {
            "find-start" -> Finder.start(context)
            "find-stop" -> Finder.stop(context)
        }
    }

    /** Reconecta já, se estiver esperando para tentar de novo. */
    fun nudge() {
        wake.offer(Unit)
    }

    fun shutdown() {
        running = false
        nudge()
        Thread { current?.disconnect() }.start() // destrava a leitura bloqueada (rede não pode rodar na thread principal)
    }

    private companion object {
        const val CONNECT_TIMEOUT_MS = 10_000
        // O PC manda um sinal a cada 4 min (ver KEEPALIVE_SECONDS); sem nada por 6,5 min a conexão morreu.
        const val READ_TIMEOUT_MS = 390_000
        const val STABLE_MS = 60_000L
        val BACKOFF_MS = longArrayOf(5_000, 15_000, 45_000, 120_000, 300_000)
    }
}
