import 'dart:async';
import 'dart:ui' show Offset;

import 'package:audio_service/audio_service.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter/widgets.dart';

import 'listen_player.dart';

/// 悬浮窗形态：最小唱片圆 / 带控件面板（全屏由 pageOpen + mini==null 表示）。
enum ListenPipMode { disc, panel }

/// App 内一起听：全屏 / 悬浮小窗共享状态（音乐不停）。
class ListenMiniState {
  const ListenMiniState({
    required this.sessionId,
    this.convId,
    this.personaName,
    this.title = '',
    this.artist = '',
    this.playing = false,
    this.artUri,
    this.mode = ListenPipMode.panel,
  });

  final String sessionId;
  final String? convId;
  final String? personaName;
  final String title;
  final String artist;
  final bool playing;
  final Uri? artUri;
  final ListenPipMode mode;

  ListenMiniState copyWith({
    String? title,
    String? artist,
    bool? playing,
    Uri? artUri,
    bool clearArtUri = false,
    ListenPipMode? mode,
  }) =>
      ListenMiniState(
        sessionId: sessionId,
        convId: convId,
        personaName: personaName,
        title: title ?? this.title,
        artist: artist ?? this.artist,
        playing: playing ?? this.playing,
        artUri: clearArtUri ? null : (artUri ?? this.artUri),
        mode: mode ?? this.mode,
      );
}

class ListenSessionController extends ChangeNotifier {
  ListenSessionController._();
  static final ListenSessionController instance = ListenSessionController._();

  /// MaterialApp 根导航；浮窗在 builder 外层，必须用这个 key push 全屏。
  final GlobalKey<NavigatorState> navigatorKey = GlobalKey<NavigatorState>();

  ListenMiniState? mini;
  bool pageOpen = false;

  /// 浮窗左上角屏幕坐标；null 表示用默认右下角。
  Offset? pipAnchor;

  StreamSubscription? _itemSub;
  StreamSubscription? _playSub;
  String? _boundSessionId;

  /// 当前绑定的一起听 session（全屏或浮窗）。
  String? get boundSessionId => _boundSessionId ?? mini?.sessionId;

  Future<void> bindPlayback({
    required String sessionId,
    String? convId,
    String? personaName,
  }) async {
    pageOpen = true;
    mini = null;
    _boundSessionId = sessionId;
    notifyListeners();
    await _itemSub?.cancel();
    await _playSub?.cancel();
    final h = await ensureListenHandler();
    _itemSub = h.mediaItem.listen((m) {
      if (_boundSessionId != sessionId) return;
      final cur = mini;
      if (cur == null) return;
      mini = cur.copyWith(
        title: m?.title ?? cur.title,
        artist: m?.artist ?? cur.artist,
        artUri: m?.artUri,
        clearArtUri: m != null && m.artUri == null,
      );
      notifyListeners();
    });
    _playSub = h.playbackState.listen((s) {
      if (_boundSessionId != sessionId) return;
      final cur = mini;
      if (cur == null) return;
      mini = cur.copyWith(playing: s.playing);
      notifyListeners();
    });
  }

  /// 左上角：收起为悬浮窗（默认 panel），音乐继续。
  Future<void> minimize({
    required String sessionId,
    String? convId,
    String? personaName,
    String title = '',
    String artist = '',
    bool playing = false,
    Uri? artUri,
    ListenPipMode mode = ListenPipMode.panel,
  }) async {
    pageOpen = false;
    mini = ListenMiniState(
      sessionId: sessionId,
      convId: convId,
      personaName: personaName,
      title: title,
      artist: artist,
      playing: playing,
      artUri: artUri,
      mode: mode,
    );
    notifyListeners();
  }

  /// 翻唱 / 单曲：直接走一起听悬浮窗（带播放控件），不建听歌会话。
  Future<void> playCoverInPip({
    required String sessionId,
    required String audioUrl,
    Map<String, String> headers = const {},
    String? convId,
    String? personaName,
    String title = '',
    String artist = '',
    Duration? duration,
  }) async {
    pageOpen = false;
    _boundSessionId = sessionId;
    mini = ListenMiniState(
      sessionId: sessionId,
      convId: convId,
      personaName: personaName,
      title: title,
      artist: artist.isEmpty ? '翻唱' : artist,
      playing: true,
      mode: ListenPipMode.panel,
    );
    notifyListeners();

    await _itemSub?.cancel();
    await _playSub?.cancel();
    final h = await ensureListenHandler();
    _itemSub = h.mediaItem.listen((m) {
      if (_boundSessionId != sessionId) return;
      final cur = mini;
      if (cur == null) return;
      mini = cur.copyWith(
        title: m?.title ?? cur.title,
        artist: m?.artist ?? cur.artist,
        artUri: m?.artUri,
        clearArtUri: m != null && m.artUri == null,
      );
      notifyListeners();
    });
    _playSub = h.playbackState.listen((s) {
      if (_boundSessionId != sessionId) return;
      final cur = mini;
      if (cur == null) return;
      mini = cur.copyWith(playing: s.playing);
      notifyListeners();
    });

    final item = MediaItem(
      id: sessionId,
      title: title.isEmpty ? '翻唱' : title,
      artist: artist.isEmpty ? (personaName ?? '翻唱') : artist,
      duration: duration,
    );
    await h.loadPlaylist(
      items: [item],
      headers: headers,
      urlFor: (_) => audioUrl,
    );
  }

  void setPipMode(ListenPipMode mode) {
    final cur = mini;
    if (cur == null || cur.mode == mode) return;
    mini = cur.copyWith(mode: mode);
    notifyListeners();
  }

  void setPipAnchor(Offset anchor) {
    pipAnchor = anchor;
    notifyListeners();
  }

  /// 点悬浮窗放大：回到全屏（清掉小窗标记，页打开后不再叠小窗）。
  void expand() {
    pageOpen = true;
    mini = null;
    notifyListeners();
  }

  /// 右上角关闭：结束会话侧由页面调 API；这里停播放并清状态。
  Future<void> closeAndStop() async {
    pageOpen = false;
    mini = null;
    _boundSessionId = null;
    await _itemSub?.cancel();
    await _playSub?.cancel();
    _itemSub = null;
    _playSub = null;
    try {
      final h = await ensureListenHandler();
      await h.stop();
    } catch (_) {}
    notifyListeners();
  }
}
