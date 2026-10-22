package com.batteryhub.agent

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.graphics.drawable.Icon
import android.media.AudioAttributes
import android.media.AudioManager
import android.media.MediaPlayer
import android.media.RingtoneManager
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.VibrationEffect
import android.os.Vibrator
import android.os.VibratorManager
import kotlin.concurrent.thread

/**
 * "Localizar dispositivo": toca o alarme no volume máximo e vibra até alguém parar
 * (botão da notificação, tela do app, comando do PC) ou até passar [MAX_RING_MS].
 * Tudo roda na thread principal; o estado inicial do volume é restaurado ao parar.
 */
object Finder {
    private const val CHANNEL_ID = "find_device"
    private const val NOTIFICATION_ID = 2
    private const val MAX_RING_MS = 10 * 60_000L

    @Volatile
    var ringing = false
        private set

    /** Chamado na thread principal quando [ringing] muda (a tela usa para mostrar o aviso). */
    var listener: (() -> Unit)? = null

    private val main = Handler(Looper.getMainLooper())
    private var player: MediaPlayer? = null
    private var savedVolume = -1
    private var savedFilter = -1 // modo Não Perturbe anterior; -1 = não foi alterado
    private var autoStop: Runnable? = null

    private val alarmAttributes = AudioAttributes.Builder()
        .setUsage(AudioAttributes.USAGE_ALARM)
        .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
        .build()

    fun start(context: Context) {
        val app = context.applicationContext
        main.post { startNow(app) }
    }

    fun stop(context: Context) {
        val app = context.applicationContext
        main.post { stopNow(app) }
    }

    private fun startNow(context: Context) {
        if (ringing) return
        ringing = true

        suspendDoNotDisturb(context) // antes do volume: com o Não Perturbe ativo o Android pode recusar a mudança
        val audio = context.getSystemService(AudioManager::class.java)
        try {
            savedVolume = audio.getStreamVolume(AudioManager.STREAM_ALARM)
            audio.setStreamVolume(AudioManager.STREAM_ALARM, audio.getStreamMaxVolume(AudioManager.STREAM_ALARM), 0)
        } catch (e: SecurityException) {
            savedVolume = -1 // Não Perturbe bloqueando: toca no volume atual
        }
        startSound(context)
        vibrator(context).vibrate(VibrationEffect.createWaveform(longArrayOf(0, 900, 500), 0), alarmAttributes)
        showNotification(context)

        autoStop = Runnable { stopNow(context) }.also { main.postDelayed(it, MAX_RING_MS) }
        listener?.invoke()
        thread { Reporter.ackFind(context, true) }
    }

    private fun stopNow(context: Context) {
        if (!ringing) return
        ringing = false

        autoStop?.let(main::removeCallbacks)
        autoStop = null
        player?.let {
            runCatching { it.stop() }
            it.release()
        }
        player = null
        vibrator(context).cancel()
        if (savedVolume >= 0) {
            runCatching {
                context.getSystemService(AudioManager::class.java)
                    .setStreamVolume(AudioManager.STREAM_ALARM, savedVolume, 0)
            }
        }
        savedVolume = -1
        restoreDoNotDisturb(context)
        context.getSystemService(NotificationManager::class.java).cancel(NOTIFICATION_ID)

        listener?.invoke()
        thread { Reporter.ackFind(context, false) }
    }

    /**
     * Se o usuário concedeu o acesso ao Não Perturbe (a tela do app tem o botão), desliga o bloqueio
     * enquanto toca; sem o acesso o alarme ainda passa pelo silencioso, mas pode ser calado por ele.
     */
    private fun suspendDoNotDisturb(context: Context) {
        val manager = context.getSystemService(NotificationManager::class.java)
        if (!manager.isNotificationPolicyAccessGranted) return
        val current = manager.currentInterruptionFilter
        if (current == NotificationManager.INTERRUPTION_FILTER_ALL ||
            current == NotificationManager.INTERRUPTION_FILTER_UNKNOWN
        ) return
        savedFilter = current
        manager.setInterruptionFilter(NotificationManager.INTERRUPTION_FILTER_ALL)
    }

    private fun restoreDoNotDisturb(context: Context) {
        if (savedFilter < 0) return
        val manager = context.getSystemService(NotificationManager::class.java)
        if (manager.isNotificationPolicyAccessGranted) manager.setInterruptionFilter(savedFilter)
        savedFilter = -1
    }

    private fun startSound(context: Context) {
        val uri = RingtoneManager.getDefaultUri(RingtoneManager.TYPE_ALARM)
            ?: RingtoneManager.getDefaultUri(RingtoneManager.TYPE_RINGTONE)
            ?: return
        player = try {
            MediaPlayer().apply {
                setAudioAttributes(alarmAttributes)
                setDataSource(context, uri)
                isLooping = true
                prepare()
                start()
            }
        } catch (e: Exception) {
            null // sem som, ainda vibra e mostra a notificação
        }
    }

    @Suppress("DEPRECATION")
    private fun vibrator(context: Context): Vibrator =
        if (Build.VERSION.SDK_INT >= 31) {
            context.getSystemService(VibratorManager::class.java).defaultVibrator
        } else {
            context.getSystemService(Vibrator::class.java)
        }

    private fun showNotification(context: Context) {
        val manager = context.getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(
            NotificationChannel(CHANNEL_ID, context.getString(R.string.find_channel), NotificationManager.IMPORTANCE_HIGH)
        )
        val flags = PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        val stopIntent = PendingIntent.getForegroundService(
            context, 1,
            Intent(context, ReportService::class.java).setAction(ReportService.ACTION_STOP_FIND), flags,
        )
        val openIntent = PendingIntent.getActivity(context, 0, Intent(context, MainActivity::class.java), flags)
        val stopAction = Notification.Action.Builder(
            Icon.createWithResource(context, R.drawable.ic_stat_battery), context.getString(R.string.find_stop), stopIntent,
        ).build()
        manager.notify(
            NOTIFICATION_ID,
            Notification.Builder(context, CHANNEL_ID)
                .setSmallIcon(R.drawable.ic_stat_battery)
                .setContentTitle(context.getString(R.string.find_title))
                .setContentText(context.getString(R.string.find_text))
                .setCategory(Notification.CATEGORY_ALARM)
                .setOngoing(true)
                .setContentIntent(openIntent)
                .addAction(stopAction)
                .build(),
        )
    }
}
