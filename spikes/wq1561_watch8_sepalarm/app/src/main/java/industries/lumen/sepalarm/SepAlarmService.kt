package industries.lumen.sepalarm

import android.app.Service
import android.bluetooth.BluetoothDevice
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.ServiceInfo
import android.os.IBinder
import android.os.PowerManager
import android.util.Log
import com.google.android.gms.wearable.Wearable
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.tasks.await

/**
 * Foreground service that owns separation state for the watch<->phone link.
 *
 * Two inputs, one state machine:
 *  - ACL broadcasts (instant, may be missed): accelerate a state change and trigger
 *    an immediate reconcile.
 *  - NodeClient.connectedNodes poll every POLL_MS (state of record): the phone can be
 *    reachable over non-BT transports, and ACL events are lost across process death,
 *    so the poll both raises missed separations and cancels stale ones.
 */
class SepAlarmService : Service() {

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private lateinit var alarm: AlarmPlayer

    @Volatile private var phoneConnected: Boolean? = null // null until first reconcile
    @Volatile private var separatedSinceMs: Long = 0

    private val aclReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            when (intent.action) {
                BluetoothDevice.ACTION_ACL_DISCONNECTED,
                BluetoothDevice.ACTION_ACL_CONNECTED -> scope.launch { reconcile("acl") }
            }
        }
    }

    override fun onCreate() {
        super.onCreate()
        alarm = AlarmPlayer(this)
        startForeground(
            Notifications.FGS_ID,
            Notifications.foregroundNote(this),
            ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE,
        )
        registerReceiver(aclReceiver, IntentFilter().apply {
            addAction(BluetoothDevice.ACTION_ACL_DISCONNECTED)
            addAction(BluetoothDevice.ACTION_ACL_CONNECTED)
        })
        scope.launch {
            while (true) {
                reconcile("poll")
                delay(POLL_MS)
            }
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_DISMISS) alarm.dismiss()
        return START_STICKY
    }

    /** Query the state of record and drive the alarm off any transition. */
    private suspend fun reconcile(source: String) {
        val nodes = try {
            Wearable.getNodeClient(this).connectedNodes.await()
        } catch (e: Exception) {
            Log.w(TAG, "connectedNodes failed ($source): $e")
            return // keep last known state; never alarm on an API error
        }
        val nowConnected = nodes.any { it.isNearby }
        val was = phoneConnected
        phoneConnected = nowConnected
        lastKnownConnected = nowConnected
        StatusTileService.requestUpdate(this)

        when {
            was != false && !nowConnected -> {
                separatedSinceMs = System.currentTimeMillis()
                Log.i(TAG, "SEPARATED ($source)")
                wakeScreen()
                alarm.startSeparationAlarm()
            }
            was == false && nowConnected -> {
                Log.i(TAG, "RECONNECTED ($source)")
                alarm.stopWithReconnectChirp()
            }
        }
    }

    private fun wakeScreen() {
        val pm = getSystemService(PowerManager::class.java)
        @Suppress("DEPRECATION")
        pm.newWakeLock(
            PowerManager.SCREEN_BRIGHT_WAKE_LOCK or PowerManager.ACQUIRE_CAUSES_WAKEUP,
            "sepalarm:alert",
        ).acquire(10_000)
    }

    override fun onDestroy() {
        unregisterReceiver(aclReceiver)
        alarm.dismiss()
        scope.cancel()
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    companion object {
        private const val TAG = "SepAlarm"
        const val POLL_MS = 30_000L
        const val ACTION_DISMISS = "industries.lumen.sepalarm.DISMISS"

        var lastKnownConnected: Boolean? = null
            private set

        fun start(context: Context) {
            context.startForegroundService(Intent(context, SepAlarmService::class.java))
        }
    }
}
