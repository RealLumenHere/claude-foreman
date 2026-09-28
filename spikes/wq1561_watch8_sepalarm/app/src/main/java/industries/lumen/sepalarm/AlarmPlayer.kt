package industries.lumen.sepalarm

import android.content.Context
import android.media.AudioAttributes
import android.media.RingtoneManager
import android.media.ToneGenerator
import android.media.AudioManager
import android.media.MediaPlayer
import android.os.VibrationEffect
import android.os.Vibrator
import android.os.VibratorManager
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/**
 * Separation alert on STREAM_ALARM (USAGE_ALARM) — audible in vibrate mode, which is
 * the entire point vs FingerFind's notification-tone channel. Repeats every
 * CYCLE_MS, escalating volume 40% -> 100% over the first three cycles, until
 * reconnect or on-wrist dismiss.
 */
class AlarmPlayer(private val context: Context) {

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)
    private var loop: Job? = null
    private var player: MediaPlayer? = null

    private val vibrator: Vibrator =
        context.getSystemService(VibratorManager::class.java).defaultVibrator

    private val alarmAttrs = AudioAttributes.Builder()
        .setUsage(AudioAttributes.USAGE_ALARM)
        .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
        .build()

    fun startSeparationAlarm() {
        if (loop?.isActive == true) return
        loop = scope.launch {
            var cycle = 0
            while (true) {
                val volume = ESCALATION.getOrElse(cycle) { 1.0f }
                playTone(volume)
                vibrator.vibrate(
                    VibrationEffect.createWaveform(VIBE_PATTERN, -1),
                    alarmAttrs,
                )
                cycle++
                delay(CYCLE_MS)
            }
        }
    }

    /** User pressed DISMISS on-wrist: silence, monitoring continues. */
    fun dismiss() {
        loop?.cancel()
        loop = null
        player?.release()
        player = null
        vibrator.cancel()
    }

    /** Phone came back: kill the loop, one short chirp, no card. */
    fun stopWithReconnectChirp() {
        dismiss()
        ToneGenerator(AudioManager.STREAM_ALARM, RECONNECT_CHIRP_VOL)
            .startTone(ToneGenerator.TONE_PROP_ACK, 300)
    }

    private fun playTone(volume: Float) {
        player?.release()
        val uri = RingtoneManager.getDefaultUri(RingtoneManager.TYPE_ALARM)
            ?: RingtoneManager.getDefaultUri(RingtoneManager.TYPE_RINGTONE)
        player = MediaPlayer().apply {
            setDataSource(context, uri)
            setAudioAttributes(alarmAttrs)
            setVolume(volume, volume)
            isLooping = false
            prepare()
            start()
        }
    }

    companion object {
        const val CYCLE_MS = 10_000L
        val ESCALATION = floatArrayOf(0.4f, 0.7f, 1.0f)
        val VIBE_PATTERN = longArrayOf(0, 400, 200, 400, 200, 800)
        const val RECONNECT_CHIRP_VOL = 70 // ToneGenerator volume 0-100
    }
}
