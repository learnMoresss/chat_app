import 'dart:async';
import 'dart:io';
import 'dart:typed_data';

import 'package:path/path.dart' as p;
import 'package:path_provider/path_provider.dart';
import 'package:record/record.dart';

/// 麦克风录音：开始 / 结束，返回本地文件与时长；支持振幅流做 VAD。
///
/// - [start]：普通语音消息，AAC m4a
/// - [startCall]：通话半双工，16k 单声道 WAV + 通话模式 AEC（可边播边听）
class VoiceRecorder {
  VoiceRecorder._();
  static final AudioRecorder _rec = AudioRecorder();
  static DateTime? _started;
  static String? _path;

  /// 通话录音配置：VOICE_COMMUNICATION + MODE_IN_COMMUNICATION + AEC/NS/AGC。
  static const RecordConfig callConfig = RecordConfig(
    encoder: AudioEncoder.wav,
    sampleRate: 16000,
    numChannels: 1,
    bitRate: 256000,
    autoGain: true,
    echoCancel: true,
    noiseSuppress: true,
    androidConfig: AndroidRecordConfig(
      audioSource: AndroidAudioSource.voiceCommunication,
      audioManagerMode: AudioManagerMode.modeInCommunication,
      speakerphone: true,
      manageBluetooth: true,
    ),
    iosConfig: IosRecordConfig(
      categoryOptions: [
        IosAudioCategoryOption.defaultToSpeaker,
        IosAudioCategoryOption.allowBluetooth,
        IosAudioCategoryOption.allowBluetoothA2DP,
      ],
    ),
    audioInterruption: AudioInterruptionMode.none,
  );

  static Future<bool> ensurePermission() => _rec.hasPermission();

  static Future<bool> isRecording() => _rec.isRecording();

  /// dBFS 振幅流（通话页 VAD / 插话用）。需在 start 之后订阅。
  static Stream<Amplitude> onAmplitude([Duration interval = const Duration(milliseconds: 50)]) {
    return _rec.onAmplitudeChanged(interval);
  }

  static Future<void> start() async {
    final ok = await ensurePermission();
    if (!ok) {
      throw StateError('没有麦克风权限');
    }
    final dir = await getTemporaryDirectory();
    _path = p.join(dir.path, 'voice_${DateTime.now().millisecondsSinceEpoch}.m4a');
    _started = DateTime.now();
    await _rec.start(
      const RecordConfig(encoder: AudioEncoder.aacLc, bitRate: 128000, sampleRate: 44100),
      path: _path!,
    );
  }

  /// 通话专用：16k mono WAV + 系统通话 AEC，停录无 AAC 收尾。
  static Future<void> startCall() async {
    final ok = await ensurePermission();
    if (!ok) {
      throw StateError('没有麦克风权限');
    }
    final dir = await getTemporaryDirectory();
    _path = p.join(dir.path, 'call_${DateTime.now().millisecondsSinceEpoch}.wav');
    _started = DateTime.now();
    await _rec.start(callConfig, path: _path!);
  }

  static Future<({File file, int seconds})?> stop({bool cancel = false}) async {
    final started = _started;
    final path = _path;
    _started = null;
    _path = null;
    final out = await _rec.stop();
    if (cancel || path == null || started == null) {
      if (path != null) {
        try {
          File(path).deleteSync();
        } catch (_) {}
      }
      return null;
    }
    final file = File(out ?? path);
    if (!file.existsSync() || file.lengthSync() < 64) {
      try {
        file.deleteSync();
      } catch (_) {}
      return null;
    }
    final secs = DateTime.now().difference(started).inMilliseconds;
    final seconds = (secs / 1000).ceil().clamp(1, 60);
    return (file: file, seconds: seconds);
  }

  /// 合并两段 16k mono PCM WAV（乐观重发用）。失败返回 null。
  static Uint8List? mergeWavBytes(Uint8List a, Uint8List b) {
    final pcmA = _pcmFromWav(a);
    final pcmB = _pcmFromWav(b);
    if (pcmA == null || pcmB == null) return null;
    final pcm = BytesBuilder(copy: false)
      ..add(pcmA)
      ..add(pcmB);
    return _wrapWav(pcm.takeBytes(), 16000);
  }

  static Uint8List? _pcmFromWav(Uint8List wav) {
    if (wav.length < 44) return null;
    var i = 12;
    while (i + 8 <= wav.length) {
      final id = String.fromCharCodes(wav.sublist(i, i + 4));
      final size = wav[i + 4] | (wav[i + 5] << 8) | (wav[i + 6] << 16) | (wav[i + 7] << 24);
      if (id == 'data') {
        final start = i + 8;
        final end = (start + size).clamp(0, wav.length);
        return wav.sublist(start, end);
      }
      i += 8 + size;
      if (size.isOdd) i += 1;
    }
    return wav.sublist(44);
  }

  static Uint8List _wrapWav(Uint8List pcm, int sampleRate) {
    final dataLen = pcm.length;
    final b = BytesBuilder(copy: false);
    void u32(int v) => b.add([v & 255, (v >> 8) & 255, (v >> 16) & 255, (v >> 24) & 255]);
    void u16(int v) => b.add([v & 255, (v >> 8) & 255]);
    b.add('RIFF'.codeUnits);
    u32(36 + dataLen);
    b.add('WAVE'.codeUnits);
    b.add('fmt '.codeUnits);
    u32(16);
    u16(1);
    u16(1);
    u32(sampleRate);
    u32(sampleRate * 2);
    u16(2);
    u16(16);
    b.add('data'.codeUnits);
    u32(dataLen);
    b.add(pcm);
    return b.takeBytes();
  }
}
