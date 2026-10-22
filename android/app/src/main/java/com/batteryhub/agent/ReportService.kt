package com.batteryhub.agent

import android.app.AlarmManager
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import android.os.SystemClock
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors

/**
 * Serviço em primeiro plano que envia a bateria ao PC a cada N minutos.
 *
 * O ritmo vem de alarmes (AlarmManager) e não de um timer interno: timers comuns param
 * quando a CPU dorme e atrasariam os envios. Cada alarme reentrega ao próprio serviço e
 * agenda o seguinte antes de enviar, então uma falha de rede não interrompe a sequência.
 */
class ReportService : Service() {
    private lateinit var settings: Settings
    private lateinit var executor: ExecutorService
    private var findChannel: FindChannel? = null

    override fun onCreate() {
        super.onCreate()
        settings = Settings(this)
        executor = Executors.newSingleThreadExecutor()
        getSystemService(NotificationManager::class.java).createNotificationChannel(
            NotificationChannel(CHANNEL_ID, getString(R.string.notif_channel), NotificationManager.IMPORTANCE_LOW)
        )
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        // Obrigatório logo após startForegroundService, mesmo que o serviço vá se encerrar.
        startForegroundCompat(buildNotification())
        if (!settings.isConfigured) {
            stopSelf()
            return START_NOT_STICKY
        }

        // Canal de comandos do PC (localizar dispositivo): uma conexão parada, sem custo enquanto ociosa.
        if (findChannel == null) findChannel = FindChannel(applicationContext).also { it.start() }
        if (intent?.action == ACTION_STOP_FIND) {  // botão "Parar" da notificação de busca
            Finder.stop(applicationContext)
            return START_STICKY
        }

        scheduleNext()
        val sendNow = when (intent?.action) {
            ACTION_SEND -> true
            ACTION_RESCHEDULE -> false
            // Início pela tela, boot ou reinício do sistema: evita enviar de novo se acabou de enviar.
            else -> System.currentTimeMillis() - settings.lastAttemptMillis >= settings.intervalMillis / 2
        }
        if (sendNow) executor.execute(::sendReading)
        return START_STICKY
    }

    private fun sendReading() {
        val power = getSystemService(PowerManager::class.java)
        val wakeLock = power.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "BatteryHub:send")
        wakeLock.acquire(WAKE_LOCK_TIMEOUT_MS)
        try {
            Reporter.send(applicationContext)
        } finally {
            if (wakeLock.isHeld) wakeLock.release()
        }
        getSystemService(NotificationManager::class.java).notify(NOTIFICATION_ID, buildNotification())
        findChannel?.nudge() // se o canal estava esperando para reconectar, o PC acabou de responder
    }

    private fun scheduleNext() {
        val alarms = getSystemService(AlarmManager::class.java)
        val triggerAt = SystemClock.elapsedRealtime() + settings.intervalMillis
        val pending = alarmIntent(this)
        if (Build.VERSION.SDK_INT < 31 || alarms.canScheduleExactAlarms()) {
            alarms.setExactAndAllowWhileIdle(AlarmManager.ELAPSED_REALTIME_WAKEUP, triggerAt, pending)
        } else {
            alarms.setAndAllowWhileIdle(AlarmManager.ELAPSED_REALTIME_WAKEUP, triggerAt, pending)
        }
    }

    private fun buildNotification(): Notification {
        val last = settings.lastResult.ifBlank { getString(R.string.sending) }
        val open = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        return Notification.Builder(this, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_stat_battery)
            .setContentTitle(getString(R.string.app_name))
            .setContentText(getString(R.string.notif_text, last, settings.intervalMinutes))
            .setContentIntent(open)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setShowWhen(false)
            .build()
    }

    private fun startForegroundCompat(notification: Notification) {
        if (Build.VERSION.SDK_INT >= 29) {
            startForeground(NOTIFICATION_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_MANIFEST)
        } else {
            startForeground(NOTIFICATION_ID, notification)
        }
    }

    override fun onDestroy() {
        getSystemService(AlarmManager::class.java).cancel(alarmIntent(this))
        findChannel?.shutdown()
        Finder.stop(applicationContext)
        executor.shutdown()
        super.onDestroy()
    }

    companion object {
        private const val CHANNEL_ID = "battery_report"
        private const val NOTIFICATION_ID = 1
        private const val WAKE_LOCK_TIMEOUT_MS = 60_000L
        const val ACTION_STOP_FIND = "com.batteryhub.agent.STOP_FIND"
        private const val ACTION_SEND = "com.batteryhub.agent.SEND"
        private const val ACTION_RESCHEDULE = "com.batteryhub.agent.RESCHEDULE"

        private fun alarmIntent(context: Context): PendingIntent =
            PendingIntent.getForegroundService(
                context, 0,
                Intent(context, ReportService::class.java).setAction(ACTION_SEND),
                PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
            )

        /** Garante o serviço rodando (não faz nada se o dispositivo ainda não foi ativado). */
        fun start(context: Context) = launch(context, null)

        /** Aplica um novo intervalo sem disparar um envio extra. */
        fun reschedule(context: Context) = launch(context, ACTION_RESCHEDULE)

        private fun launch(context: Context, action: String?) {
            if (!Settings(context).isConfigured) return
            try {
                context.startForegroundService(Intent(context, ReportService::class.java).setAction(action))
            } catch (e: IllegalStateException) {
                // Sistema recusou iniciar em segundo plano; o próximo alarme ou abertura do app tenta de novo.
            }
        }
    }
}
