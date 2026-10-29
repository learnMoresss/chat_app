import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:just_audio/just_audio.dart';

/// 连续 PCM 流播放：块到了就入队，预缓冲后开播，避免整句等完。
///
/// 用 just_audio 无缝拼接短 WAV（每块一帧），预缓冲约 [_prebufferMs] 再 play，
/// 减轻一卡一卡。
class PcmStreamPlayer {
  PcmStreamPlayer();

  static const int _prebufferMs = 280;

  final _player = AudioPlayer();
  ConcatenatingAudioSource? _playlist;
  bool _started = false;
  bool _closed = false;
  bool _generationDone = false;
  int _sampleRate = 24000;
  int _bufferedBytes = 0;
  int _pendingMergeBytes = 0;
  final BytesBuilder _merge = BytesBuilder(copy: false);
  Completer<void>? _drain;

  bool get isPlaying => _player.playing;
  bool get hasAudio => _started || _bufferedBytes > 0 || _pendingMergeBytes > 0;

  Future<void> prepare({int sampleRate = 24000}) async {
    if (_closed) return;
    _sampleRate = sampleRate > 0 ? sampleRate : 24000;
    _started = false;
    _generationDone = false;
    _bufferedBytes = 0;
    _pendingMergeBytes = 0;
    _merge.clear();
    _drain = Completer<void>();
    await _player.stop();
    _playlist = ConcatenatingAudioSource(children: [], useLazyPreparation: true);
    await _player.setAudioSource(_playlist!);
    await _player.setVolume(1.0);
  }

  /// 追加一块 s16le mono PCM（可与上一块合并到约 200ms 再入播放列表）。
  Future<void> addPcm(Uint8List pcm, {int? sampleRate}) async {
    if (_closed || pcm.isEmpty) return;
    if (sampleRate != null && sampleRate > 0) _sampleRate = sampleRate;
    _merge.add(pcm);
    _pendingMergeBytes += pcm.length;
    _bufferedBytes += pcm.length;

    final minMerge = (_sampleRate * 2 * 200) ~/ 1000; // ~200ms
    final prebuffer = (_sampleRate * 2 * _prebufferMs) ~/ 1000;

    if (_pendingMergeBytes < minMerge && !_generationDone && _bufferedBytes < prebuffer) {
      return;
    }
    await _flushMerge();

    if (!_started && _bufferedBytes >= prebuffer) {
      _started = true;
      unawaited(_player.play());
    }
  }

  Future<void> markGenerationDone() async {
    if (_closed) return;
    _generationDone = true;
    await _flushMerge();
    if (!_started && _bufferedBytes > 0) {
      _started = true;
      unawaited(_player.play());
    }
    // 等播完
    if (!_started) {
      _drain?.complete();
      return;
    }
    try {
      await _player.processingStateStream
          .firstWhere((s) => s == ProcessingState.completed)
          .timeout(const Duration(seconds: 120));
    } catch (_) {}
    if (!(_drain?.isCompleted ?? true)) _drain?.complete();
  }

  Future<void> waitUntilDrained() async {
    final d = _drain;
    if (d == null) return;
    await d.future.timeout(const Duration(seconds: 120), onTimeout: () {});
  }

  Future<void> stop() async {
    _merge.clear();
    _pendingMergeBytes = 0;
    _bufferedBytes = 0;
    _started = false;
    _generationDone = true;
    try {
      await _player.stop();
    } catch (_) {}
    if (!(_drain?.isCompleted ?? true)) _drain?.complete();
  }

  Future<void> dispose() async {
    _closed = true;
    await stop();
    await _player.dispose();
  }

  Future<void> _flushMerge() async {
    if (_pendingMergeBytes <= 0 || _playlist == null) return;
    final pcm = Uint8List.fromList(_merge.takeBytes());
    _pendingMergeBytes = 0;
    final wav = _pcm16ToWav(pcm, _sampleRate);
    final src = AudioSource.uri(
      Uri.dataFromBytes(wav, mimeType: 'audio/wav'),
    );
    await _playlist!.add(src);
  }

  static Uint8List _pcm16ToWav(Uint8List pcm, int sampleRate) {
    final dataLen = pcm.length;
    final b = BytesBuilder(copy: false);
    void u32(int v) {
      b.add([v & 255, (v >> 8) & 255, (v >> 16) & 255, (v >> 24) & 255]);
    }

    void u16(int v) {
      b.add([v & 255, (v >> 8) & 255]);
    }

    b.add(ascii.encode('RIFF'));
    u32(36 + dataLen);
    b.add(ascii.encode('WAVE'));
    b.add(ascii.encode('fmt '));
    u32(16);
    u16(1); // PCM
    u16(1); // mono
    u32(sampleRate);
    u32(sampleRate * 2);
    u16(2);
    u16(16);
    b.add(ascii.encode('data'));
    u32(dataLen);
    b.add(pcm);
    return b.takeBytes();
  }
}
