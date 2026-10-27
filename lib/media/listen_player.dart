import 'dart:async';
import 'dart:io';
import 'dart:ui' show Color;

import 'package:audio_service/audio_service.dart';
import 'package:just_audio/just_audio.dart';

/// 经 Dart HttpClient 拉流（可信任自签），供 iOS AVPlayer / Android Exo 播放。
class _TrustedHttpSource extends StreamAudioSource {
  _TrustedHttpSource(this.uri, {this.headers = const {}});

  final Uri uri;
  final Map<String, String> headers;

  static HttpClient _client() {
    final c = HttpClient();
    c.badCertificateCallback =
        (cert, host, port) => host == '119.45.30.73' || host.endsWith('.local');
    c.connectionTimeout = const Duration(seconds: 20);
    return c;
  }

  @override
  Future<StreamAudioResponse> request([int? start, int? end]) async {
    final client = _client();
    try {
      final req = await client.getUrl(uri);
      final hasTok = uri.queryParameters.containsKey('access_token') ||
          uri.queryParameters.containsKey('token');
      if (!hasTok) {
        headers.forEach(req.headers.set);
      }
      if (start != null || end != null) {
        final s = start ?? 0;
        final e = end != null ? (end - 1) : '';
        req.headers.set(HttpHeaders.rangeHeader, 'bytes=$s-$e');
      }
      final res = await req.close().timeout(const Duration(seconds: 60));
      if (res.statusCode < 200 || res.statusCode >= 300) {
        final body = await res.fold<List<int>>(<int>[], (a, b) => a..addAll(b));
        throw HttpException(
          '连接不到音乐（HTTP ${res.statusCode}） ${String.fromCharCodes(body.take(80))}',
          uri: uri,
        );
      }
      final lenHeader = res.headers.value(HttpHeaders.contentLengthHeader);
      final contentLength = lenHeader == null ? null : int.tryParse(lenHeader);
      int? sourceLength = contentLength;
      final cr = res.headers.value(HttpHeaders.contentRangeHeader);
      if (cr != null && cr.contains('/')) {
        final total = int.tryParse(cr.split('/').last.trim());
        if (total != null && total > 0) sourceLength = total;
      }
      final mime = res.headers.contentType?.mimeType ?? 'audio/mpeg';
      return StreamAudioResponse(
        sourceLength: sourceLength,
        contentLength: contentLength,
        offset: start,
        stream: res,
        contentType: mime,
      );
    } catch (e) {
      client.close(force: true);
      rethrow;
    }
  }
}

/// Android / iOS 系统「正在播放」：MediaSession + 通知栏控制。
///
/// iOS AVPlayer 不走 Dart [HttpOverrides]，公网自签会握手失败 → 用 [_TrustedHttpSource]。
class ListenAudioHandler extends BaseAudioHandler with QueueHandler, SeekHandler {
  ListenAudioHandler() {
    _player.playbackEventStream.listen(_broadcastState);
    _player.processingStateStream.listen((state) {
      if (state == ProcessingState.completed) {
        skipToNext();
      }
    });
  }

  final AudioPlayer _player = AudioPlayer();
  final List<MediaItem> _queue = [];
  Map<String, String> _headers = const {};
  String Function(String trackId)? _urlFor;

  Future<void> loadPlaylist({
    required List<MediaItem> items,
    required Map<String, String> headers,
    required String Function(String trackId) urlFor,
    int startIndex = 0,
  }) async {
    _headers = headers;
    _urlFor = urlFor;
    _queue
      ..clear()
      ..addAll(items);
    queue.add(_queue);
    final i = startIndex.clamp(0, _queue.isEmpty ? 0 : _queue.length - 1);
    if (_queue.isEmpty) return;
    await _playIndex(i);
  }

  bool _needsTrustedStream(Uri uri) {
    if (Platform.isIOS) return true;
    final host = uri.host;
    return host == '119.45.30.73' || host.endsWith('.local');
  }

  Future<void> _playIndex(int index) async {
    if (index < 0 || index >= _queue.length) return;
    final item = _queue[index];
    mediaItem.add(item);
    final url = _urlFor?.call(item.id) ?? '';
    if (url.isEmpty) return;
    final uri = Uri.parse(url);
    try {
      if (_needsTrustedStream(uri)) {
        await _player.setAudioSource(_TrustedHttpSource(uri, headers: _headers));
      } else {
        await _player.setAudioSource(
          AudioSource.uri(
            uri,
            headers: _headers.isEmpty ? null : _headers,
          ),
        );
      }
      await _player.play();
    } catch (e) {
      playbackState.add(
        playbackState.value.copyWith(
          processingState: AudioProcessingState.error,
          playing: false,
          errorMessage: '$e',
        ),
      );
      rethrow;
    }
  }

  @override
  Future<void> play() => _player.play();

  @override
  Future<void> pause() => _player.pause();

  @override
  Future<void> stop() async {
    await _player.stop();
    await super.stop();
  }

  @override
  Future<void> seek(Duration position) => _player.seek(position);

  @override
  Future<void> skipToNext() async {
    final cur = mediaItem.value;
    if (cur == null || _queue.isEmpty) return;
    final i = _queue.indexWhere((e) => e.id == cur.id);
    if (i < 0 || i + 1 >= _queue.length) return;
    await _playIndex(i + 1);
  }

  @override
  Future<void> skipToPrevious() async {
    final cur = mediaItem.value;
    if (cur == null || _queue.isEmpty) return;
    final i = _queue.indexWhere((e) => e.id == cur.id);
    if (i <= 0) return;
    await _playIndex(i - 1);
  }

  @override
  Future<void> skipToQueueItem(int index) => _playIndex(index);

  void _broadcastState(PlaybackEvent event) {
    final playing = _player.playing;
    playbackState.add(
      playbackState.value.copyWith(
        controls: [
          MediaControl.skipToPrevious,
          if (playing) MediaControl.pause else MediaControl.play,
          MediaControl.skipToNext,
          MediaControl.stop,
        ],
        systemActions: const {
          MediaAction.seek,
          MediaAction.seekForward,
          MediaAction.seekBackward,
        },
        androidCompactActionIndices: const [0, 1, 2],
        processingState: const {
          ProcessingState.idle: AudioProcessingState.idle,
          ProcessingState.loading: AudioProcessingState.loading,
          ProcessingState.buffering: AudioProcessingState.buffering,
          ProcessingState.ready: AudioProcessingState.ready,
          ProcessingState.completed: AudioProcessingState.completed,
        }[_player.processingState]!,
        playing: playing,
        updatePosition: _player.position,
        bufferedPosition: _player.bufferedPosition,
        speed: _player.speed,
        queueIndex: () {
          final cur = mediaItem.value;
          if (cur == null) return null;
          final i = _queue.indexWhere((e) => e.id == cur.id);
          return i >= 0 ? i : null;
        }(),
      ),
    );
  }
}

ListenAudioHandler? _handler;

Future<ListenAudioHandler> ensureListenHandler() async {
  if (_handler != null) return _handler!;
  // audio_service → 系统 MediaSession：控制中心 / 通知栏 / 锁屏 / 小米妙播
  // ongoing 与 stopForegroundOnPause 不能同时「ongoing=true + pause不清前台」
  _handler = await AudioService.init(
    builder: () => ListenAudioHandler(),
    config: const AudioServiceConfig(
      androidNotificationChannelId: 'com.chat_app.listen',
      androidNotificationChannelName: '一起听歌',
      androidNotificationOngoing: false,
      androidStopForegroundOnPause: false,
      androidShowNotificationBadge: true,
      notificationColor: Color(0xFF2A1840),
      fastForwardInterval: Duration(seconds: 10),
      rewindInterval: Duration(seconds: 10),
    ),
  );
  return _handler!;
}
