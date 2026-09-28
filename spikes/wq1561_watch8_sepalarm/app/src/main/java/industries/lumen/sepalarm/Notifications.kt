package industries.lumen.sepalarm

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent

/**
 * The anti-FingerFind contract, in code:
 *  - The only notification this app ever posts is the mandatory FGS one, on an
 *    IMPORTANCE_MIN silent channel (drawer-minimized, never a card, never a tone).
 *  - Disconnect/reconnect events post NOTHING — sound + vibration + on-screen state
 *    carry the signal.
 *  - Any future transient notification MUST use TRANSIENT_TIMEOUT_MS.
 */
object Notifications {

    const val FGS_ID = 1
    private const val CHANNEL_ID = "sepalarm_fgs"
    const val TRANSIENT_TIMEOUT_MS = 8_000L

    fun foregroundNote(context: Context): Notification {
        val nm = context.getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(
            NotificationChannel(
                CHANNEL_ID,
                context.getString(R.string.channel_fgs),
                NotificationManager.IMPORTANCE_MIN,
            ).apply {
                setShowBadge(false)
                setSound(null, null)
                enableVibration(false)
            }
        )
        val tap = PendingIntent.getActivity(
            context, 0,
            Intent(context, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE,
        )
        return Notification.Builder(context, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.stat_sys_data_bluetooth)
            .setContentTitle(context.getString(R.string.fgs_title))
            .setContentIntent(tap)
            .setOngoing(true)
            .build()
    }
}
