package industries.lumen.sepalarm

import android.Manifest
import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.widget.Button
import android.widget.TextView

/** On-wrist status + DISMISS. Requests runtime permissions, then starts the FGS. */
class MainActivity : Activity() {

    private lateinit var status: TextView
    private val ui = Handler(Looper.getMainLooper())

    private val refresh = object : Runnable {
        override fun run() {
            status.text = when (SepAlarmService.lastKnownConnected) {
                true -> getString(R.string.status_connected)
                false -> getString(R.string.status_separated)
                null -> getString(R.string.status_unknown)
            }
            ui.postDelayed(this, 2_000)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        status = findViewById(R.id.status)

        findViewById<Button>(R.id.dismiss).setOnClickListener {
            startService(
                Intent(this, SepAlarmService::class.java)
                    .setAction(SepAlarmService.ACTION_DISMISS)
            )
        }

        val missing = REQUIRED.filter {
            checkSelfPermission(it) != PackageManager.PERMISSION_GRANTED
        }
        if (missing.isEmpty()) SepAlarmService.start(this)
        else requestPermissions(missing.toTypedArray(), 1)
    }

    override fun onRequestPermissionsResult(
        requestCode: Int, permissions: Array<out String>, grantResults: IntArray,
    ) {
        if (grantResults.all { it == PackageManager.PERMISSION_GRANTED }) {
            SepAlarmService.start(this)
        } else {
            status.text = getString(R.string.status_no_permission)
        }
    }

    override fun onResume() {
        super.onResume()
        ui.post(refresh)
    }

    override fun onPause() {
        super.onPause()
        ui.removeCallbacks(refresh)
    }

    companion object {
        val REQUIRED = arrayOf(
            Manifest.permission.BLUETOOTH_CONNECT,
            Manifest.permission.POST_NOTIFICATIONS,
        )
    }
}
