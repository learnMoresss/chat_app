import 'dart:async';
import 'dart:ui';

import 'package:audio_service/audio_service.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../api/api_client.dart';
import '../media/listen_player.dart';
import '../media/listen_session.dart';
import '../models.dart';
import '../store.dart';
import '../theme.dart';
import 'listen_song_picker.dart';

Uri? _trackArtUri(Map<String, dynamic> t) {
  for (final k in const [
    'cover_url',
    'artwork_url',
    'image_url',
    'cover',
    'artwork',
    'art_uri',
  ]) {
    final v = t[k]?.toString().trim();
    if (v == null || v.isEmpty) continue;
    final u = Uri.tryParse(v);
    if (u != null && u.hasScheme) return u;
  }
  return null;
}

/// 仿抖音「一起看」的一起听页：暗底、歌单；左上收起小窗，右上关闭。
class ListenTogetherPage extends StatefulWidget {
  const ListenTogetherPage({
    super.key,
    required this.store,
    required this.sessionId,
    this.convId,
    this.personaName,
    this.resumeOnly = false,
  });

  final AppStore store;
  final String sessionId;
  final String? convId;
  final String? personaName;
  /// 从悬浮窗回来：不重拉歌单，只接回全屏。
  final bool resumeOnly;

  @override
  State<ListenTogetherPage> createState() => _ListenTogetherPageState();
}

class _ListenTogetherPageState extends State<ListenTogetherPage> {
  static const _bg = Color(0xFF0B0B0F);
  static const _top = Color(0xFF2A1840);

  Map<String, dynamic>? session;
  List<Map<String, dynamic>> tracks = [];
  bool loading = true;
  String? error;
  ListenAudioHandler? handler;
  StreamSubscription? _itemSub;
  StreamSubscription? _playSub;
  MediaItem? current;
  bool playing = false;
  final _chatBubbles = <_ListenChatBubble>[];
  final _seenBubbleMsgIds = <String>{};
  Timer? _bubbleTick;
  bool _composerOpen = false;
  final _composerCtrl = TextEditingController();
  final _composerFocus = FocusNode();

  ApiClient? get api => widget.store.api;

  @override
  void initState() {
    super.initState();
    // 进页时已有消息一律视为「看过」：只展示之后新来的气泡，避免收起再进又播一遍
    final convId = widget.convId;
    if (convId != null) {
      for (final m in widget.store.msgs(convId)) {
        _seenBubbleMsgIds.add(m.id);
      }
    }
    widget.store.addListener(_onStoreChat);
    _bubbleTick = Timer.periodic(const Duration(milliseconds: 200), (_) {
      if (!mounted || _chatBubbles.isEmpty) return;
      final now = DateTime.now();
      var changed = false;
      for (final b in _chatBubbles) {
        final age = now.difference(b.at).inMilliseconds;
        // 约 4.2s 开始淡出，5s 后移除
        if (!b.fading && age >= 4200) {
          b.fading = true;
          changed = true;
        }
      }
      final before = _chatBubbles.length;
      _chatBubbles.removeWhere((b) => now.difference(b.at).inMilliseconds >= 5200);
      if (_chatBubbles.length != before || changed) setState(() {});
    });
    unawaited(_boot());
  }

  Future<void> _boot() async {
    final client = api;
    if (client == null) {
      setState(() {
        loading = false;
        error = '未连接服务器';
      });
      return;
    }
    try {
      final h = await ensureListenHandler();
      handler = h;
      await ListenSessionController.instance.bindPlayback(
        sessionId: widget.sessionId,
        convId: widget.convId,
        personaName: widget.personaName,
      );

      if (widget.resumeOnly) {
        final raw = await client.getListenSession(widget.sessionId);
        final list = (raw['tracks'] as List? ?? const [])
            .whereType<Map>()
            .map((e) => Map<String, dynamic>.from(e))
            .toList();
        _wireHandler(h, list, client);
        if (!mounted) return;
        setState(() {
          session = Map<String, dynamic>.from(raw['session'] as Map? ?? {});
          tracks = list;
          current = h.mediaItem.value;
          playing = h.playbackState.value.playing;
          loading = false;
        });
        return;
      }

      var raw = await client.getListenSession(widget.sessionId);
      var sess = Map<String, dynamic>.from(raw['session'] as Map? ?? {});
      if ((sess['status'] as String?) == 'invited') {
        raw = await client.acceptListenSession(widget.sessionId);
        sess = Map<String, dynamic>.from(raw['session'] as Map? ?? {});
      }
      final list = (raw['tracks'] as List? ?? const [])
          .whereType<Map>()
          .map((e) => Map<String, dynamic>.from(e))
          .toList();
      final items = [
        for (final t in list)
          MediaItem(
            id: t['id']?.toString() ?? '',
            title: t['title']?.toString() ?? '未知曲目',
            artist: (t['artist']?.toString().isNotEmpty == true)
                ? t['artist'].toString()
                : (widget.personaName ?? '一起听'),
            duration: t['duration_sec'] is num
                ? Duration(seconds: (t['duration_sec'] as num).round())
                : null,
            artUri: _trackArtUri(t),
          ),
      ].where((e) => e.id.isNotEmpty).toList();
      final start = (sess['track_index'] as num?)?.toInt() ?? 0;
      if (items.isEmpty) {
        if (!mounted) return;
        setState(() {
          session = sess;
          tracks = list;
          loading = false;
          error = '曲库里暂时抽不到歌，请稍后再试或先在运维里扫描曲库';
        });
        return;
      }
      // 先出歌单 UI，音频可能要经公网缓冲，避免一直转圈像「连不上」
      _wireHandler(h, list, client);
      if (!mounted) return;
      setState(() {
        session = sess;
        tracks = list;
        loading = false;
        current = items[start.clamp(0, items.length - 1)];
      });
      try {
        await h.loadPlaylist(
          items: items,
          headers: client.authHeaders(),
          urlFor: client.musicTrackAudioUrl,
          startIndex: start,
        );
      } catch (e) {
        if (!mounted) return;
        setState(() {
          error = '连接不到音乐：$e';
        });
      }
    } catch (e) {
      if (!mounted) return;
      setState(() {
        loading = false;
        error = '$e';
      });
    }
  }

  void _wireHandler(ListenAudioHandler h, List<Map<String, dynamic>> list, ApiClient client) {
    _itemSub?.cancel();
    _playSub?.cancel();
    _itemSub = h.mediaItem.listen((m) {
      if (!mounted) return;
      setState(() => current = m);
      final idx = list.indexWhere((e) => e['id']?.toString() == m?.id);
      if (idx >= 0) {
        unawaited(client.patchListenSession(widget.sessionId, trackIndex: idx, status: 'active'));
      }
    });
    _playSub = h.playbackState.listen((s) {
      if (!mounted) return;
      setState(() => playing = s.playing);
    });
  }

  void _onStoreChat() {
    final convId = widget.convId;
    if (convId == null || !mounted) return;
    final list = widget.store.msgs(convId);
    if (list.isEmpty) return;
    final last = list.last;
    if (last.kind != MsgKind.text) return;
    final text = last.text.trim();
    if (text.isEmpty) return;
    final idx = _chatBubbles.indexWhere((b) => b.msgId == last.id);
    if (idx >= 0) {
      // 流式同条：只更新文案，不重播入场、不叠旧句
      if (_chatBubbles[idx].text == text) return;
      setState(() {
        _chatBubbles[idx].text = text;
        _chatBubbles[idx].at = DateTime.now();
        _chatBubbles[idx].fading = false;
      });
      return;
    }
    // 已展示过并淡出的同条消息：忽略后续流式整段（避免 aaa 消失后又冒出 aaa.bbb）
    if (_seenBubbleMsgIds.contains(last.id)) return;
    _seenBubbleMsgIds.add(last.id);
    setState(() {
      _chatBubbles.add(_ListenChatBubble(
        msgId: last.id,
        text: text,
        at: DateTime.now(),
        mine: last.mine,
      ));
      if (_chatBubbles.length > 4) {
        final dropped = _chatBubbles.removeAt(0);
        _seenBubbleMsgIds.add(dropped.msgId);
      }
    });
  }

  void _openComposer() {
    if (_composerOpen) return;
    setState(() => _composerOpen = true);
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) _composerFocus.requestFocus();
    });
  }

  void _closeComposer() {
    if (!_composerOpen) return;
    _composerFocus.unfocus();
    setState(() => _composerOpen = false);
  }

  void _sendComposer() {
    final text = _composerCtrl.text.trim();
    final convId = widget.convId;
    if (text.isEmpty || convId == null) return;
    final conv = widget.store.conv(convId);
    if (conv == null) return;
    widget.store.sendText(conv, text);
    _composerCtrl.clear();
    // 发送后保持输入条，方便边听边聊；点空白可收起
  }

  @override
  void dispose() {
    widget.store.removeListener(_onStoreChat);
    _bubbleTick?.cancel();
    _itemSub?.cancel();
    _playSub?.cancel();
    _composerCtrl.dispose();
    _composerFocus.dispose();
    super.dispose();
  }

  Future<void> _close() async {
    try {
      await api?.patchListenSession(widget.sessionId, status: 'ended');
    } catch (_) {}
    await ListenSessionController.instance.closeAndStop();
    if (mounted) Navigator.pop(context);
  }

  void _minimize() {
    unawaited(
      ListenSessionController.instance.minimize(
        sessionId: widget.sessionId,
        convId: widget.convId,
        personaName: widget.personaName,
        title: current?.title ?? (tracks.isNotEmpty ? tracks.first['title']?.toString() ?? '' : ''),
        artist: current?.artist ?? '',
        playing: playing,
        artUri: current?.artUri,
      ),
    );
    // 确保系统媒体会话仍挂着（可通知栏控制）
    unawaited(ensureListenHandler());
    Navigator.pop(context);
  }

  @override
  Widget build(BuildContext context) {
    final name = widget.personaName ?? 'TA';

    return PopScope(
      canPop: false,
      onPopInvokedWithResult: (didPop, _) {
        if (didPop) return;
        _minimize();
      },
      child: AnnotatedRegion<SystemUiOverlayStyle>(
      value: const SystemUiOverlayStyle(
        statusBarColor: _top,
        statusBarIconBrightness: Brightness.light,
        statusBarBrightness: Brightness.dark,
        systemNavigationBarColor: _bg,
        systemNavigationBarIconBrightness: Brightness.light,
      ),
      child: Scaffold(
        backgroundColor: _bg,
        body: Stack(
          fit: StackFit.expand,
          children: [
            DecoratedBox(
              decoration: BoxDecoration(
                gradient: LinearGradient(
                  begin: Alignment.topCenter,
                  end: Alignment.bottomCenter,
                  colors: [
                    _top.withValues(alpha: 0.95),
                    _bg,
                    const Color(0xFF121018),
                  ],
                ),
              ),
            ),
            SafeArea(
              child: loading
                  ? const Center(child: CircularProgressIndicator(color: Colors.white70))
                  : error != null
                      ? Center(
                          child: Padding(
                            padding: const EdgeInsets.all(24),
                            child: Text(error!, style: const TextStyle(color: Colors.white70), textAlign: TextAlign.center),
                          ),
                        )
                      : Column(
                          children: [
                            Padding(
                              padding: const EdgeInsets.fromLTRB(8, 4, 8, 0),
                              child: Row(
                                children: [
                                  IconButton(
                                    tooltip: '收起',
                                    onPressed: _minimize,
                                    icon: const Icon(Icons.keyboard_arrow_down, color: Colors.white, size: 32),
                                  ),
                                  Expanded(
                                    child: Text(
                                      '和 $name 一起听',
                                      textAlign: TextAlign.center,
                                      style: const TextStyle(color: Colors.white, fontSize: 16, fontWeight: FontWeight.w600),
                                    ),
                                  ),
                                  TextButton(
                                    onPressed: _close,
                                    child: const Text('关闭', style: TextStyle(color: Colors.white70)),
                                  ),
                                ],
                              ),
                            ),
                            const SizedBox(height: 28),
                            ClipRRect(
                              borderRadius: BorderRadius.circular(20),
                              child: BackdropFilter(
                                filter: ImageFilter.blur(sigmaX: 12, sigmaY: 12),
                                child: Container(
                                  width: 220,
                                  height: 220,
                                  alignment: Alignment.center,
                                  decoration: BoxDecoration(
                                    color: Colors.white.withValues(alpha: 0.08),
                                    borderRadius: BorderRadius.circular(20),
                                    border: Border.all(color: Colors.white24),
                                  ),
                                  child: const Icon(Icons.music_note_rounded, size: 88, color: Colors.white70),
                                ),
                              ),
                            ),
                            const SizedBox(height: 28),
                            Padding(
                              padding: const EdgeInsets.symmetric(horizontal: 28),
                              child: Text(
                                current?.title ?? (tracks.isNotEmpty ? tracks.first['title']?.toString() ?? '…' : '暂无曲目'),
                                maxLines: 2,
                                textAlign: TextAlign.center,
                                overflow: TextOverflow.ellipsis,
                                style: const TextStyle(color: Colors.white, fontSize: 22, fontWeight: FontWeight.w700, height: 1.25),
                              ),
                            ),
                            const SizedBox(height: 8),
                            Text(
                              current?.artist ?? '',
                              style: TextStyle(color: Colors.white.withValues(alpha: 0.5), fontSize: 14),
                            ),
                            const SizedBox(height: 28),
                            Row(
                              mainAxisAlignment: MainAxisAlignment.center,
                              children: [
                                IconButton(
                                  iconSize: 36,
                                  color: Colors.white,
                                  onPressed: () => handler?.skipToPrevious(),
                                  icon: const Icon(Icons.skip_previous_rounded),
                                ),
                                const SizedBox(width: 12),
                                Material(
                                  color: Colors.white,
                                  shape: const CircleBorder(),
                                  child: InkWell(
                                    customBorder: const CircleBorder(),
                                    onTap: () async {
                                      if (playing) {
                                        await handler?.pause();
                                      } else {
                                        await handler?.play();
                                      }
                                    },
                                    child: SizedBox(
                                      width: 68,
                                      height: 68,
                                      child: Icon(
                                        playing ? Icons.pause_rounded : Icons.play_arrow_rounded,
                                        size: 40,
                                        color: const Color(0xFF111111),
                                      ),
                                    ),
                                  ),
                                ),
                                const SizedBox(width: 12),
                                IconButton(
                                  iconSize: 36,
                                  color: Colors.white,
                                  onPressed: () => handler?.skipToNext(),
                                  icon: const Icon(Icons.skip_next_rounded),
                                ),
                              ],
                            ),
                            const SizedBox(height: 12),
                            Expanded(
                              child: ListView.builder(
                                padding: const EdgeInsets.fromLTRB(16, 0, 16, 8),
                                itemCount: tracks.length,
                                itemBuilder: (context, i) {
                                  final t = tracks[i];
                                  final id = t['id']?.toString() ?? '';
                                  final on = current?.id == id;
                                  return ListTile(
                                    dense: true,
                                    onTap: () => handler?.skipToQueueItem(i),
                                    leading: Text(
                                      '${i + 1}'.padLeft(2, '0'),
                                      style: TextStyle(
                                        color: on ? AppColors.green : Colors.white38,
                                        fontSize: 13,
                                        fontFeatures: const [FontFeature.tabularFigures()],
                                      ),
                                    ),
                                    title: Text(
                                      t['title']?.toString() ?? '',
                                      maxLines: 1,
                                      overflow: TextOverflow.ellipsis,
                                      style: TextStyle(
                                        color: on ? Colors.white : Colors.white70,
                                        fontWeight: on ? FontWeight.w600 : FontWeight.w400,
                                      ),
                                    ),
                                    subtitle: Text(
                                      t['artist']?.toString() ?? '',
                                      maxLines: 1,
                                      overflow: TextOverflow.ellipsis,
                                      style: const TextStyle(color: Colors.white38, fontSize: 12),
                                    ),
                                    trailing: on
                                        ? Icon(playing ? Icons.equalizer : Icons.music_note, color: AppColors.green, size: 18)
                                        : null,
                                  );
                                },
                              ),
                            ),
                            // 底部固定聊天区：占位不叠歌单
                            _ListenChatDock(
                              bubbles: _chatBubbles,
                              composerOpen: _composerOpen,
                              composerCtrl: _composerCtrl,
                              composerFocus: _composerFocus,
                              onOpen: _openComposer,
                              onClose: _closeComposer,
                              onSend: _sendComposer,
                            ),
                          ],
                        ),
            ),
          ],
        ),
      ),
    ),
    );
  }
}

/// 歌单下方独立底栏：气泡 + 输入，不遮挡列表。
class _ListenChatDock extends StatelessWidget {
  const _ListenChatDock({
    required this.bubbles,
    required this.composerOpen,
    required this.composerCtrl,
    required this.composerFocus,
    required this.onOpen,
    required this.onClose,
    required this.onSend,
  });

  final List<_ListenChatBubble> bubbles;
  final bool composerOpen;
  final TextEditingController composerCtrl;
  final FocusNode composerFocus;
  final VoidCallback onOpen;
  final VoidCallback onClose;
  final VoidCallback onSend;

  @override
  Widget build(BuildContext context) {
    return Material(
      color: const Color(0xF016121C),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Container(height: 1, color: Colors.white.withValues(alpha: 0.08)),
          if (bubbles.isNotEmpty)
            Padding(
              padding: const EdgeInsets.fromLTRB(12, 10, 12, 4),
              child: ConstrainedBox(
                constraints: const BoxConstraints(maxHeight: 120),
                child: SingleChildScrollView(
                  reverse: true,
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      for (final b in bubbles)
                        Padding(
                          padding: const EdgeInsets.only(bottom: 6),
                          child: _ListenBubbleAnim(
                            key: ValueKey('listen_b_${b.msgId}'),
                            fading: b.fading,
                            child: Align(
                              alignment: b.mine ? Alignment.centerRight : Alignment.centerLeft,
                              child: ConstrainedBox(
                                constraints: const BoxConstraints(maxWidth: 280),
                                child: Material(
                                  color: b.mine
                                      ? const Color(0xFF07C160)
                                      : const Color(0xFFF2F2F7),
                                  borderRadius: BorderRadius.only(
                                    topLeft: Radius.circular(b.mine ? 14 : 4),
                                    topRight: Radius.circular(b.mine ? 4 : 14),
                                    bottomLeft: const Radius.circular(14),
                                    bottomRight: const Radius.circular(14),
                                  ),
                                  child: Padding(
                                    padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                                    child: Text(
                                      b.text,
                                      maxLines: 3,
                                      overflow: TextOverflow.ellipsis,
                                      style: TextStyle(
                                        color: b.mine ? Colors.white : const Color(0xFF222222),
                                        fontSize: 13,
                                        height: 1.3,
                                      ),
                                    ),
                                  ),
                                ),
                              ),
                            ),
                          ),
                        ),
                    ],
                  ),
                ),
              ),
            ),
          if (composerOpen)
            Padding(
              padding: const EdgeInsets.fromLTRB(10, 6, 4, 8),
              child: Row(
                children: [
                  Expanded(
                    child: TextField(
                      controller: composerCtrl,
                      focusNode: composerFocus,
                      style: const TextStyle(color: Colors.white, fontSize: 15),
                      cursorColor: AppColors.green,
                      textInputAction: TextInputAction.send,
                      onSubmitted: (_) => onSend(),
                      decoration: InputDecoration(
                        hintText: '说点什么…',
                        hintStyle: TextStyle(color: Colors.white.withValues(alpha: 0.35)),
                        filled: true,
                        fillColor: const Color(0xFF2A2433),
                        contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
                        border: OutlineInputBorder(
                          borderRadius: BorderRadius.circular(20),
                          borderSide: BorderSide.none,
                        ),
                      ),
                    ),
                  ),
                  IconButton(
                    onPressed: onSend,
                    icon: const Icon(Icons.send_rounded, color: Color(0xFF07C160)),
                  ),
                  IconButton(
                    onPressed: onClose,
                    icon: Icon(Icons.keyboard_hide_rounded, color: Colors.white.withValues(alpha: 0.45)),
                  ),
                ],
              ),
            )
          else
            InkWell(
              onTap: onOpen,
              child: Padding(
                padding: const EdgeInsets.fromLTRB(14, 10, 14, 12),
                child: Row(
                  children: [
                    Icon(Icons.chat_bubble_outline_rounded, size: 18, color: Colors.white.withValues(alpha: 0.55)),
                    const SizedBox(width: 8),
                    Expanded(
                      child: Text(
                        bubbles.isEmpty ? '边听边聊，点这里发消息' : '继续聊天…',
                        style: TextStyle(color: Colors.white.withValues(alpha: 0.55), fontSize: 13),
                      ),
                    ),
                    Icon(Icons.keyboard_arrow_up_rounded, size: 18, color: Colors.white.withValues(alpha: 0.35)),
                  ],
                ),
              ),
            ),
        ],
      ),
    );
  }
}

class _ListenChatBubble {
  _ListenChatBubble({
    required this.msgId,
    required this.text,
    required this.at,
    this.mine = false,
  });
  final String msgId;
  String text;
  DateTime at;
  final bool mine;
  bool fading = false;
}

/// 左侧滑入；淡出时透明度过渡到 0（不重播入场）。
class _ListenBubbleAnim extends StatefulWidget {
  const _ListenBubbleAnim({super.key, required this.fading, required this.child});
  final bool fading;
  final Widget child;

  @override
  State<_ListenBubbleAnim> createState() => _ListenBubbleAnimState();
}

class _ListenBubbleAnimState extends State<_ListenBubbleAnim> with SingleTickerProviderStateMixin {
  late final AnimationController _in = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 280),
  )..forward();

  @override
  void dispose() {
    _in.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: _in,
      builder: (context, child) {
        final t = Curves.easeOut.transform(_in.value);
        return AnimatedOpacity(
          opacity: widget.fading ? 0 : 1,
          duration: const Duration(milliseconds: 420),
          curve: Curves.easeOut,
          child: Transform.translate(
            offset: Offset(-18 * (1 - t), 0),
            child: child,
          ),
        );
      },
      child: widget.child,
    );
  }
}

class _WechatDialogAction {
  const _WechatDialogAction({
    required this.label,
    required this.value,
    this.primary = false,
    this.muted = false,
  });
  final String label;
  final String value;
  final bool primary;
  final bool muted;
}

/// 仿微信 Alert：白底圆角、标题/正文居中、底栏全宽竖排 + 分隔线。
class _WechatConfirmDialog extends StatelessWidget {
  const _WechatConfirmDialog({
    required this.title,
    required this.message,
    required this.actions,
  });

  final String title;
  final String message;
  final List<_WechatDialogAction> actions;

  static const _line = Color(0x1A000000);

  @override
  Widget build(BuildContext context) {
    return Dialog(
      elevation: 0,
      backgroundColor: Colors.transparent,
      insetPadding: const EdgeInsets.symmetric(horizontal: 52),
      child: Material(
        color: const Color(0xFFF7F7F7),
        borderRadius: BorderRadius.circular(14),
        clipBehavior: Clip.antiAlias,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(20, 28, 20, 20),
              child: Column(
                children: [
                  Text(
                    title,
                    textAlign: TextAlign.center,
                    style: const TextStyle(
                      fontSize: 17,
                      fontWeight: FontWeight.w600,
                      color: Color(0xFF191919),
                      height: 1.3,
                    ),
                  ),
                  const SizedBox(height: 10),
                  Text(
                    message,
                    textAlign: TextAlign.center,
                    style: const TextStyle(
                      fontSize: 15,
                      fontWeight: FontWeight.w400,
                      color: Color(0xFF888888),
                      height: 1.45,
                    ),
                  ),
                ],
              ),
            ),
            for (final a in actions) ...[
              const Divider(height: 1, thickness: 0.5, color: _line),
              SizedBox(
                width: double.infinity,
                height: 50,
                child: TextButton(
                  onPressed: () => Navigator.pop(context, a.value),
                  style: TextButton.styleFrom(
                    foregroundColor: a.primary
                        ? AppColors.green
                        : a.muted
                            ? const Color(0xFF888888)
                            : const Color(0xFF191919),
                    backgroundColor: Colors.white,
                    shape: const RoundedRectangleBorder(),
                    textStyle: TextStyle(
                      fontSize: 17,
                      fontWeight: a.primary ? FontWeight.w600 : FontWeight.w400,
                    ),
                  ),
                  child: Text(a.label),
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

Future<void> _openListenPage(
  BuildContext context,
  AppStore store, {
  required String sessionId,
  required String convId,
  bool resumeOnly = false,
}) async {
  final conv = store.conv(convId);
  final name = conv?.personaId != null ? store.persona(conv!.personaId!)?.display : null;
  final ctl = ListenSessionController.instance;
  // 已有同场浮窗：先 expand 再 push，避免叠两层
  if (ctl.mini?.sessionId == sessionId) {
    ctl.expand();
  }
  await Navigator.push(
    context,
    MaterialPageRoute(
      builder: (_) => ListenTogetherPage(
        store: store,
        sessionId: sessionId,
        convId: convId,
        personaName: name,
        resumeOnly: resumeOnly,
      ),
    ),
  );
}

Future<void> _openSongPicker(BuildContext context, AppStore store, {required String convId}) async {
  await Navigator.push(
    context,
    MaterialPageRoute(
      builder: (_) => ListenSongPickerPage(store: store, convId: convId),
    ),
  );
}

/// 从会话「更多」主动发起一起听：有进行中则进入/结束再建；新建走曲库自选。
Future<void> startListenTogether(BuildContext context, AppStore store, {required String convId, String mood = '随心情'}) async {
  final api = store.api;
  if (api == null) {
    ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('未连接服务器')));
    return;
  }

  showDialog(
    context: context,
    barrierDismissible: false,
    builder: (_) => const Center(child: CircularProgressIndicator()),
  );

  Map<String, dynamic>? openSession;
  try {
    final list = await api.listListenSessions();
    if (list.isNotEmpty && list.first is Map) {
      openSession = Map<String, dynamic>.from(list.first as Map);
    }
  } catch (_) {
    openSession = null;
  }

  final ctl = ListenSessionController.instance;
  if (!context.mounted) return;
  Navigator.pop(context); // loading

  final hasOpen = openSession != null || ctl.boundSessionId != null;
  if (hasOpen) {
    final sid = (openSession?['id']?.toString() ?? '').trim().isNotEmpty
        ? openSession!['id'].toString()
        : (ctl.boundSessionId ?? '');
    final moodLabel = (openSession?['mood']?.toString() ?? '').trim();
    final titleHint = ctl.mini?.title.trim().isNotEmpty == true
        ? ctl.mini!.title
        : (moodLabel.isNotEmpty ? moodLabel : '一起听');
    final action = await showDialog<String>(
      context: context,
      barrierColor: const Color(0x66000000),
      builder: (ctx) => _WechatConfirmDialog(
        title: '已有一起听进行中',
        message: '当前：「$titleHint」\n同时只能有一场。可进入继续听，或结束后再选歌创建。',
        actions: const [
          _WechatDialogAction(label: '进入', value: 'enter', primary: true),
          _WechatDialogAction(label: '结束并选歌', value: 'end'),
          _WechatDialogAction(label: '取消', value: 'cancel', muted: true),
        ],
      ),
    );
    if (!context.mounted) return;
    if (action == 'enter') {
      if (sid.isEmpty) {
        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('找不到进行中的会话')));
        return;
      }
      final sameLocal = ctl.boundSessionId == sid;
      await _openListenPage(context, store, sessionId: sid, convId: (openSession?['conv_id']?.toString() ?? convId), resumeOnly: sameLocal);
      return;
    }
    if (action == 'end') {
      try {
        if (sid.isNotEmpty) {
          await api.patchListenSession(sid, status: 'ended');
        }
        // 账号上可能还有其它开放场，一并关掉再选歌
        try {
          final rest = await api.listListenSessions();
          for (final e in rest) {
            if (e is! Map) continue;
            final id = e['id']?.toString() ?? '';
            if (id.isEmpty) continue;
            await api.patchListenSession(id, status: 'ended');
          }
        } catch (_) {}
        await ctl.closeAndStop();
      } catch (e) {
        if (context.mounted) {
          ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('结束失败：$e')));
        }
        return;
      }
      if (!context.mounted) return;
      await _openSongPicker(context, store, convId: convId);
      return;
    }
    return; // cancel
  }

  await _openSongPicker(context, store, convId: convId);
}
