import 'dart:io';
import 'dart:typed_data';

import 'package:audioplayers/audioplayers.dart';
import 'package:path_provider/path_provider.dart';

/// App 全局音频：拉高音量、iOS 走 playback + 扬声器（模拟器才能从 Mac 喇叭出声）。
class AppAudio {
  AppAudio._();

  static bool _ready = false;
  static Directory? _tmp;
  static Directory? _cacheDir;

  static Future<void> ensureConfigured() async {
    if (_ready) return;
    // defaultToSpeaker 只能配 playAndRecord；模拟器才能从 Mac 喇叭出来
    await AudioPlayer.global.setAudioContext(
      AudioContext(
        iOS: AudioContextIOS(
          category: AVAudioSessionCategory.playAndRecord,
          options: const {
            AVAudioSessionOptions.defaultToSpeaker,
            AVAudioSessionOptions.allowBluetooth,
            AVAudioSessionOptions.mixWithOthers,
          },
        ),
        android: const AudioContextAndroid(
          isSpeakerphoneOn: true,
          stayAwake: false,
          contentType: AndroidContentType.speech,
          usageType: AndroidUsageType.media,
          audioFocus: AndroidAudioFocus.gain,
        ),
      ),
    );
    _ready = true;
  }

  static Future<Directory> _ensureCacheDir() async {
    if (_cacheDir != null) return _cacheDir!;
    final root = await getTemporaryDirectory();
    final d = Directory('${root.path}/chat_audio_cache');
    if (!await d.exists()) await d.create(recursive: true);
    _cacheDir = d;
    return d;
  }

  /// 按 fileId 落盘，二次播放免再下网络。
  static Future<File> putCache(String fileId, Uint8List bytes, {String ext = 'wav'}) async {
    final dir = await _ensureCacheDir();
    final safe = fileId.replaceAll(RegExp(r'[^a-zA-Z0-9_-]'), '_');
    final f = File('${dir.path}/$safe.$ext');
    await f.writeAsBytes(bytes, flush: true);
    return f;
  }

  static Future<File?> getCache(String fileId, {String ext = 'wav'}) async {
    final dir = await _ensureCacheDir();
    final safe = fileId.replaceAll(RegExp(r'[^a-zA-Z0-9_-]'), '_');
    final f = File('${dir.path}/$safe.$ext');
    if (await f.exists() && await f.length() > 44) return f;
    return null;
  }

  /// 播放本地文件（已缓存的试听最快）。
  static Future<void> playFile(AudioPlayer player, File file) async {
    await ensureConfigured();
    await player.stop();
    await player.setVolume(1.0);
    await player.setPlaybackRate(1.0);
    await player.play(DeviceFileSource(file.path));
  }

  /// 播放 WAV/PCM 字节；优先临时文件，失败再试 BytesSource。
  static Future<void> playBytes(
    AudioPlayer player,
    Uint8List bytes, {
    String ext = 'wav',
    String? mimeType,
    String? cacheFileId,
  }) async {
    await ensureConfigured();
    await player.stop();
    await player.setVolume(1.0);
    await player.setPlaybackRate(1.0);
    File f;
    if (cacheFileId != null && cacheFileId.isNotEmpty) {
      f = await putCache(cacheFileId, bytes, ext: ext);
    } else {
      _tmp ??= await Directory.systemTemp.createTemp('app_audio_');
      f = File('${_tmp!.path}/play_${DateTime.now().microsecondsSinceEpoch}.$ext');
      await f.writeAsBytes(bytes, flush: true);
    }
    try {
      await player.play(DeviceFileSource(f.path));
    } catch (_) {
      await player.play(BytesSource(bytes, mimeType: mimeType ?? 'audio/$ext'));
    }
  }

  static Future<void> disposeTemp() async {
    final d = _tmp;
    _tmp = null;
    if (d == null) return;
    try {
      await d.delete(recursive: true);
    } catch (_) {}
  }
}
