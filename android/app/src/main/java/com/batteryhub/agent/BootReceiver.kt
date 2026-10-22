package com.batteryhub.agent

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

/** Reinicia o envio após ligar o aparelho ou atualizar o app. */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        ReportService.start(context)
    }
}
