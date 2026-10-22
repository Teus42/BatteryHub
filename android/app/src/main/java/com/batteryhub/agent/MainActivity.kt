package com.batteryhub.agent

import android.app.Activity
import android.app.NotificationManager
import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.PowerManager
import android.provider.Settings as AndroidSettings
import android.text.Editable
import android.text.TextWatcher
import android.text.format.DateUtils
import android.widget.Button
import android.view.View
import android.view.ViewGroup
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import kotlin.concurrent.thread

class MainActivity : Activity() {
    private lateinit var settings: Settings
    private lateinit var hostInput: EditText
    private lateinit var portInput: EditText
    private lateinit var codeInput: EditText
    private lateinit var activationText: TextView
    private lateinit var activateButton: Button
    private lateinit var intervalInput: EditText
    private lateinit var statusText: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        settings = Settings(this)
        limitContentWidth()

        hostInput = findViewById(R.id.host)
        portInput = findViewById(R.id.port)
        codeInput = findViewById(R.id.code)
        activationText = findViewById(R.id.activation_status)
        activateButton = findViewById(R.id.activate)
        intervalInput = findViewById(R.id.interval)
        statusText = findViewById(R.id.status)

        hostInput.setText(settings.host)
        portInput.setText(settings.port.toString())
        intervalInput.setText(settings.intervalMinutes.toString())

        codeInput.addTextChangedListener(CodeFormatter())
        findViewById<Button>(R.id.save).setOnClickListener { saveServer() }
        activateButton.setOnClickListener { activate() }
        findViewById<Button>(R.id.apply_interval).setOnClickListener { applyInterval() }
        findViewById<Button>(R.id.test).setOnClickListener { sendTest() }
        findViewById<Button>(R.id.battery_opt).setOnClickListener { requestBatteryExemption() }
        findViewById<Button>(R.id.vendor_guide).setOnClickListener { openVendorGuide() }
        findViewById<Button>(R.id.find_stop).setOnClickListener { Finder.stop(applicationContext) }
        findViewById<Button>(R.id.dnd_access).setOnClickListener {
            startActivity(Intent(AndroidSettings.ACTION_NOTIFICATION_POLICY_ACCESS_SETTINGS))
        }

        // O envio é automático: basta o dispositivo estar ativado.
        requestNotificationPermission()
        ReportService.start(this)
    }

    /** Android 13+: sem esta permissão a notificação fixa do serviço fica oculta (o serviço roda igual). */
    private fun requestNotificationPermission() {
        if (Build.VERSION.SDK_INT >= 33 &&
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) {
            requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 0)
        }
    }

    /** Em tablets o conteúdo vira uma coluna centralizada em vez de campos esticados. */
    private fun limitContentWidth() {
        val content = findViewById<LinearLayout>(R.id.content)
        val maxWidth = (MAX_CONTENT_DP * resources.displayMetrics.density).toInt()
        content.layoutParams = FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
            .also { it.gravity = android.view.Gravity.CENTER_HORIZONTAL }
        content.post {
            if (content.width > maxWidth) {
                content.layoutParams = FrameLayout.LayoutParams(maxWidth, ViewGroup.LayoutParams.WRAP_CONTENT)
                    .also { it.gravity = android.view.Gravity.CENTER_HORIZONTAL }
            }
        }
    }

    override fun onResume() {
        super.onResume()
        showActivation()
        showStatus()
        showFindBanner()
        showDndAccess()
        Finder.listener = { runOnUiThread { showFindBanner() } }
    }

    /** O botão abre a tela do sistema onde o acesso ao Não Perturbe é concedido; mostra se já foi. */
    private fun showDndAccess() {
        val granted = getSystemService(NotificationManager::class.java).isNotificationPolicyAccessGranted
        findViewById<Button>(R.id.dnd_access).setText(if (granted) R.string.dnd_allowed else R.string.dnd_allow)
    }

    override fun onPause() {
        Finder.listener = null
        super.onPause()
    }

    /** O aviso com o botão "Parar toque" só aparece enquanto o PC está procurando este aparelho. */
    private fun showFindBanner() {
        findViewById<View>(R.id.find_banner).visibility = if (Finder.ringing) View.VISIBLE else View.GONE
    }

    /** Valida e grava IP e porta; devolve false (com aviso no campo) se algum for inválido. */
    private fun saveServer(): Boolean {
        val host = hostInput.text.toString().trim()
        val port = portInput.text.toString().toIntOrNull()
        when {
            host.isEmpty() -> hostInput.error = getString(R.string.required)
            port == null || port !in 1..65535 -> portInput.error = getString(R.string.invalid_port)
            else -> {
                settings.host = host
                settings.port = port
                return true
            }
        }
        return false
    }

    private fun activate() {
        val code = codeInput.text.toString().filter { it.isDigit() }
        if (code.length != 6) {
            codeInput.error = getString(R.string.invalid_code)
            return
        }
        if (!saveServer()) return

        activateButton.isEnabled = false
        activationText.setText(R.string.activating)
        thread {
            val result = Reporter.pair(applicationContext, code)
            runOnUiThread {
                activateButton.isEnabled = true
                if (result == Reporter.PAIRED) {
                    codeInput.text.clear()
                    Toast.makeText(this, R.string.activated_toast, Toast.LENGTH_SHORT).show()
                    showActivation()
                    ReportService.start(this)
                } else {
                    activationText.text = result
                }
            }
        }
    }

    private fun showActivation() {
        activationText.text = if (settings.isActivated) {
            getString(R.string.activated_as, settings.deviceName.ifBlank { "—" })
        } else {
            getString(R.string.not_activated)
        }
        activateButton.setText(if (settings.isActivated) R.string.reactivate else R.string.activate)
    }

    private fun applyInterval() {
        val minutes = intervalInput.text.toString().toIntOrNull()
        if (minutes == null || minutes !in Settings.MIN_INTERVAL_MINUTES..Settings.MAX_INTERVAL_MINUTES) {
            intervalInput.error = getString(
                R.string.invalid_interval, Settings.MIN_INTERVAL_MINUTES, Settings.MAX_INTERVAL_MINUTES
            )
            return
        }
        settings.intervalMinutes = minutes
        ReportService.reschedule(this)
        Toast.makeText(this, getString(R.string.interval_applied, minutes), Toast.LENGTH_SHORT).show()
    }

    private fun sendTest() {
        if (!saveServer()) return
        statusText.setText(R.string.sending)
        thread {
            Reporter.send(applicationContext)
            runOnUiThread { showStatus() }
        }
    }

    private fun showStatus() {
        val attempt = settings.lastAttemptMillis
        statusText.text = if (attempt == 0L) {
            getString(R.string.no_attempts)
        } else {
            val ago = DateUtils.getRelativeTimeSpanString(attempt, System.currentTimeMillis(), DateUtils.MINUTE_IN_MILLIS)
            "${settings.lastResult}\n($ago)"
        }
    }

    private fun requestBatteryExemption() {
        val pm = getSystemService(POWER_SERVICE) as PowerManager
        if (pm.isIgnoringBatteryOptimizations(packageName)) {
            Toast.makeText(this, R.string.already_exempt, Toast.LENGTH_SHORT).show()
            return
        }
        startActivity(
            Intent(AndroidSettings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS)
                .setData(Uri.parse("package:$packageName"))
        )
    }

    /** Guia por fabricante (Xiaomi, Samsung, Huawei...) para liberar execução em segundo plano. */
    private fun openVendorGuide() {
        val vendor = Build.MANUFACTURER.lowercase().replace(Regex("[^a-z0-9]"), "")
        startActivity(Intent(Intent.ACTION_VIEW, Uri.parse("https://dontkillmyapp.com/$vendor")))
    }

    private companion object {
        const val MAX_CONTENT_DP = 560
    }

    /** Mostra o código como 123-456 enquanto o usuário digita. */
    private class CodeFormatter : TextWatcher {
        private var editing = false

        override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) = Unit
        override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) = Unit

        override fun afterTextChanged(s: Editable) {
            if (editing) return
            editing = true
            val digits = s.filter { it.isDigit() }.take(6)
            val formatted = if (digits.length > 3) "${digits.take(3)}-${digits.drop(3)}" else digits.toString()
            s.replace(0, s.length, formatted)
            editing = false
        }
    }
}
