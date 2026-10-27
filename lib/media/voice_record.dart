import 'dart:io';

import 'package:path/path.dart' as p;
import 'package:path_provider/path_provider.dart';
import 'package:record/record.dart';

/// 按住说话：开始 / 结束录音，返回本地 m4a 路径与时长。
class VoiceRecorder {
  VoiceRecorder._();
  static final AudioRecorder _rec = AudioRecorder();
  static DateTime? _started;
  static String? _path;

  static Future<bool> ensurePermission() => _rec.hasPermission();

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
}
