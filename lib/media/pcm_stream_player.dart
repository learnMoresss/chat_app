import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:flutter/services.dart';
import 'package:just_audio/just_audio.dart';

/// 连续 PCM 流播放：块到了就入队，生成快慢都不断播。
///
/// - Android：原生 AudioTrack（MODE_STREAM + 通话用途），欠载只是静音，
///   后续块到了接着播；通话用途让系统 AEC 拿到参考信号。
/// - 其他平台：just_audio 拼接短 WAV。
class PcmStreamPlayer {
  PcmStreamPlayer() : _impl = Platform.isAndroid ? _NativePcm() : _JustAudioPcm();

  final _PcmImpl _impl;

  bool get hasAudio => _impl.hasAudio;

  Future<void> prepare({
    int sampleRate = 24000,
    bool speaker = true,
    int prebufferMs = 1400,
  }) =>
      _impl.prepare(sampleRate: sampleRate, speaker: speaker, prebufferMs: prebufferMs);

  Future<void> addPcm(Uint8List pcm, {int? sampleRate}) => _impl.addPcm(pcm, sampleRate: sampleRate);

  /// 服务端已发完；短句不足预缓冲时强制起播，再 [waitUntilDrained]。
  Future<void> markGenerationDone() => _impl.markGenerationDone();

  Future<void> waitUntilDrained() => _impl.waitUntilDrained();

  Future<void> setSpeaker(bool on) => _impl.setSpeaker(on);

  Future<void> stop() => _impl.stop();

  Future<void> dispose() => _impl.dispose();
}

abstract class _PcmImpl {
  bool get hasAudio;
  Future<void> prepare({
    required int sampleRate,
    required bool speaker,
    int prebufferMs = 1400,
  });
  Future<void> addPcm(Uint8List pcm, {int? sampleRate});
  Future<void> markGenerationDone();
  Future<void> waitUntilDrained();
  Future<void> setSpeaker(bool on);
  Future<void> stop();
  Future<void> dispose();
}

class _NativePcm implements _PcmImpl {
  static const _ch = MethodChannel('huanxin/pcm_stream');

  int _sampleRate = 24000;
  int _fedBytes = 0;
  int _gen = 0;
  bool _prepared = false;

  @override
  bool get hasAudio => _fedBytes > 0;

  @override
  Future<void> prepare({
    required int sampleRate,
    required bool speaker,
    int prebufferMs = 1400,
  }) async {
    _gen++;
    _sampleRate = sampleRate > 0 ? sampleRate : 24000;
    _fedBytes = 0;
    await _ch.invokeMethod('prepare', {
      'sampleRate': _sampleRate,
      'speaker': speaker,
      'prebufferMs': prebufferMs,
    });
    _prepared = true;
  }

  @override
  Future<void> addPcm(Uint8List pcm, {int? sampleRate}) async {
    if (!_prepared || pcm.isEmpty) return;
    _fedBytes += pcm.length;
    await _ch.invokeMethod('feed', {'pcm': pcm});
  }

  @override
  Future<void> markGenerationDone() async {
    if (!_prepared) return;
    try {
      await _ch.invokeMethod('kick');
    } catch (_) {}
  }

  @override
  Future<void> waitUntilDrained() async {
    if (!_prepared) return;
    final gen = _gen;
    // 上限：已送入音频时长 + 5s 余量，防止状态异常时卡死
    final audioMs = (_fedBytes * 1000) ~/ (_sampleRate * 2);
    final deadline = DateTime.now().add(Duration(milliseconds: audioMs + 5000));
    while (gen == _gen && _prepared && DateTime.now().isBefore(deadline)) {
      try {
        final s = await _ch.invokeMapMethod<String, dynamic>('status');
        if (s == null || s['drained'] == true) return;
      } catch (_) {
        return;
      }
      await Future<void>.delayed(const Duration(milliseconds: 80));
    }
  }

  @override
  Future<void> setSpeaker(bool on) async {
    try {
      await _ch.invokeMethod('setSpeaker', {'speaker': on});
    } catch (_) {}
  }

  @override
  Future<void> stop() async {
    _gen++;
    _prepared = false;
    _fedBytes = 0;
    try {
      await _ch.invokeMethod('stop');
    } catch (_) {}
  }

  @override
  Future<void> dispose() async {
    _gen++;
    _prepared = false;
    try {
      await _ch.invokeMethod('release');
    } catch (_) {}
  }
}

/// just_audio 拼接短 WAV；播完已入队部分会进入 completed，
/// 后续块追加时要手动续播，结束判断以「全部块已入队且播到最后一块末尾」为准。
class _JustAudioPcm implements _PcmImpl {
  /// 与 Android 一致：覆盖 CosyVoice RTF>1 的块间空窗。
  int _prebufferMs = 1400;

  final _player = AudioPlayer();
  ConcatenatingAudioSource? _playlist;
  bool _started = false;
  bool _closed = false;
  bool _generationDone = false;
  int _sampleRate = 24000;
  int _bufferedBytes = 0;
  int _pendingMergeBytes = 0;
  final BytesBuilder _merge = BytesBuilder(copy: false);

  @override
  bool get hasAudio => _started || _bufferedBytes > 0 || _pendingMergeBytes > 0;

  @override
  Future<void> prepare({
    required int sampleRate,
    required bool speaker,
    int prebufferMs = 1400,
  }) async {
    if (_closed) return;
    _sampleRate = sampleRate > 0 ? sampleRate : 24000;
    _prebufferMs = prebufferMs.clamp(200, 3000);
    _started = false;
    _generationDone = false;
    _bufferedBytes = 0;
    _pendingMergeBytes = 0;
    _merge.clear();
    await _player.stop();
    _playlist = ConcatenatingAudioSource(children: [], useLazyPreparation: true);
    await _player.setAudioSource(_playlist!);
    await _player.setVolume(1.0);
  }

  @override
  Future<void> addPcm(Uint8List pcm, {int? sampleRate}) async {
    if (_closed || pcm.isEmpty) return;
    if (sampleRate != null && sampleRate > 0) _sampleRate = sampleRate;
    _merge.add(pcm);
    _pendingMergeBytes += pcm.length;
    _bufferedBytes += pcm.length;

    final minMerge = (_sampleRate * 2 * 200) ~/ 1000;
    final prebuffer = (_sampleRate * 2 * _prebufferMs) ~/ 1000;
    if (_pendingMergeBytes < minMerge && _bufferedBytes < prebuffer) return;
    await _flushMerge();
    if (!_started && _bufferedBytes >= prebuffer) {
      _started = true;
      unawaited(_player.play());
    }
  }

  @override
  Future<void> markGenerationDone() async {
    if (_closed) return;
    _generationDone = true;
    await _flushMerge();
    if (!_started && _bufferedBytes > 0) {
      _started = true;
      unawaited(_player.play());
    }
  }

  @override
  Future<void> waitUntilDrained() async {
    if (!_started || _playlist == null) return;
    final lastIndex = _playlist!.length - 1;
    try {
      await _player.playerStateStream
          .firstWhere((s) =>
              !_started ||
              (_generationDone &&
                  s.processingState == ProcessingState.completed &&
                  (_player.currentIndex ?? 0) >= lastIndex))
          .timeout(const Duration(seconds: 120));
    } catch (_) {}
  }

  @override
  Future<void> setSpeaker(bool on) async {}

  @override
  Future<void> stop() async {
    _merge.clear();
    _pendingMergeBytes = 0;
    _bufferedBytes = 0;
    _started = false;
    _generationDone = true;
    try {
      await _player.stop();
    } catch (_) {}
  }

  @override
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
    final wasCompleted = _player.processingState == ProcessingState.completed;
    await _playlist!.add(AudioSource.uri(Uri.dataFromBytes(wav, mimeType: 'audio/wav')));
    if (_started && wasCompleted) {
      // 已播完旧块：跳到新追加的块继续播
      await _player.seek(Duration.zero, index: _playlist!.length - 1);
      unawaited(_player.play());
    }
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
    u16(1);
    u16(1);
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
