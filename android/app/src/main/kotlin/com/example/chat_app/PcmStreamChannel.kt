package com.example.chat_app

import android.content.Context
import android.media.AudioAttributes
import android.media.AudioDeviceInfo
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioTrack
import android.os.Build
import io.flutter.plugin.common.BinaryMessenger
import io.flutter.plugin.common.MethodCall
import io.flutter.plugin.common.MethodChannel
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicLong

/**
 * 通话 TTS 流式播放：AudioTrack MODE_STREAM + USAGE_VOICE_COMMUNICATION。
 *
 * CosyVoice 出块 RTF≈1：首块约 0.5s 音频要算 ~1.4s，后续块间隔也接近音频时长。
 * 若首块一到就 play，块间会欠载静音 →「一个字卡一下」。
 * 因此：先攒够预缓冲再 play；轨缓冲加大吸收抖动；短句用 kick 强制起播。
 */
class PcmStreamChannel(context: Context, messenger: BinaryMessenger) : MethodChannel.MethodCallHandler {
    private val audioManager = context.getSystemService(Context.AUDIO_SERVICE) as AudioManager
    private val channel = MethodChannel(messenger, "huanxin/pcm_stream")

    private var track: AudioTrack? = null
    private var writer: Thread? = null
    private val queue = LinkedBlockingQueue<ByteArray>()
    private val pendingBytes = AtomicLong(0)
    private val framesWritten = AtomicLong(0)
    @Volatile private var running = false
    @Volatile private var started = false
    private var sampleRate = 24000
    /** 开播前至少攒这么多 PCM。CosyVoice RTF≈1.4，需大于一块音频时长。 */
    private var prebufferMs = 1400

    init {
        channel.setMethodCallHandler(this)
    }

    override fun onMethodCall(call: MethodCall, result: MethodChannel.Result) {
        try {
            when (call.method) {
                "prepare" -> {
                    val sr = call.argument<Int>("sampleRate") ?: 24000
                    val speaker = call.argument<Boolean>("speaker") ?: true
                    val pb = call.argument<Int>("prebufferMs") ?: 1000
                    prepare(sr, speaker, pb)
                    result.success(null)
                }
                "feed" -> {
                    val bytes = call.argument<ByteArray>("pcm")
                    if (bytes != null && bytes.isNotEmpty() && running) {
                        pendingBytes.addAndGet(bytes.size.toLong())
                        queue.offer(bytes)
                        maybeStart(force = false)
                    }
                    result.success(null)
                }
                "kick" -> {
                    // 短句总音频不足预缓冲：强制起播，避免一直闷声
                    maybeStart(force = true)
                    result.success(null)
                }
                "status" -> result.success(status())
                "setSpeaker" -> {
                    route(call.argument<Boolean>("speaker") ?: true)
                    result.success(null)
                }
                "stop" -> {
                    stopTrack()
                    result.success(null)
                }
                "release" -> {
                    stopTrack()
                    resetRoute()
                    result.success(null)
                }
                else -> result.notImplemented()
            }
        } catch (e: Exception) {
            result.error("pcm_stream", e.message, null)
        }
    }

    private fun prepare(sr: Int, speaker: Boolean, prebuffer: Int) {
        stopTrack()
        sampleRate = if (sr > 0) sr else 24000
        prebufferMs = prebuffer.coerceIn(200, 3000)
        route(speaker)
        val minBuf = AudioTrack.getMinBufferSize(
            sampleRate,
            AudioFormat.CHANNEL_OUT_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
        )
        // ~2s 轨缓冲：出块抖动时少欠载
        val bufBytes = maxOf(minBuf * 8, sampleRate * 2 * 2)
        val t = AudioTrack.Builder()
            .setAudioAttributes(
                AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_VOICE_COMMUNICATION)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                    .build(),
            )
            .setAudioFormat(
                AudioFormat.Builder()
                    .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                    .setSampleRate(sampleRate)
                    .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
                    .build(),
            )
            .setBufferSizeInBytes(bufBytes)
            .setTransferMode(AudioTrack.MODE_STREAM)
            .build()
        track = t
        queue.clear()
        pendingBytes.set(0)
        framesWritten.set(0)
        running = true
        started = false
        // 先不 play：writer 把 PCM 写入轨缓冲，攒够再起播
        val th = Thread({ writeLoop(t) }, "pcm-stream-writer")
        writer = th
        th.start()
    }

    private fun prebufferBytes(): Long = (sampleRate.toLong() * 2L * prebufferMs) / 1000L

    private fun maybeStart(force: Boolean) {
        if (started || !running) return
        val t = track ?: return
        if (!force && pendingBytes.get() < prebufferBytes()) return
        if (pendingBytes.get() <= 0) return
        try {
            t.play()
            started = true
        } catch (_: Exception) {
        }
    }

    private fun writeLoop(t: AudioTrack) {
        while (running) {
            // 起播前 PCM 只积在 queue 里（pendingBytes 才是真实预缓冲量）
            if (!started) {
                try {
                    Thread.sleep(20)
                } catch (_: InterruptedException) {
                }
                continue
            }
            val b = try {
                queue.poll(50, TimeUnit.MILLISECONDS)
            } catch (_: InterruptedException) {
                null
            } ?: continue
            var off = 0
            while (off < b.size && running) {
                val n = t.write(b, off, b.size - off)
                if (n <= 0) break
                off += n
            }
            framesWritten.addAndGet((b.size / 2).toLong())
            pendingBytes.addAndGet(-b.size.toLong())
        }
    }

    private fun status(): Map<String, Any> {
        val t = track
        val written = framesWritten.get()
        val head = try {
            (t?.playbackHeadPosition ?: 0).toLong() and 0xffffffffL
        } catch (_: Exception) {
            written
        }
        val pending = pendingBytes.get()
        val drained = !running || (started && pending <= 0 && head >= written - sampleRate / 50)
        return mapOf(
            "drained" to drained,
            "pendingBytes" to pending,
            "framesWritten" to written,
            "head" to head,
            "started" to started,
        )
    }

    private fun stopTrack() {
        running = false
        started = false
        queue.clear()
        pendingBytes.set(0)
        val t = track
        track = null
        writer?.interrupt()
        try {
            writer?.join(300)
        } catch (_: InterruptedException) {
        }
        writer = null
        if (t != null) {
            try {
                t.pause()
                t.flush()
                t.stop()
            } catch (_: Exception) {
            }
            t.release()
        }
    }

    private fun route(speaker: Boolean) {
        try {
            audioManager.mode = AudioManager.MODE_IN_COMMUNICATION
        } catch (_: Exception) {
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            if (speaker) {
                val dev = audioManager.availableCommunicationDevices
                    .firstOrNull { it.type == AudioDeviceInfo.TYPE_BUILTIN_SPEAKER }
                if (dev != null) audioManager.setCommunicationDevice(dev)
            } else {
                audioManager.clearCommunicationDevice()
            }
        } else {
            @Suppress("DEPRECATION")
            audioManager.isSpeakerphoneOn = speaker
        }
    }

    private fun resetRoute() {
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                audioManager.clearCommunicationDevice()
            } else {
                @Suppress("DEPRECATION")
                audioManager.isSpeakerphoneOn = false
            }
            audioManager.mode = AudioManager.MODE_NORMAL
        } catch (_: Exception) {
        }
    }
}
