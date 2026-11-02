import 'dart:async';
import 'dart:convert';
import 'dart:math' as math;
import 'dart:typed_data';

import 'package:audioplayers/audioplayers.dart';
import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:permission_handler/permission_handler.dart';

import '../api/api_client.dart';
import '../format.dart';
import '../media/app_audio.dart';
import '../media/auth_image.dart';
import '../media/listen_session.dart';
import '../media/media_pick.dart';
import '../media/voice_record.dart';
import '../models.dart';
import '../place_approx.dart';
import '../store.dart';
import '../theme.dart';
import '../voice_wave.dart';
import '../widgets.dart';
import 'chat_info.dart';
import 'fullscreen_images.dart';
import 'listen_together.dart';
import 'location_picker.dart';
import 'persona.dart';
import 'persona_draw.dart';
import 'realtime_voice.dart';
import 'video_player_page.dart';
import 'watch_ai.dart';

void _toastIfNeeded(BuildContext context, Object e, {String prefix = ''}) {
  if (!shouldToastApiError(e)) return;
  final msg = prefix.isEmpty ? '$e' : '$prefix$e';
  ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg)));
}

/// 通话前麦克风权限门闩：拒绝则弹窗引导设置，不进入通话页。
Future<bool> _ensureMicForCall(BuildContext context) async {
  var st = await Permission.microphone.status;
  if (st.isGranted) return true;
  st = await Permission.microphone.request();
  if (st.isGranted) return true;
  if (!context.mounted) return false;
  final go = await showDialog<bool>(
    context: context,
    builder: (ctx) => AlertDialog(
      title: const Text('需要麦克风权限'),
      content: const Text('语音/视频通话需要使用麦克风。请在系统设置中开启权限后再试。'),
      actions: [
        TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('取消')),
        TextButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('去设置')),
      ],
    ),
  );
  if (go == true) {
    await openAppSettings();
  }
  return false;
}

/// 发起/接听实时语音：先要麦克风授权，再校验固定音色，最后进通话页。
Future<void> launchRealtimeVoiceCall(
  BuildContext context,
  AppStore store,
  Conv conv, {
  bool Function()? isLaunching,
  void Function(bool)? setLaunching,
}) async {
  if (isLaunching?.call() == true) return;
  setLaunching?.call(true);
  try {
    final p = conv.personaId != null ? store.persona(conv.personaId!) : null;
    final api = store.api;
    if (api == null) {
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('未登录，无法语音通话')));
      }
      return;
    }
    if (p == null) {
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('当前会话没有人设，无法语音通话')));
      }
      return;
    }
    final micOk = await _ensureMicForCall(context);
    if (!micOk || !context.mounted) return;
    var hasFixed = false;
    try {
      final ready = await api.voiceRealtimeReady(convId: conv.id);
      hasFixed = ready['has_fixed_voice'] == true;
      if (!hasFixed) {
        final assets = await api.listPersonaAssets(p.id, kind: 'voice');
        hasFixed = assets.any((a) => a is Map && a['fixed'] == true);
      }
    } catch (e) {
      if (context.mounted) _toastIfNeeded(context, e);
      return;
    }
    if (!hasFixed) {
      if (!context.mounted) return;
      final go = await showDialog<bool>(
        context: context,
        builder: (ctx) => AlertDialog(
          title: const Text('先固定音色'),
          content: Text('语音通话需要先给「${p.display}」固定一条音色，固定后才能开始通话。'),
          actions: [
            TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('取消')),
            TextButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('去固定')),
          ],
        ),
      );
      if (go != true || !context.mounted) return;
      await Navigator.of(context).push(
        MaterialPageRoute(builder: (_) => PersonaDrawPage(store: store, persona: p)),
      );
      if (!context.mounted) return;
      try {
        final ready2 = await api.voiceRealtimeReady(convId: conv.id);
        hasFixed = ready2['has_fixed_voice'] == true;
        if (!hasFixed) {
          final assets = await api.listPersonaAssets(p.id, kind: 'voice');
          hasFixed = assets.any((a) => a is Map && a['fixed'] == true);
        }
      } catch (e) {
        if (context.mounted) _toastIfNeeded(context, e);
        return;
      }
      if (!hasFixed) {
        if (context.mounted) {
          ScaffoldMessenger.of(context).showSnackBar(
            const SnackBar(content: Text('还没有固定音色，通话未开始')),
          );
        }
        return;
      }
    }
    if (!context.mounted) return;
    await Navigator.of(context).push(
      MaterialPageRoute(
        builder: (_) => RealtimeVoicePage(
          store: store,
          convId: conv.id,
          peerName: p.display,
          peerColor: p.color,
          peerAvatarFileId: p.avatarFileId.isNotEmpty ? p.avatarFileId : null,
        ),
      ),
    );
  } finally {
    setLaunching?.call(false);
  }
}

class ChatPage extends StatefulWidget {
  const ChatPage({super.key, required this.store, required this.conv});
  final AppStore store;
  final Conv conv;
  @override
  State<ChatPage> createState() => _ChatPageState();
}

class _ChatPageState extends State<ChatPage> {
  /// 防连点叠多个通话页；真正互斥由服务端「清空旧路、只留最新」保证。
  bool _voiceLaunching = false;
  final input = TextEditingController();
  final scroll = ScrollController();
  final focus = FocusNode();
  bool voiceMode = false;
  bool emojiOpen = false;
  bool moreOpen = false;
  bool selecting = false;
  final selected = <String>{};
  String? quoteId;
  String? reedit;
  bool atBottom = true;
  bool showNewBtn = false;
  int entryUnread = 0;

  bool recording = false;
  bool recordCancel = false;
  DateTime? recordStarted;

  /// 当前正在「播放」的语音消息 id（演示动画，无真实音频）。
  String? playingVoiceId;
  Timer? _voicePlayTimer;
  final AudioPlayer _voicePlayer = AudioPlayer();

  /// 看 AI 小窗会话（非空则显示右下角小窗）。
  WatchAiController? _watchAi;

  /// 微信式：相册多选后暂存在输入栏上方，点发送再上传。
  final staged = <StagedMedia>[];

  /// 输入框上方表情联想（微信式）。
  List<Sticker> _suggestStickers = const [];
  Timer? _suggestDebounce;

  AppStore get store => widget.store;
  Conv get conv => widget.conv;
  bool get panelOpen => emojiOpen || moreOpen;
  bool get inputRaised => panelOpen || focus.hasFocus;

  void _onSuggestQuery(String raw) {
    _suggestDebounce?.cancel();
    final q = raw.trim();
    if (q.isEmpty || q.length < 1) {
      if (_suggestStickers.isNotEmpty) setState(() => _suggestStickers = const []);
      return;
    }
    // 纯表情/过短英文不查
    if (q.length == 1 && q.codeUnitAt(0) < 128) {
      if (_suggestStickers.isNotEmpty) setState(() => _suggestStickers = const []);
      return;
    }
    _suggestDebounce = Timer(const Duration(milliseconds: 280), () async {
      final hits = await store.suggestStickers(q);
      if (!mounted) return;
      if (input.text.trim() != q) return;
      setState(() => _suggestStickers = hits);
    });
  }

  Future<void> _closeWatchAi() async {
    final c = _watchAi;
    _watchAi = null;
    if (mounted) setState(() {});
    if (c != null) {
      // 只结束监控，不 stop 设备（唤醒/关闭归 AI）
      await c.detach();
      c.dispose();
    }
  }

  Future<void> _expandWatchAi() async {
    final c = _watchAi;
    if (c == null || !mounted) return;
    c.fullscreen = true;
    if (mounted) setState(() {}); // 进全屏时立刻藏小窗，避免叠层
    final result = await Navigator.of(context).push<Object?>(
      MaterialPageRoute(builder: (_) => WatchAiPage(controller: c)),
    );
    if (!mounted) return;
    // push 的 Future 可能先于 WatchAiPage.dispose 完成，须先清 fullscreen 再重建，否则小窗永远不出来
    c.fullscreen = false;
    if (result == 'closed') {
      await _closeWatchAi();
    } else {
      setState(() {}); // 回到小窗
    }
  }

  /// 打开监控小窗：不自动唤醒。未开机也可开窗，用「开机/关机」按钮手动控制。
  Future<void> _openWatchAi(BuildContext context, AppStore store) async {
    final api = store.api;
    if (api == null) {
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('未连接服务器')));
      return;
    }
    if (_watchAi != null) {
      await _expandWatchAi();
      return;
    }
    try {
      final devices = await api.listVirtualDevices();
      Map<String, dynamic>? deskRun;
      Map<String, dynamic>? phoneRun;
      Map<String, dynamic>? deskAny;
      Map<String, dynamic>? phoneAny;
      for (final raw in devices) {
        final d = Map<String, dynamic>.from(raw as Map);
        final status = (d['status'] as String?) ?? '';
        if (status == 'destroyed') continue;
        final kind = (d['kind'] as String?) ?? 'desktop';
        if (kind == 'android') {
          phoneAny ??= d;
          if (status == 'running') phoneRun ??= d;
        } else {
          deskAny ??= d;
          if (status == 'running') deskRun ??= d;
        }
      }
      if (!context.mounted) return;
      final desk = deskRun ?? deskAny;
      final phone = phoneRun ?? phoneAny;
      // 有正在跑的优先；都没跑则默认电脑页（可点开机）
      final useKind = deskRun != null
          ? 'desktop'
          : (phoneRun != null ? 'android' : 'desktop');
      final on = useKind == 'android' ? phoneRun != null : deskRun != null;
      final ctrl = WatchAiController(
        api: api,
        deviceId: desk?['id'] as String? ?? '',
        initialViewUrl: desk?['view_url'] as String? ?? '',
        phoneDeviceId: phone?['id'] as String?,
        phoneViewUrl: phone?['view_url'] as String?,
        deviceOn: on,
      );
      setState(() => _watchAi = ctrl);
      unawaited(ctrl.attach(kind: useKind));
    } catch (e) {
      if (!context.mounted) return;
      _toastIfNeeded(context, e);
    }
  }

  void _dismissComposer() {
    if (!inputRaised && !voiceMode) return;
    focus.unfocus();
    if (emojiOpen || moreOpen) {
      setState(() {
        emojiOpen = false;
        moreOpen = false;
      });
    }
  }

  /// 首帧滚到底完成前先隐藏列表，避免「先从顶闪一下再跳到底」的跳动。
  bool _listReady = false;
  /// 用于检测新消息 / 流式变长后自动贴底。
  String _stickSig = '';

  @override
  void initState() {
    super.initState();
    store.setActiveConv(conv.id);
    entryUnread = conv.unread;
    // 进入会话即清未读（本地 + 服务端）；断联时也不再被 sync 盖回旧未读。
    WidgetsBinding.instance.addPostFrameCallback((_) {
      unawaited(store.markConversationRead(conv.id));
    });
    input.text = conv.draft;
    input.addListener(() {
      store.scheduleDraftSave(conv.id, input.text);
      _onSuggestQuery(input.text);
      setState(() {});
    });
    scroll.addListener(() {
      if (!scroll.hasClients) return;
      final p = scroll.position;
      final near = p.maxScrollExtent - p.pixels <= 80;
      if (near != atBottom) setState(() => atBottom = near);
      if (near && showNewBtn) setState(() => showNewBtn = false);
    });
    // 等布局完成再贴底，并再补一帧（图片占位/时间条高度变化）；然后才显示列表。
    WidgetsBinding.instance.addPostFrameCallback((_) => _prepareInitialScroll());
  }

  Future<void> _prepareInitialScroll() async {
    _jumpBottom(animated: false);
    await Future<void>.delayed(Duration.zero);
    if (!mounted) return;
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted) return;
      _jumpBottom(animated: false);
      if (!_listReady) setState(() => _listReady = true);
    });
  }

  void _jumpBottom({bool animated = true}) {
    if (!scroll.hasClients) return;
    final target = scroll.position.maxScrollExtent;
    if (!animated || target <= 0) {
      scroll.jumpTo(target);
      return;
    }
    scroll.animateTo(target, duration: const Duration(milliseconds: 200), curve: Curves.easeOut);
  }

  Future<void> _togglePlayVoice(Msg m) async {
    final meta = m.meta ?? const <String, dynamic>{};
    final isSing = meta['kind_extra']?.toString() == 'sing' || meta['card']?.toString() == 'sing_track';
    if (isSing) {
      await _playSingWithListenPip(m);
      return;
    }

    _voicePlayTimer?.cancel();
    await _voicePlayer.stop();
    if (playingVoiceId == m.id) {
      setState(() => playingVoiceId = null);
      return;
    }
    if (!m.mine) m.heard = true;
    setState(() => playingVoiceId = m.id);
    store.ping();

    try {
      await AppAudio.ensureConfigured();
      final local = meta['local_bytes'];
      Uint8List? bytes;
      var ext = 'm4a';
      if (local is List) {
        bytes = Uint8List.fromList(List<int>.from(local));
      } else {
        final files = meta['files'];
        final fid = (files is List && files.isNotEmpty) ? files.first.toString() : null;
        if (fid != null && store.api != null) {
          bytes = await AuthImageCache.load(store.api!, fid);
        }
      }
      if (bytes != null && bytes.isNotEmpty) {
        // wav / mp3（唱歌）/ m4a（用户录音）按魔数识别，切勿把 mp3 当 m4a 播（会无声）
        ext = _detectAudioExt(bytes);
        final mime = switch (ext) {
          'wav' => 'audio/wav',
          'mp3' => 'audio/mpeg',
          _ => 'audio/mp4',
        };
        await AppAudio.playBytes(_voicePlayer, bytes, ext: ext, mimeType: mime);
      }
    } catch (_) {
      // 回退：按时长假装播完
    }

    // 唱歌可很长；跟真实时长走，上限 15 分钟
    final secs = m.seconds.clamp(1, 15 * 60);
    _voicePlayTimer = Timer(Duration(seconds: secs), () {
      if (!mounted) return;
      if (playingVoiceId == m.id) setState(() => playingVoiceId = null);
    });
  }

  /// 翻唱：复用一起听悬浮窗（播放/暂停控件），不再用气泡内假进度。
  Future<void> _playSingWithListenPip(Msg m) async {
    final api = store.api;
    if (api == null) return;
    final meta = m.meta ?? const <String, dynamic>{};
    final files = meta['files'];
    final fid = (files is List && files.isNotEmpty) ? files.first.toString() : '';
    if (fid.isEmpty) return;
    if (!m.mine) m.heard = true;
    setState(() => playingVoiceId = m.id);
    store.ping();
    final title = (meta['title']?.toString() ?? meta['caption']?.toString() ?? m.text).trim();
    final artist = (meta['artist']?.toString() ?? '').trim();
    final persona = store.persona(conv.personaId ?? m.senderId);
    final secs = m.seconds > 0
        ? m.seconds
        : (meta['seconds'] is num ? (meta['seconds'] as num).toInt() : 0);
    try {
      await ListenSessionController.instance.playCoverInPip(
        sessionId: 'sing_${m.id}',
        audioUrl: api.mediaFileAudioUrl(fid),
        headers: api.authHeaders(),
        convId: conv.id,
        personaName: persona?.display ?? store.nameOfSender(m.senderId),
        title: title.isEmpty ? '翻唱' : title,
        artist: artist,
        duration: secs > 0 ? Duration(seconds: secs) : null,
      );
    } catch (e) {
      if (!mounted) return;
      setState(() => playingVoiceId = null);
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('播放失败：$e')));
    }
  }

  String _detectAudioExt(Uint8List bytes) {
    if (bytes.length >= 12 &&
        bytes[0] == 0x52 &&
        bytes[1] == 0x49 &&
        bytes[2] == 0x46 &&
        bytes[3] == 0x46) {
      return 'wav';
    }
    // ID3 tag or MPEG frame sync
    if (bytes.length >= 3 && bytes[0] == 0x49 && bytes[1] == 0x44 && bytes[2] == 0x33) {
      return 'mp3';
    }
    if (bytes.length >= 2 && bytes[0] == 0xFF && (bytes[1] & 0xE0) == 0xE0) {
      return 'mp3';
    }
    // ftyp box → m4a/mp4
    if (bytes.length >= 8 &&
        bytes[4] == 0x66 &&
        bytes[5] == 0x74 &&
        bytes[6] == 0x79 &&
        bytes[7] == 0x70) {
      return 'm4a';
    }
    return 'm4a';
  }

  @override
  void dispose() {
    // 离开聊天页前再标一次已读（在聊期间对方连发也会累计到服务端）
    unawaited(store.markConversationRead(conv.id));
    if (store.activeConvId == conv.id) {
      store.setActiveConv(null);
    }
    _voicePlayTimer?.cancel();
    _voicePlayer.dispose();
    final watch = _watchAi;
    _watchAi = null;
    if (watch != null) {
      unawaited(watch.detach().whenComplete(watch.dispose));
    }
    final draft = input.text;
    final s = store;
    final cid = conv.id;
    WidgetsBinding.instance.addPostFrameCallback((_) {
      s.flushDraft(cid, draft);
    });
    _suggestDebounce?.cancel();
    input.dispose();
    scroll.dispose();
    focus.dispose();
    super.dispose();
  }

  List<Msg> get list => store.msgs(conv.id);

  void send() {
    final text = input.text.trim();
    if (text.isEmpty && staged.isEmpty) return;
    HapticFeedback.lightImpact();
    if (staged.isNotEmpty) {
      final items = [
        for (final s in staged)
          (
            bytes: List<int>.from(s.bytes ?? Uint8List(0)),
            name: s.name,
            kind: s.kind,
            contentType: s.contentType,
          ),
      ].where((e) => e.bytes.isNotEmpty).toList();
      final caption = text;
      input.clear();
      setState(() {
        staged.clear();
        _suggestStickers = const [];
      });
      unawaited(store.sendMediaMessage(conv, items: items, caption: caption));
    } else {
      input.clear();
      store.sendText(conv, text, quoteId: quoteId);
    }
    quoteId = null;
    reedit = null;
    setState(() => _suggestStickers = const []);
    _stickOrBadge();
  }

  Future<void> _pickFromMore(String kind) async {
    try {
      switch (kind) {
        case 'image':
          final more = await MediaPick.pickImagesFromGallery(already: staged.length);
          if (more.isEmpty) return;
          setState(() {
            staged.addAll(more);
            moreOpen = false;
          });
        case 'camera':
          final shot = await MediaPick.takePhoto();
          if (shot == null) return;
          setState(() {
            if (staged.length < MediaPick.maxImages) staged.add(shot);
            moreOpen = false;
          });
        case 'video':
          final v = await MediaPick.pickVideo();
          if (v == null) return;
          setState(() {
            staged
              ..clear()
              ..add(v);
            moreOpen = false;
          });
        case 'file':
          final f = await MediaPick.pickFile();
          if (f == null) return;
          setState(() {
            staged
              ..clear()
              ..add(f);
            moreOpen = false;
          });
        case 'listen':
          setState(() => moreOpen = false);
          await startListenTogether(context, store, convId: conv.id);
        case 'voice_call':
          setState(() => moreOpen = false);
          await launchRealtimeVoiceCall(
            context,
            store,
            conv,
            isLaunching: () => _voiceLaunching,
            setLaunching: (v) => _voiceLaunching = v,
          );
        case 'video_call':
          setState(() => moreOpen = false);
          final api = store.api;
          if (api == null) return;
          final micOk = await _ensureMicForCall(context);
          if (!micOk || !mounted) return;
          try {
            final r = await api.createCall(convId: conv.id, media: 'video');
            if (!mounted) return;
            ScaffoldMessenger.of(context).showSnackBar(
              SnackBar(content: Text('视频通话已发起（${r['id'] ?? ''}），信令已通，媒体通道后续接 WebRTC')),
            );
          } catch (e) {
            if (mounted) _toastIfNeeded(context, e);
          }
        case 'location':
          setState(() => moreOpen = false);
          final api = store.api;
          if (api == null) return;
          try {
            final picked = await Navigator.of(context).push<Map<String, dynamic>>(
              MaterialPageRoute(builder: (_) => LocationPickerPage(store: store)),
            );
            if (picked == null || !mounted) return;
            final lat = picked['lat'];
            final lng = picked['lng'];
            if (lat is! num || lng is! num) return;
            final clientId = 'loc_${DateTime.now().microsecondsSinceEpoch}';
            await api.sendMessage(
              convId: conv.id,
              clientId: clientId,
              kind: 'location',
              text: jsonEncode({
                'name': (picked['name'] ?? '位置').toString(),
                'address': (picked['address'] ?? '').toString(),
                'lat': lat.toDouble(),
                'lng': lng.toDouble(),
              }),
            );
            await store.pullSync();
          } catch (e) {
            if (mounted) _toastIfNeeded(context, e);
          }
        default:
          if (mounted) {
            ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$kind 入口已留，实现后接')));
          }
          setState(() => moreOpen = false);
      }
    } catch (e) {
      if (mounted) {
        _toastIfNeeded(context, e);
      }
    }
    _stickOrBadge();
  }

  void _stickOrBadge() {
    if (atBottom) {
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (!mounted) return;
        _jumpBottom(animated: true);
        // 列表高度变化后再补一帧，避免贴底差一点
        WidgetsBinding.instance.addPostFrameCallback((_) {
          if (mounted && atBottom) _jumpBottom(animated: false);
        });
      });
    } else {
      setState(() => showNewBtn = true);
    }
  }

  void _forceStickBottom({Duration? afterAnim}) {
    atBottom = true;
    showNewBtn = false;
    void jump() {
      if (!mounted || !scroll.hasClients) return;
      _jumpBottom(animated: false);
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (mounted && scroll.hasClients) _jumpBottom(animated: false);
      });
    }

    WidgetsBinding.instance.addPostFrameCallback((_) => jump());
    // AnimatedSize 拉高后 maxScrollExtent 变大，动画结束再贴一次，否则最新消息仍被挡住
    if (afterAnim != null && afterAnim > Duration.zero) {
      Future<void>.delayed(afterAnim, jump);
    }
  }

  void _togglePanel({bool emoji = false, bool more = false}) {
    setState(() {
      voiceMode = false;
      if (emoji) {
        emojiOpen = !emojiOpen;
        moreOpen = false;
        if (emojiOpen) focus.unfocus();
      } else if (more) {
        moreOpen = !moreOpen;
        emojiOpen = false;
        if (moreOpen) focus.unfocus();
      } else {
        emojiOpen = false;
        moreOpen = false;
      }
    });
    // 更多/表情面板像键盘一样顶起内容：强制滚到最新
    _forceStickBottom(afterAnim: const Duration(milliseconds: 240));
  }

  void _enterVoiceMode() {
    focus.unfocus();
    setState(() {
      voiceMode = true;
      emojiOpen = false;
      moreOpen = false;
    });
  }

  void _enterKeyboardMode() {
    setState(() {
      voiceMode = false;
      emojiOpen = false;
      moreOpen = false;
    });
    focus.requestFocus();
  }

  Future<void> _startRecord() async {
    HapticFeedback.mediumImpact();
    // 先亮遮罩动画，再异步开麦，避免「按住没反馈」
    setState(() {
      recording = true;
      recordCancel = false;
      recordStarted = DateTime.now();
      emojiOpen = false;
      moreOpen = false;
    });
    try {
      await VoiceRecorder.start();
    } catch (e) {
      if (mounted) {
        setState(() {
          recording = false;
          recordCancel = false;
          recordStarted = null;
        });
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('无法录音：$e')));
      }
      return;
    }
  }

  void _updateRecord(LongPressMoveUpdateDetails d) {
    // 上滑超过 60 视为取消，和微信一致。
    final cancel = d.localOffsetFromOrigin.dy < -60;
    if (cancel != recordCancel) {
      HapticFeedback.selectionClick();
      setState(() => recordCancel = cancel);
    }
  }

  Future<void> _endRecord() async {
    if (!recording) return;
    final cancel = recordCancel;
    setState(() {
      recording = false;
      recordCancel = false;
      recordStarted = null;
    });
    final captured = await VoiceRecorder.stop(cancel: cancel);
    if (cancel) {
      HapticFeedback.heavyImpact();
      return;
    }
    HapticFeedback.lightImpact();
    if (captured == null) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('录音太短')));
      }
      return;
    }
    try {
      final bytes = await captured.file.readAsBytes();
      await store.sendVoiceMessage(conv, bytes: bytes, seconds: captured.seconds, filename: 'voice.m4a');
      try {
        captured.file.deleteSync();
      } catch (_) {}
      _stickOrBadge();
    } catch (e) {
      if (mounted) {
        _toastIfNeeded(context, e, prefix: '发送失败：');
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final unreadElse = store.unreadTotal;

    return ListenableBuilder(
      listenable: store,
      builder: (context, _) {
        final hint = store.shapeHint;
        if (hint != null && hint.isNotEmpty) {
          WidgetsBinding.instance.addPostFrameCallback((_) {
            if (!mounted || store.shapeHint == null) return;
            final text = store.shapeHint!;
            store.shapeHint = null;
            ScaffoldMessenger.of(context).showSnackBar(
              SnackBar(content: Text(text), duration: const Duration(seconds: 4)),
            );
          });
        }
        final title = store.typingTitle(conv.id, store.titleOf(conv));
        final kb = MediaQuery.viewInsetsOf(context).bottom;
        if (kb > 0) {
          WidgetsBinding.instance.addPostFrameCallback((_) {
            if (!mounted) return;
            if (emojiOpen || moreOpen) {
              setState(() {
                emojiOpen = false;
                moreOpen = false;
              });
            }
            // 键盘顶起时把最新消息顶出遮挡区
            if (atBottom) _jumpBottom(animated: false);
          });
        }
        final msgs = list.where((m) => m.text.isNotEmpty || m.kind != MsgKind.text || m.mine).toList();
        final last = msgs.isEmpty ? null : msgs.last;
        final sig = '${msgs.length}|${last?.id ?? ''}|${last?.text.length ?? 0}';
        if (sig != _stickSig) {
          final parts = _stickSig.split('|');
          final prevCount = int.tryParse(parts.isNotEmpty ? parts[0] : '') ?? 0;
          final prevId = parts.length > 1 ? parts[1] : '';
          final prevLen = int.tryParse(parts.length > 2 ? parts[2] : '') ?? 0;
          _stickSig = sig;
          final countGrew = msgs.length > prevCount;
          final streamGrew = last != null && last.id == prevId && last.text.length > prevLen;
          final newTail = last != null && last.id != prevId && prevId.isNotEmpty;
          if (countGrew || streamGrew || newTail) {
            WidgetsBinding.instance.addPostFrameCallback((_) {
              if (mounted) _stickOrBadge();
            });
          }
        }
        final firstUnreadIndex = entryUnread > 0 ? (msgs.length - entryUnread).clamp(0, msgs.length) : -1;
        // 和微信一样：消息从顶部往下排；内容超出后再贴底看最新。
        // 未 ready 前透明占位，避免首屏从顶跳到底的闪动。
        final messageList = Opacity(
          opacity: _listReady ? 1 : 0,
          child: NotificationListener<ScrollNotification>(
            onNotification: (n) {
              // 用户手指拖动列表时收起输入区
              if (n is ScrollUpdateNotification && n.dragDetails != null) {
                _dismissComposer();
              }
              return false;
            },
            child: GestureDetector(
              behavior: HitTestBehavior.translucent,
              onTap: _dismissComposer,
              child: ListView.builder(
                controller: scroll,
                keyboardDismissBehavior: ScrollViewKeyboardDismissBehavior.onDrag,
                cacheExtent: 480,
                padding: const EdgeInsets.fromLTRB(12, 8, 12, 12),
                itemCount: msgs.length,
                itemBuilder: (context, i) {
                  final m = msgs[i];
                  final prev = i > 0 ? msgs[i - 1] : null;
                  final showTime = prev == null || gapNeedsTime(prev.time, m.time);
                  final showUnreadLine = i == firstUnreadIndex && entryUnread > 0;
                  return Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      if (showTime) _TimeBar(formatChatTime(m.time)),
                      if (showUnreadLine) const _TimeBar('以下为新消息'),
                      _Bubble(
                        store: store,
                        conv: conv,
                        msg: m,
                        selecting: selecting,
                        selected: selected.contains(m.id),
                        voicePlaying: playingVoiceId == m.id,
                        onSelect: () => setState(() => selected.contains(m.id) ? selected.remove(m.id) : selected.add(m.id)),
                        onLong: (rect) => _longPress(m, rect),
                        onRetry: () => store.retry(m),
                        onPlayVoice: () => _togglePlayVoice(m),
                        onAvatar: () {
                          if (conv.personaId != null) {
                            final p = store.persona(conv.personaId!);
                            if (p != null) Navigator.push(context, MaterialPageRoute(builder: (_) => PersonaPage(store: store, persona: p)));
                          }
                        },
                      ),
                    ],
                  );
                },
              ),
            ),
          ),
        );

        final hasBgBytes = conv.bgImageBytes != null && conv.bgImageBytes!.isNotEmpty;
        final hasBgFile = conv.bgFileId.isNotEmpty && store.api != null;
        final hasBgImage = hasBgBytes || hasBgFile;
        final bgColor = conversationBgColor(conv.bgName);

        return Scaffold(
          backgroundColor: hasBgImage ? Colors.black : bgColor,
          resizeToAvoidBottomInset: true,
          appBar: AppBar(
            leadingWidth: 72,
            leading: Row(children: [
              BackButton(onPressed: () {
                _dismissComposer();
                Navigator.pop(context);
              }),
              if (unreadElse > 0) Text('$unreadElse', style: const TextStyle(fontSize: 16)),
            ]),
            // 双击标题栏 → 滚到最新消息（常见微信手势）
            title: GestureDetector(
              onDoubleTap: () {
                setState(() => showNewBtn = false);
                _jumpBottom();
              },
              onTap: _dismissComposer,
              child: Text(
                title,
                style: TextStyle(
                  fontSize: store.isTyping(conv.id) ? 15 : 17,
                  fontWeight: store.isTyping(conv.id) ? FontWeight.w400 : FontWeight.w600,
                  color: store.isTyping(conv.id) ? AppColors.sub : const Color(0xFF111111),
                ),
              ),
            ),
            actions: [
              if (selecting)
                TextButton(
                  onPressed: () {
                    store.deleteMsgs(conv, selected);
                    setState(() {
                      selecting = false;
                      selected.clear();
                    });
                  },
                  child: const Text('删除', style: TextStyle(color: AppColors.badge)),
                )
              else ...[
                IconButton(
                  tooltip: '看 AI',
                  icon: const Icon(Icons.desktop_windows_outlined),
                  onPressed: () => _openWatchAi(context, store),
                ),
                IconButton(onPressed: () => Navigator.push(context, MaterialPageRoute(builder: (_) => ChatInfoPage(store: store, conv: conv))), icon: const Icon(Icons.more_horiz)),
              ],
            ],
          ),
          body: Stack(
            fit: StackFit.expand,
            children: [
              if (hasBgBytes)
                Positioned.fill(
                  child: Image.memory(conv.bgImageBytes!, fit: BoxFit.cover),
                )
              else if (hasBgFile)
                Positioned.fill(
                  child: AuthFileImage(api: store.api, fileId: conv.bgFileId, fit: BoxFit.cover),
                )
              else
                Positioned.fill(child: ColoredBox(color: bgColor)),
              Column(
                children: [
                  Expanded(
                    child: Stack(
                      children: [
                        messageList,
                        if (entryUnread > 0)
                          Positioned(
                            right: 12,
                            top: 12,
                            child: Material(
                              color: Colors.white,
                              elevation: 2,
                              borderRadius: BorderRadius.circular(16),
                              child: InkWell(
                                onTap: () {
                                  setState(() => entryUnread = 0);
                                  if (scroll.hasClients) {
                                    final target = (firstUnreadIndex * 72.0).clamp(0, scroll.position.maxScrollExtent);
                                    scroll.animateTo(target.toDouble(), duration: const Duration(milliseconds: 300), curve: Curves.easeOut);
                                  }
                                },
                                borderRadius: BorderRadius.circular(16),
                                child: Padding(
                                  padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
                                  child: Text('$entryUnread 条新消息', style: const TextStyle(color: AppColors.name, fontSize: 13)),
                                ),
                              ),
                            ),
                          ),
                        if (showNewBtn)
                          Positioned(
                            left: 0,
                            right: 0,
                            bottom: 8,
                            child: Center(
                              child: FilledButton(
                                onPressed: () {
                                  setState(() => showNewBtn = false);
                                  _jumpBottom();
                                },
                                child: const Text('新消息'),
                              ),
                            ),
                          ),
                      ],
                    ),
                  ),
                  if (staged.isNotEmpty)
                    Material(
                      color: const Color(0xFFF0F0F0),
                      child: SizedBox(
                        height: 88,
                        child: ListView.separated(
                          scrollDirection: Axis.horizontal,
                          padding: const EdgeInsets.fromLTRB(10, 8, 10, 8),
                          itemCount: staged.length,
                          separatorBuilder: (_, _) => const SizedBox(width: 8),
                          itemBuilder: (_, i) {
                            final s = staged[i];
                            return Stack(
                              children: [
                                ClipRRect(
                                  borderRadius: BorderRadius.circular(6),
                                  child: s.kind == 'image' && s.bytes != null
                                      ? Image.memory(s.bytes!, width: 72, height: 72, fit: BoxFit.cover)
                                      : Container(
                                          width: 72,
                                          height: 72,
                                          color: const Color(0xFFDDDDDD),
                                          child: Icon(
                                            s.kind == 'video' ? Icons.videocam : Icons.insert_drive_file,
                                            color: Colors.white,
                                          ),
                                        ),
                                ),
                                Positioned(
                                  right: 0,
                                  top: 0,
                                  child: InkWell(
                                    onTap: () => setState(() => staged.removeAt(i)),
                                    child: const CircleAvatar(
                                      radius: 10,
                                      backgroundColor: Color(0x99000000),
                                      child: Icon(Icons.close, size: 12, color: Colors.white),
                                    ),
                                  ),
                                ),
                              ],
                            );
                          },
                        ),
                      ),
                    ),
                  if (quoteId != null) _QuoteBar(text: _quoteText(), onClear: () => setState(() => quoteId = null)),
                  if (reedit != null)
                    Material(
                      color: const Color(0xFFFFF8E1),
                      child: ListTile(
                        dense: true,
                        title: Text('重新编辑：$reedit', maxLines: 1, overflow: TextOverflow.ellipsis),
                        trailing: IconButton(icon: const Icon(Icons.close, size: 18), onPressed: () => setState(() => reedit = null)),
                        onTap: () {
                          input.text = reedit!;
                          input.selection = TextSelection.collapsed(offset: input.text.length);
                          setState(() => reedit = null);
                        },
                      ),
                    ),
                  if (_suggestStickers.isNotEmpty)
                    _StickerSuggestBar(
                      store: store,
                      items: _suggestStickers,
                      onPick: (s) {
                        if (s.fileId.isNotEmpty) {
                          unawaited(store.sendExistingMedia(conv, fileId: s.fileId, kind: 'image'));
                        }
                        setState(() {
                          _suggestStickers = const [];
                          emojiOpen = false;
                        });
                        _stickOrBadge();
                      },
                    ),
                  ColoredBox(
                    color: const Color(0xFFF7F7F7),
                    child: Column(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        _Composer(
                          voiceMode: voiceMode,
                          hasText: input.text.trim().isNotEmpty || staged.isNotEmpty,
                          recording: recording,
                          recordCancel: recordCancel,
                          panelOpen: panelOpen,
                          emojiOpen: emojiOpen,
                          controller: input,
                          focus: focus,
                          onToggleVoice: () {
                            if (voiceMode) {
                              _enterKeyboardMode();
                            } else {
                              _enterVoiceMode();
                            }
                          },
                          onEmoji: () {
                            if (emojiOpen) {
                              setState(() => emojiOpen = false);
                              focus.requestFocus();
                            } else {
                              _togglePanel(emoji: true);
                            }
                          },
                          onMore: () {
                            if (moreOpen) {
                              setState(() => moreOpen = false);
                            } else {
                              _togglePanel(more: true);
                            }
                          },
                          onSend: send,
                          onFocus: () => setState(() {
                            voiceMode = false;
                            emojiOpen = false;
                            moreOpen = false;
                          }),
                          onRecordStart: _startRecord,
                          onRecordMove: _updateRecord,
                          onRecordEnd: _endRecord,
                        ),
                        // 用固定高度占位（不用 AnimatedSize 从 0 长高），同一帧就压缩消息区并贴底
                        SizedBox(
                          height: emojiOpen
                              ? 248
                              : moreOpen
                                  ? _MorePanel.panelHeight
                                  : 0,
                          child: emojiOpen
                              ? _EmojiPanel(
                                  store: store,
                                  hasText: input.text.trim().isNotEmpty,
                                  onPickEmoji: (e) {
                                    final t = input.text;
                                    final sel = input.selection;
                                    final start = sel.start >= 0 ? sel.start : t.length;
                                    final end = sel.end >= 0 ? sel.end : t.length;
                                    input.text = t.replaceRange(start, end, e);
                                    input.selection = TextSelection.collapsed(offset: start + e.length);
                                    setState(() {});
                                  },
                                  onPickSticker: (s) {
                                    if (s.fileId.isNotEmpty) {
                                      unawaited(store.sendExistingMedia(conv, fileId: s.fileId, kind: 'image'));
                                    } else {
                                      store.sendKind(conv, MsgKind.image, s.label);
                                    }
                                    setState(() => emojiOpen = false);
                                    _stickOrBadge();
                                  },
                                  onDelete: () {
                                    final t = input.text;
                                    if (t.isEmpty) return;
                                    input.text = t.characters.skipLast(1).toString();
                                    input.selection = TextSelection.collapsed(offset: input.text.length);
                                    setState(() {});
                                  },
                                  onSend: send,
                                )
                              : moreOpen
                                  ? _MorePanel(onPick: (kind) => unawaited(_pickFromMore(kind)))
                                  : null,
                        ),
                        SizedBox(height: MediaQuery.paddingOf(context).bottom),
                      ],
                    ),
                  ),
                ],
              ),
              if (recording) _RecordMask(cancel: recordCancel),
              if (_watchAi != null && !_watchAi!.fullscreen)
                WatchAiPipBubble(
                  controller: _watchAi!,
                  onExpand: () => unawaited(_expandWatchAi()),
                  onClose: () => unawaited(_closeWatchAi()),
                ),
              if ((store.deviceProgress[conv.id] ?? store.deviceProgress['_'] ?? '').isNotEmpty)
                Positioned(
                  left: 12,
                  right: 12,
                  bottom: 88 + MediaQuery.paddingOf(context).bottom,
                  child: Material(
                    color: const Color(0xE6111111),
                    borderRadius: BorderRadius.circular(10),
                    child: Padding(
                      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                      child: Text(
                        'AI操作：${store.deviceProgress[conv.id] ?? store.deviceProgress['_']}',
                        maxLines: 2,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(color: Colors.white, fontSize: 13),
                      ),
                    ),
                  ),
                ),
            ],
          ),
        );
      },
    );
  }

  String _quoteText() {
    final m = list.where((e) => e.id == quoteId).firstOrNull;
    if (m == null) return '';
    return '${store.nameOfSender(m.senderId)}: ${m.text}';
  }

  void _longPress(Msg m, Rect bubbleRect) {
    if (m.kind == MsgKind.system) return;
    _dismissComposer();
    HapticFeedback.mediumImpact();

    final items = <_WxMenuItem>[
      if (m.kind == MsgKind.text)
        _WxMenuItem(Icons.copy_outlined, '复制', () {
          Clipboard.setData(ClipboardData(text: m.text));
          ScaffoldMessenger.of(context).showSnackBar(
            const SnackBar(content: Text('已复制'), duration: Duration(seconds: 1)),
          );
        }),
      if (m.kind == MsgKind.voice)
        _WxMenuItem(
          Icons.text_fields,
          (m.meta?['asr_visible'] == true) ? '隐藏文字' : '转文字',
          () {
            store.toggleVoiceAsrVisible(m);
            setState(() {});
          },
        ),
      _WxMenuItem(Icons.format_quote, '引用', () => setState(() => quoteId = m.id)),
      _WxMenuItem(Icons.checklist, '多选', () {
        setState(() {
          selecting = true;
          selected.add(m.id);
        });
      }),
      if (m.mine && DateTime.now().difference(m.time).inMinutes < 2)
        _WxMenuItem(Icons.undo, '撤回', () {
          final wasText = m.kind == MsgKind.text ? m.text : null;
          store.revoke(m);
          if (wasText != null) setState(() => reedit = wasText);
        }),
      _WxMenuItem(Icons.delete_outline, '删除', () => store.deleteMsgs(conv, {m.id}), danger: true),
    ];
    if (items.isEmpty) return;

    final overlay = Overlay.of(context);
    late OverlayEntry entry;
    entry = OverlayEntry(
      builder: (ctx) => _WxMsgMenuOverlay(
        bubble: bubbleRect,
        mine: m.mine,
        items: items,
        onDismiss: () => entry.remove(),
      ),
    );
    overlay.insert(entry);
  }
}

class _WxMenuItem {
  const _WxMenuItem(this.icon, this.label, this.onTap, {this.danger = false});
  final IconData icon;
  final String label;
  final VoidCallback onTap;
  final bool danger;
}

/// 微信式：贴着被长按的消息气泡定位（用气泡 Rect，不用手指坐标）。
class _WxMsgMenuOverlay extends StatelessWidget {
  const _WxMsgMenuOverlay({
    required this.bubble,
    required this.mine,
    required this.items,
    required this.onDismiss,
  });

  final Rect bubble;
  final bool mine;
  final List<_WxMenuItem> items;
  final VoidCallback onDismiss;

  @override
  Widget build(BuildContext context) {
    final size = MediaQuery.sizeOf(context);
    final pad = MediaQuery.paddingOf(context);
    const itemW = 52.0;
    const barH = 62.0;
    const gap = 6.0;
    final barW = (items.length * itemW).clamp(itemW, size.width - 24).toDouble();

    // 优先贴在气泡正上方；上方不够再落到气泡下方
    var top = bubble.top - barH - gap - 6;
    var below = false;
    if (top < pad.top + 8) {
      top = bubble.bottom + gap;
      below = true;
    }
    if (top + barH + 6 > size.height - pad.bottom - 8) {
      top = (bubble.top - barH - gap - 6).clamp(pad.top + 8, size.height - pad.bottom - barH - 8);
      below = false;
    }

    // 水平：相对气泡居中，再钳到屏幕内
    var left = bubble.center.dx - barW / 2;
    left = left.clamp(12.0, size.width - barW - 12);

    // 小三角对准气泡中心（相对菜单条）
    final arrowX = (bubble.center.dx - left).clamp(16.0, barW - 16);
    final arrowAlign = Alignment((arrowX / barW) * 2 - 1, 0);

    return Material(
      type: MaterialType.transparency,
      child: Stack(
        children: [
          Positioned.fill(
            child: GestureDetector(
              behavior: HitTestBehavior.opaque,
              onTap: onDismiss,
              onPanStart: (_) => onDismiss(),
            ),
          ),
          Positioned(
            left: left,
            top: top,
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                // 菜单在气泡下方时：尖角朝上贴气泡
                if (below)
                  Align(
                    alignment: arrowAlign,
                    child: CustomPaint(
                      size: const Size(12, 6),
                      painter: _WxMenuArrowPainter(up: true),
                    ),
                  ),
                Material(
                  color: const Color(0xFF4C4C4C),
                  borderRadius: BorderRadius.circular(8),
                  elevation: 8,
                  shadowColor: Colors.black54,
                  child: SizedBox(
                    width: barW,
                    height: barH,
                    child: Row(
                      children: [
                        for (final it in items)
                          Expanded(
                            child: InkWell(
                              borderRadius: BorderRadius.circular(8),
                              onTap: () {
                                onDismiss();
                                it.onTap();
                              },
                              child: Column(
                                mainAxisAlignment: MainAxisAlignment.center,
                                children: [
                                  Icon(
                                    it.icon,
                                    size: 20,
                                    color: it.danger ? const Color(0xFFFF6B6B) : Colors.white,
                                  ),
                                  const SizedBox(height: 4),
                                  Text(
                                    it.label,
                                    style: TextStyle(
                                      color: it.danger ? const Color(0xFFFF6B6B) : Colors.white,
                                      fontSize: 11,
                                    ),
                                  ),
                                ],
                              ),
                            ),
                          ),
                      ],
                    ),
                  ),
                ),
                // 菜单在气泡上方时：尖角朝下贴气泡
                if (!below)
                  Align(
                    alignment: arrowAlign,
                    child: CustomPaint(
                      size: const Size(12, 6),
                      painter: _WxMenuArrowPainter(up: false),
                    ),
                  ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _WxMenuArrowPainter extends CustomPainter {
  _WxMenuArrowPainter({required this.up});
  final bool up;

  @override
  void paint(Canvas canvas, Size size) {
    final p = Paint()..color = const Color(0xFF4C4C4C);
    final path = Path();
    if (up) {
      path.moveTo(0, size.height);
      path.lineTo(size.width / 2, 0);
      path.lineTo(size.width, size.height);
    } else {
      path.moveTo(0, 0);
      path.lineTo(size.width / 2, size.height);
      path.lineTo(size.width, 0);
    }
    path.close();
    canvas.drawPath(path, p);
  }

  @override
  bool shouldRepaint(covariant _WxMenuArrowPainter old) => old.up != up;
}

class _TimeBar extends StatelessWidget {
  const _TimeBar(this.text);
  final String text;
  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 8),
      child: Center(
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
          decoration: BoxDecoration(color: AppColors.chip, borderRadius: BorderRadius.circular(3)),
          child: Text(text, style: const TextStyle(fontSize: 12, color: Color(0xFF666666))),
        ),
      ),
    );
  }
}

class _QuoteBar extends StatelessWidget {
  const _QuoteBar({required this.text, required this.onClear});
  final String text;
  final VoidCallback onClear;
  @override
  Widget build(BuildContext context) {
    return Material(
      color: const Color(0xFFF0F0F0),
      child: ListTile(
        dense: true,
        title: Text(text, maxLines: 1, overflow: TextOverflow.ellipsis, style: const TextStyle(fontSize: 13, color: AppColors.sub)),
        trailing: IconButton(icon: const Icon(Icons.close, size: 18), onPressed: onClear),
      ),
    );
  }
}

class _Composer extends StatelessWidget {
  const _Composer({
    required this.voiceMode,
    required this.hasText,
    required this.recording,
    required this.recordCancel,
    required this.panelOpen,
    required this.emojiOpen,
    required this.controller,
    required this.focus,
    required this.onToggleVoice,
    required this.onEmoji,
    required this.onMore,
    required this.onSend,
    required this.onFocus,
    required this.onRecordStart,
    required this.onRecordMove,
    required this.onRecordEnd,
  });
  final bool voiceMode;
  final bool hasText;
  final bool recording;
  final bool recordCancel;
  final bool panelOpen;
  final bool emojiOpen;
  final TextEditingController controller;
  final FocusNode focus;
  final VoidCallback onToggleVoice;
  final VoidCallback onEmoji;
  final VoidCallback onMore;
  final VoidCallback onSend;
  final VoidCallback onFocus;
  final VoidCallback onRecordStart;
  final GestureLongPressMoveUpdateCallback onRecordMove;
  final VoidCallback onRecordEnd;

  static const _iconSize = 28.0;
  static const _barH = 36.0;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: EdgeInsets.fromLTRB(8, 8, 8, panelOpen ? 4 : 8),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.center,
        children: [
          _IconHit(
            onTap: onToggleVoice,
            child: Icon(voiceMode ? Icons.keyboard : Icons.mic_none, size: _iconSize, color: const Color(0xFF181818)),
          ),
          const SizedBox(width: 6),
          Expanded(
            child: voiceMode
                ? GestureDetector(
                    onLongPressStart: (_) => onRecordStart(),
                    onLongPressMoveUpdate: onRecordMove,
                    onLongPressEnd: (_) => onRecordEnd(),
                    onLongPressCancel: onRecordEnd,
                    child: Container(
                      height: _barH,
                      alignment: Alignment.center,
                      decoration: BoxDecoration(
                        color: recording ? (recordCancel ? const Color(0xFFFA5151) : const Color(0xFFE5E5E5)) : Colors.white,
                        borderRadius: BorderRadius.circular(4),
                      ),
                      child: Text(
                        recording ? (recordCancel ? '松开 取消' : '松开 发送') : '按住 说话',
                        style: TextStyle(fontSize: 16, fontWeight: FontWeight.w500, color: recording && recordCancel ? Colors.white : const Color(0xFF181818)),
                      ),
                    ),
                  )
                : TextField(
                    controller: controller,
                    focusNode: focus,
                    minLines: 1,
                    maxLines: 4,
                    textInputAction: TextInputAction.send,
                    style: const TextStyle(fontSize: 16, height: 1.25),
                    onTap: onFocus,
                    onSubmitted: (_) => onSend(),
                    decoration: InputDecoration(
                      filled: true,
                      fillColor: Colors.white,
                      isDense: true,
                      contentPadding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
                      border: OutlineInputBorder(borderRadius: BorderRadius.circular(4), borderSide: BorderSide.none),
                    ),
                  ),
          ),
          const SizedBox(width: 6),
          _IconHit(
            onTap: onEmoji,
            child: Icon(emojiOpen ? Icons.keyboard : Icons.emoji_emotions_outlined, size: _iconSize, color: const Color(0xFF181818)),
          ),
          const SizedBox(width: 4),
          if (hasText && !voiceMode)
            SizedBox(
              height: _barH,
              child: FilledButton(
                onPressed: onSend,
                style: FilledButton.styleFrom(
                  backgroundColor: AppColors.green,
                  minimumSize: const Size(56, 36),
                  padding: const EdgeInsets.symmetric(horizontal: 12),
                  shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(4)),
                ),
                child: const Text('发送', style: TextStyle(fontSize: 15)),
              ),
            )
          else
            _IconHit(
              onTap: onMore,
              // 更多面板打开时显示键盘图标，再点可收起（仿微信）
              child: Icon(panelOpen && !emojiOpen ? Icons.keyboard : Icons.add_circle_outline, size: _iconSize, color: const Color(0xFF181818)),
            ),
        ],
      ),
    );
  }
}

class _IconHit extends StatelessWidget {
  const _IconHit({required this.onTap, required this.child});
  final VoidCallback onTap;
  final Widget child;
  @override
  Widget build(BuildContext context) {
    return SizedBox(
      width: 36,
      height: 36,
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(18),
        child: Center(child: child),
      ),
    );
  }
}

class _RecordMask extends StatefulWidget {
  const _RecordMask({required this.cancel});
  final bool cancel;

  @override
  State<_RecordMask> createState() => _RecordMaskState();
}

class _RecordMaskState extends State<_RecordMask> with SingleTickerProviderStateMixin {
  late final AnimationController _pulse = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 900),
  )..repeat(reverse: true);

  @override
  void dispose() {
    _pulse.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final cancel = widget.cancel;
    return Positioned.fill(
      child: ColoredBox(
        color: const Color(0x99000000),
        child: Center(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Container(
                width: 168,
                height: 168,
                decoration: BoxDecoration(
                  color: cancel ? const Color(0xFFFA5151) : const Color(0xFF4C4C4C),
                  borderRadius: BorderRadius.circular(16),
                ),
                child: Column(
                  mainAxisAlignment: MainAxisAlignment.center,
                  children: [
                    // 卡片尺寸固定；只让图标轻微呼吸
                    AnimatedBuilder(
                      animation: _pulse,
                      builder: (context, child) {
                        final t = 0.92 + _pulse.value * 0.12;
                        final opacity = 0.75 + _pulse.value * 0.25;
                        return Opacity(
                          opacity: opacity,
                          child: Transform.scale(scale: t, child: child),
                        );
                      },
                      child: Icon(
                        cancel ? Icons.undo : Icons.mic,
                        color: Colors.white,
                        size: 56,
                      ),
                    ),
                    const SizedBox(height: 10),
                    if (!cancel)
                      const VoiceWaveIcon(
                        pointRight: true,
                        playing: true,
                        color: Colors.white70,
                        size: 28,
                      ),
                    const SizedBox(height: 8),
                    Text(
                      cancel ? '松开手指，取消发送' : '正在录音…上滑取消',
                      style: const TextStyle(color: Colors.white70, fontSize: 13),
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

class _StickerSuggestBar extends StatelessWidget {
  const _StickerSuggestBar({
    required this.store,
    required this.items,
    required this.onPick,
  });
  final AppStore store;
  final List<Sticker> items;
  final ValueChanged<Sticker> onPick;

  @override
  Widget build(BuildContext context) {
    return ColoredBox(
      color: const Color(0xFFF0F0F0),
      child: SizedBox(
        height: 72,
        child: ListView.separated(
          scrollDirection: Axis.horizontal,
          padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
          itemCount: items.length,
          separatorBuilder: (_, __) => const SizedBox(width: 8),
          itemBuilder: (context, i) {
            final s = items[i];
            return InkWell(
              onTap: () => onPick(s),
              borderRadius: BorderRadius.circular(8),
              child: SizedBox(
                width: 56,
                child: Column(
                  children: [
                    Expanded(
                      child: ClipRRect(
                        borderRadius: BorderRadius.circular(6),
                        child: s.fileId.isNotEmpty && store.api != null
                            ? AuthFileImage(
                                api: store.api,
                                fileId: s.fileId,
                                placeholder: ColoredBox(color: s.color),
                              )
                            : ColoredBox(color: s.color),
                      ),
                    ),
                    const SizedBox(height: 2),
                    Text(
                      s.emotion.isNotEmpty ? s.emotion : s.label,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(fontSize: 10, color: Color(0xFF666666)),
                    ),
                  ],
                ),
              ),
            );
          },
        ),
      ),
    );
  }
}

class _EmojiPanel extends StatefulWidget {
  const _EmojiPanel({
    required this.store,
    required this.hasText,
    required this.onPickEmoji,
    required this.onPickSticker,
    required this.onDelete,
    required this.onSend,
  });
  final AppStore store;
  final bool hasText;
  final ValueChanged<String> onPickEmoji;
  final ValueChanged<Sticker> onPickSticker;
  final VoidCallback onDelete;
  final VoidCallback onSend;

  @override
  State<_EmojiPanel> createState() => _EmojiPanelState();
}

class _EmojiPanelState extends State<_EmojiPanel> {
  /// 0 Unicode  1 全局组  2 我的组
  int tab = 0;
  String? openPackId;
  List<Sticker> packItems = const [];

  @override
  void initState() {
    super.initState();
    unawaited(widget.store.refreshStickersFromApi().then((_) {
      if (mounted) setState(() {});
    }));
  }

  static const emojis = [
    '😀', '😁', '😂', '🤣', '😊', '😇', '🙂', '😉', '😍', '🥰', '😘', '😗', '😋', '😜', '🤔', '🤨',
    '😐', '😑', '😶', '🙄', '😏', '😣', '😥', '😮', '😯', '😪', '😫', '🥱', '😴', '😌', '😛', '😝',
    '😒', '😓', '😔', '😕', '🙃', '🤑', '😲', '🙁', '😖', '😞', '😟', '😤', '😢', '😭', '😨', '😩',
    '🤯', '😬', '😰', '😱', '🥵', '🥶', '😳', '🤪', '😵', '😡', '😠', '😷', '🤒', '🤕', '🤢', '🤮',
    '👍', '👎', '👏', '🙌', '🤝', '✌️', '🤞', '🤟', '👌', '🙏', '💪', '❤️', '🧡', '💛', '💚', '💙',
    '💜', '🖤', '💔', '💯', '💢', '💥', '💫', '⭐', '🌟', '✨', '🔥', '🎉', '🎊', '🎈', '🎁', '🏆',
  ];

  Future<void> _openPack(StickerPack pack) async {
    final api = widget.store.api;
    if (api == null) return;
    try {
      final row = await api.getStickerPack(pack.id);
      final items = (row['items'] as List?) ?? const [];
      if (!mounted) return;
      setState(() {
        openPackId = pack.id;
        packItems = items.whereType<Map>().map((e) => Sticker.fromApi(Map<String, dynamic>.from(e))).toList();
      });
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('打开组失败：$e')));
      }
    }
  }

  Future<void> _addFromAlbum({String packId = ''}) async {
    final api = widget.store.api;
    final picked = await MediaPick.pickImagesFromGallery(already: 0);
    if (picked.isEmpty) return;
    final first = picked.first;
    final bytes = first.bytes;
    if (bytes == null || bytes.isEmpty) return;
    if (api == null || widget.store.demo) {
      widget.store.addSticker(
        Sticker('st-${DateTime.now().microsecondsSinceEpoch}', const Color(0xFF5B8FF9), label: '自定义表情'),
      );
      setState(() {});
      return;
    }
    try {
      final up = await api.uploadChatMedia(bytes: bytes, filename: first.name, kind: 'image');
      final fid = up['file_id']?.toString() ?? '';
      if (fid.isEmpty) throw ApiException('上传未返回 file_id');
      if (packId.isNotEmpty) {
        await api.appendPackItem(packId: packId, fileId: fid, label: '自定义表情');
        await _openPack(StickerPack(id: packId, title: ''));
      } else {
        final row = await api.collectSticker(fileId: fid, label: '自定义表情');
        widget.store.addSticker(Sticker.fromApi(row));
      }
      await widget.store.refreshStickersFromApi();
      if (mounted) setState(() {});
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('添加表情失败：$e')));
      }
    }
  }

  Future<void> _createMyPack() async {
    final api = widget.store.api;
    if (api == null) return;
    final title = await showWxInputDialog(
      context,
      title: '新建表情组',
      initial: '我的组',
      hint: '组名',
      confirmText: '创建',
    );
    if (title == null || title.isEmpty) return;
    try {
      await api.createStickerPack(title: title);
      await widget.store.refreshStickersFromApi();
      if (mounted) setState(() {});
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('创建失败：$e')));
      }
    }
  }

  Widget _packGrid(List<StickerPack> packs, {required bool mine}) {
    return GridView.builder(
      padding: const EdgeInsets.fromLTRB(10, 8, 10, 0),
      gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(
        crossAxisCount: 4,
        mainAxisSpacing: 10,
        crossAxisSpacing: 10,
        childAspectRatio: 0.85,
      ),
      itemCount: packs.length + (mine ? 1 : 0),
      itemBuilder: (context, i) {
        if (mine && i == 0) {
          return InkWell(
            onTap: _createMyPack,
            borderRadius: BorderRadius.circular(8),
            child: Container(
              decoration: BoxDecoration(
                color: const Color(0xFFF5F5F5),
                borderRadius: BorderRadius.circular(8),
                border: Border.all(color: const Color(0xFFE0E0E0)),
              ),
              child: const Column(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  Icon(Icons.add, size: 28, color: Color(0xFF888888)),
                  SizedBox(height: 4),
                  Text('新建组', style: TextStyle(fontSize: 11, color: Color(0xFF888888))),
                ],
              ),
            ),
          );
        }
        final pack = packs[mine ? i - 1 : i];
        final cover = pack.coverFileIds.isNotEmpty ? pack.coverFileIds.first : '';
        return InkWell(
          onTap: () => _openPack(pack),
          onLongPress: mine ? () => _confirmDeletePack(pack) : null,
          borderRadius: BorderRadius.circular(8),
          child: Column(
            children: [
              Expanded(
                child: ClipRRect(
                  borderRadius: BorderRadius.circular(8),
                  child: cover.isNotEmpty && widget.store.api != null
                      ? AuthFileImage(
                          api: widget.store.api,
                          fileId: cover,
                          placeholder: const ColoredBox(color: Color(0xFFE8E8E8)),
                        )
                      : const ColoredBox(color: Color(0xFFE8E8E8), child: Icon(Icons.collections, color: Color(0xFFAAAAAA))),
                ),
              ),
              const SizedBox(height: 4),
              Text(pack.title, maxLines: 1, overflow: TextOverflow.ellipsis, style: const TextStyle(fontSize: 11)),
            ],
          ),
        );
      },
    );
  }

  Future<void> _confirmDeletePack(StickerPack pack) async {
    final api = widget.store.api;
    if (api == null) return;
    final ok = await showWxConfirmDialog(
      context,
      title: '删除此组？',
      message: '将删除「${pack.title}」整组表情，此操作不可恢复。',
      confirmText: '删除',
      busyText: '删除中…',
      destructive: true,
      runOnConfirm: () async {
        await api.deleteStickerPack(pack.id);
        if (openPackId == pack.id) {
          openPackId = null;
          packItems = const [];
        }
        await widget.store.refreshStickersFromApi();
        if (mounted) setState(() {});
      },
    );
    if (ok == true && mounted) {
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('已删除表情组')));
    }
  }

  void _showPackItemMenu(Sticker s, Rect anchor) {
    HapticFeedback.mediumImpact();
    final canDelete = openPackId != null &&
        widget.store.stickerPacks.any((p) => p.id == openPackId && p.scope == 'account');
    final items = <_WxMenuItem>[
      _WxMenuItem(Icons.send_outlined, '发送', () => widget.onPickSticker(s)),
      if (canDelete)
        _WxMenuItem(Icons.delete_outline, '删除', () => _deletePackItem(s), danger: true),
    ];
    final overlay = Overlay.of(context);
    late OverlayEntry entry;
    entry = OverlayEntry(
      builder: (ctx) => _WxMsgMenuOverlay(
        bubble: anchor,
        mine: true,
        items: items,
        onDismiss: () => entry.remove(),
      ),
    );
    overlay.insert(entry);
  }

  Future<void> _deletePackItem(Sticker s) async {
    final api = widget.store.api;
    if (api == null || s.id.isEmpty) return;
    final ok = await showWxConfirmDialog(
      context,
      title: '删除表情？',
      message: '将从本组移除这张表情。',
      confirmText: '删除',
      destructive: true,
      busyText: '删除中…',
      runOnConfirm: () async {
        await api.deleteSticker(s.id);
        widget.store.removeSticker(s.id);
        if (openPackId != null) {
          await _openPack(StickerPack(id: openPackId!, title: ''));
        }
        await widget.store.refreshStickersFromApi();
      },
    );
    if (ok == true && mounted) {
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('已删除')));
    }
  }

  Widget _packItemsGrid() {
    final minePack = openPackId != null &&
        widget.store.stickerPacks.any((p) => p.id == openPackId && p.scope == 'account');
    return Column(
      children: [
        SizedBox(
          height: 36,
          child: Row(
            children: [
              IconButton(
                icon: const Icon(Icons.arrow_back_ios_new, size: 16),
                onPressed: () => setState(() {
                  openPackId = null;
                  packItems = const [];
                }),
              ),
              const Expanded(child: Text('组内表情', style: TextStyle(fontSize: 13))),
              if (minePack)
                IconButton(
                  icon: const Icon(Icons.add, size: 20),
                  onPressed: () => _addFromAlbum(packId: openPackId ?? ''),
                ),
            ],
          ),
        ),
        Expanded(
          child: GridView.builder(
            padding: const EdgeInsets.fromLTRB(10, 0, 10, 0),
            gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(
              crossAxisCount: 5,
              mainAxisSpacing: 10,
              crossAxisSpacing: 10,
            ),
            itemCount: packItems.length + (minePack ? 1 : 0),
            itemBuilder: (context, i) {
              if (minePack && i == 0) {
                return InkWell(
                  onTap: () => _addFromAlbum(packId: openPackId ?? ''),
                  borderRadius: BorderRadius.circular(6),
                  child: Container(
                    decoration: BoxDecoration(
                      color: const Color(0xFFF5F5F5),
                      borderRadius: BorderRadius.circular(6),
                      border: Border.all(color: const Color(0xFFE0E0E0)),
                    ),
                    child: const Icon(Icons.add, size: 28, color: Color(0xFF888888)),
                  ),
                );
              }
              final s = packItems[minePack ? i - 1 : i];
              return Builder(
                builder: (cellCtx) {
                  return InkWell(
                    onTap: () => widget.onPickSticker(s),
                    onLongPress: () {
                      final box = cellCtx.findRenderObject() as RenderBox?;
                      if (box == null || !box.hasSize) return;
                      final rect = box.localToGlobal(Offset.zero) & box.size;
                      _showPackItemMenu(s, rect);
                    },
                    child: ClipRRect(
                      borderRadius: BorderRadius.circular(6),
                      child: s.fileId.isNotEmpty && widget.store.api != null
                          ? AuthFileImage(api: widget.store.api, fileId: s.fileId, placeholder: ColoredBox(color: s.color))
                          : ColoredBox(color: s.color),
                    ),
                  );
                },
              );
            },
          ),
        ),
      ],
    );
  }

  @override
  Widget build(BuildContext context) {
    final packs = widget.store.stickerPacks;
    final globalPacks = packs.where((p) => p.scope == 'global').toList();
    final myPacks = packs.where((p) => p.scope == 'account').toList();
    return SizedBox(
      height: 248,
      child: Column(
        children: [
          Expanded(
            child: openPackId != null
                ? _packItemsGrid()
                : tab == 0
                    ? GridView.builder(
                        padding: const EdgeInsets.fromLTRB(6, 4, 6, 0),
                        gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(crossAxisCount: 8, mainAxisSpacing: 2, crossAxisSpacing: 2),
                        itemCount: emojis.length,
                        itemBuilder: (context, i) {
                          final e = emojis[i];
                          return InkWell(
                            onTap: () => widget.onPickEmoji(e),
                            child: Center(child: Text(e, style: const TextStyle(fontSize: 26))),
                          );
                        },
                      )
                    : tab == 1
                        ? _packGrid(globalPacks, mine: false)
                        : _packGrid(myPacks, mine: true),
          ),
          Container(
            height: 44,
            decoration: const BoxDecoration(border: Border(top: BorderSide(color: Color(0xFFE5E5E5), width: 0.5))),
            padding: const EdgeInsets.symmetric(horizontal: 6),
            child: Row(
              children: [
                _EmojiTab(label: '😀', selected: tab == 0 && openPackId == null, onTap: () => setState(() { tab = 0; openPackId = null; })),
                _EmojiTab(label: '全', selected: tab == 1 && openPackId == null, onTap: () => setState(() { tab = 1; openPackId = null; })),
                _EmojiTab(label: '我', selected: tab == 2 && openPackId == null, onTap: () => setState(() { tab = 2; openPackId = null; })),
                const Spacer(),
                if (widget.hasText)
                  SizedBox(
                    height: 32,
                    child: FilledButton(
                      onPressed: widget.onSend,
                      style: FilledButton.styleFrom(
                        backgroundColor: AppColors.green,
                        padding: const EdgeInsets.symmetric(horizontal: 14),
                        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(4)),
                      ),
                      child: const Text('发送', style: TextStyle(fontSize: 14)),
                    ),
                  )
                else if (tab == 0)
                  TextButton(onPressed: widget.onDelete, child: const Text('删除', style: TextStyle(color: Color(0xFF666666), fontSize: 15))),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _EmojiTab extends StatelessWidget {
  const _EmojiTab({required this.label, required this.selected, required this.onTap});
  final String label;
  final bool selected;
  final VoidCallback onTap;
  @override
  Widget build(BuildContext context) {
    return InkWell(
      onTap: onTap,
      child: Container(
        width: 44,
        height: 36,
        alignment: Alignment.center,
        decoration: BoxDecoration(
          color: selected ? const Color(0xFFE8E8E8) : Colors.transparent,
          borderRadius: BorderRadius.circular(6),
        ),
        child: Text(label, style: const TextStyle(fontSize: 20)),
      ),
    );
  }
}

class _MorePanel extends StatelessWidget {
  const _MorePanel({required this.onPick});
  final ValueChanged<String> onPick;

  /// 8 个入口、两行；链接改由聊天里直接发，AI 自行读取
  static const double panelHeight = 248;

  @override
  Widget build(BuildContext context) {
    final items = [
      ('相册', Icons.photo, 'image'),
      ('拍摄', Icons.photo_camera, 'camera'),
      ('视频', Icons.movie_creation_outlined, 'video'),
      ('文件', Icons.insert_drive_file, 'file'),
      ('一起听歌', Icons.headphones, 'listen'),
      ('位置', Icons.location_on_outlined, 'location'),
      ('语音通话', Icons.call, 'voice_call'),
      ('视频通话', Icons.videocam, 'video_call'),
    ];
    const padH = 12.0;
    const padV = 10.0;
    const spacing = 10.0;
    const cols = 4;
    const rows = 2;
    return LayoutBuilder(
      builder: (context, constraints) {
        final w = constraints.maxWidth;
        final cellW = (w - padH * 2 - spacing * (cols - 1)) / cols;
        final cellH = (panelHeight - padV * 2 - spacing * (rows - 1)) / rows;
        final aspect = cellW / cellH;
        return SizedBox(
          height: panelHeight,
          child: GridView.count(
            padding: const EdgeInsets.fromLTRB(padH, padV, padH, padV),
            physics: const NeverScrollableScrollPhysics(),
            crossAxisCount: cols,
            mainAxisSpacing: spacing,
            crossAxisSpacing: spacing,
            childAspectRatio: aspect,
            children: [
              for (final item in items)
                InkWell(
                  onTap: () => onPick(item.$3),
                  child: Column(mainAxisAlignment: MainAxisAlignment.center, children: [
                    Container(
                      width: 56,
                      height: 56,
                      decoration: BoxDecoration(color: Colors.white, borderRadius: BorderRadius.circular(12)),
                      child: Icon(item.$2, color: const Color(0xFF333333), size: 28),
                    ),
                    const SizedBox(height: 6),
                    Text(item.$1, style: const TextStyle(fontSize: 12, color: Color(0xFF666666))),
                  ]),
                ),
            ],
          ),
        );
      },
    );
  }
}

class _Bubble extends StatelessWidget {
  const _Bubble({
    required this.store,
    required this.conv,
    required this.msg,
    required this.selecting,
    required this.selected,
    required this.voicePlaying,
    required this.onSelect,
    required this.onLong,
    required this.onRetry,
    required this.onPlayVoice,
    required this.onAvatar,
  });
  final AppStore store;
  final Conv conv;
  final Msg msg;
  final bool selecting;
  final bool selected;
  final bool voicePlaying;
  final VoidCallback onSelect;
  final ValueChanged<Rect> onLong;
  final VoidCallback onRetry;
  final VoidCallback onPlayVoice;
  final VoidCallback onAvatar;

  @override
  Widget build(BuildContext context) {
    if (msg.kind == MsgKind.system) {
      final level = (msg.meta?['level'] as String?) ?? '';
      final warn = level == 'warn';
      final action = (msg.meta?['action'] as String?) ?? '';
      final link = (msg.meta?['link'] as String?) ?? '';
      final setupKind = ((msg.meta?['setup'] as String?) ?? '').trim().toLowerCase();
      final setupPid = ((msg.meta?['persona_id'] as String?) ?? conv.personaId ?? '').trim();
      final body = msg.text;
      Widget child;
      if (action == 'open_persona_draw' && link.isNotEmpty) {
        final idx = body.indexOf(link);
        final before = idx >= 0 ? body.substring(0, idx) : body;
        final after = idx >= 0 ? body.substring(idx + link.length) : '';
        final drawTab = setupKind == 'image' ? 1 : 0;
        child = Text.rich(
          TextSpan(
            style: TextStyle(fontSize: 12, height: 1.35, color: warn ? AppColors.badge : AppColors.sub),
            children: [
              if (before.isNotEmpty) TextSpan(text: before),
              TextSpan(
                text: link,
                style: const TextStyle(
                  color: AppColors.name,
                  decoration: TextDecoration.underline,
                  fontWeight: FontWeight.w600,
                ),
                recognizer: (TapGestureRecognizer()
                  ..onTap = () {
                    if (setupPid.isEmpty) return;
                    final p = store.persona(setupPid);
                    if (p == null) return;
                    Navigator.push(
                      context,
                      MaterialPageRoute(
                        builder: (_) => PersonaDrawPage(
                          store: store,
                          persona: p,
                          initialTab: drawTab,
                        ),
                      ),
                    );
                  }),
              ),
              if (after.isNotEmpty) TextSpan(text: after),
            ],
          ),
          textAlign: TextAlign.center,
        );
      } else {
        child = Text(
          body,
          textAlign: TextAlign.center,
          style: TextStyle(
            fontSize: 12,
            height: 1.35,
            color: warn ? AppColors.badge : AppColors.sub,
          ),
        );
      }
      return Padding(
        padding: const EdgeInsets.symmetric(vertical: 6, horizontal: 24),
        child: Center(child: child),
      );
    }

    final mine = msg.mine;
    final senderName = store.nameOfSender(msg.senderId);
    final senderColor = msg.senderId == store.meId ? const Color(0xFF4C8DFF) : (store.persona(msg.senderId)?.color ?? store.colorOf(conv));
    final quote = msg.quoteId == null ? null : store.msgs(conv.id).where((e) => e.id == msg.quoteId).firstOrNull;

    final radius = BorderRadius.only(
      topLeft: Radius.circular(mine ? 6 : 2),
      topRight: Radius.circular(mine ? 2 : 6),
      bottomLeft: const Radius.circular(6),
      bottomRight: const Radius.circular(6),
    );

    // 图/视频/链接（含听歌）是独立卡片，不要套绿/白聊天气泡
    // 语音通话气泡走微信绿/白条，不当独立卡片
    final isVoiceCallMsg = msg.kind == MsgKind.link && msg.meta?['card']?.toString() == 'voice_call';
    final isSingMsg = msg.kind == MsgKind.voice &&
        (msg.meta?['kind_extra']?.toString() == 'sing' || msg.meta?['card']?.toString() == 'sing_track');
    final standaloneCard = !isVoiceCallMsg &&
        (msg.kind == MsgKind.image ||
            msg.kind == MsgKind.video ||
            msg.kind == MsgKind.link ||
            isSingMsg);

    Widget body;
    EdgeInsets pad = const EdgeInsets.symmetric(horizontal: 12, vertical: 9);
    switch (msg.kind) {
      case MsgKind.image:
        body = _ImageCard(store: store, msg: msg);
        pad = EdgeInsets.zero;
      case MsgKind.voice:
        if (isSingMsg) {
          body = _SingMusicCard(
            msg: msg,
            store: store,
            voicePlaying: voicePlaying,
            onPlay: onPlayVoice,
          );
          pad = EdgeInsets.zero;
        } else {
          body = _VoiceBubbleBody(msg: msg, mine: mine, voicePlaying: voicePlaying);
          pad = const EdgeInsets.symmetric(horizontal: 10, vertical: 4);
        }
      case MsgKind.file:
        body = SizedBox(
          width: 180,
          child: Row(children: [
            const Icon(Icons.insert_drive_file, color: AppColors.name),
            const SizedBox(width: 8),
            Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [Text(msg.text, maxLines: 1, overflow: TextOverflow.ellipsis), Text(fileSize(msg.bytes), style: const TextStyle(fontSize: 12, color: AppColors.sub))])),
          ]),
        );
      case MsgKind.link:
        body = _LinkCardBody(msg: msg, store: store, conv: conv);
        pad = isVoiceCallMsg
            ? const EdgeInsets.symmetric(horizontal: 12, vertical: 10)
            : EdgeInsets.zero;
      case MsgKind.video:
        final meta = msg.meta ?? const <String, dynamic>{};
        final dur = msg.seconds > 0 ? msg.seconds : (meta['duration'] is num ? (meta['duration'] as num).round() : 0);
        final files = meta['files'];
        final fileId = (files is List && files.isNotEmpty) ? files.first.toString() : null;
        body = GestureDetector(
          onTap: () {
            if (fileId == null || store.api == null) {
              ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('视频还不能播放')));
              return;
            }
            Navigator.push(
              context,
              MaterialPageRoute(
                builder: (_) => VideoPlayerPage(api: store.api!, fileId: fileId),
              ),
            );
          },
          child: SizedBox(
            width: 200,
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Container(
                  height: 110,
                  width: double.infinity,
                  decoration: BoxDecoration(color: const Color(0xFF2A2A2A), borderRadius: BorderRadius.circular(8)),
                  child: Stack(
                    alignment: Alignment.center,
                    children: [
                      const Icon(Icons.play_circle_fill, color: Colors.white70, size: 44),
                      if (dur > 0)
                        Positioned(
                          right: 6,
                          bottom: 6,
                          child: Text(
                            '${dur ~/ 60}:${(dur % 60).toString().padLeft(2, '0')}',
                            style: const TextStyle(color: Colors.white, fontSize: 11),
                          ),
                        ),
                    ],
                  ),
                ),
                if (msg.text.isNotEmpty && !msg.text.startsWith('[')) ...[
                  const SizedBox(height: 6),
                  Text(msg.text, maxLines: 2, overflow: TextOverflow.ellipsis, style: const TextStyle(fontSize: 13, color: Color(0xFF333333))),
                ],
                if (msg.bytes > 0)
                  Text(fileSize(msg.bytes), style: const TextStyle(fontSize: 11, color: AppColors.sub)),
              ],
            ),
          ),
        );
        pad = EdgeInsets.zero;
      default:
        body = Text(msg.text, style: const TextStyle(fontSize: 16, height: 1.35, color: Color(0xFF111111)));
    }

    final bubble = Builder(
      builder: (bubbleCtx) => GestureDetector(
      onLongPressStart: selecting
          ? null
          : (_) {
              final box = bubbleCtx.findRenderObject() as RenderBox?;
              if (box == null || !box.hasSize) return;
              final origin = box.localToGlobal(Offset.zero);
              onLong(origin & box.size);
            },
      onTap: selecting
          ? onSelect
          : () {
              if (msg.kind == MsgKind.voice) onPlayVoice();
            },
      child: Container(
        // 普通语音按时长定宽；翻唱音乐卡片定宽
        width: msg.kind == MsgKind.voice
            ? (isSingMsg ? 248.0 : voiceBubbleWidth(msg.seconds) + 20.0)
            : null,
        constraints: BoxConstraints(
          maxWidth: standaloneCard ? 280 : 260,
          minHeight: standaloneCard ? 0 : (msg.kind == MsgKind.voice ? 40 : 36),
        ),
        padding: pad,
        // 独立卡片：透明无气泡底；文本/语音仍用绿/白气泡
        decoration: standaloneCard
            ? null
            : BoxDecoration(color: mine ? AppColors.mine : Colors.white, borderRadius: radius),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: [
            if (quote != null && !standaloneCard)
              Container(
                width: double.infinity,
                margin: const EdgeInsets.only(bottom: 6),
                padding: const EdgeInsets.all(6),
                color: const Color(0x22000000),
                child: Text('${store.nameOfSender(quote.senderId)}: ${quote.text}', maxLines: 2, overflow: TextOverflow.ellipsis, style: const TextStyle(fontSize: 12, color: Color(0xFF555555))),
              ),
            body,
          ],
        ),
      ),
    ),
    );

    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisAlignment: mine ? MainAxisAlignment.end : MainAxisAlignment.start,
        children: [
          if (selecting)
            Padding(
              padding: const EdgeInsets.only(right: 8, top: 10),
              child: Icon(selected ? Icons.check_circle : Icons.circle_outlined, color: selected ? AppColors.green : AppColors.muted, size: 22),
            ),
          if (!mine)
            GestureDetector(
              onTap: () {
                final fid = store.persona(msg.senderId)?.avatarFileId ?? '';
                if (fid.isNotEmpty) {
                  openAvatarPreview(context, api: store.api, fileId: fid, label: senderName);
                } else {
                  onAvatar();
                }
              },
              onLongPress: onAvatar,
              child: Avatar(
                senderName,
                senderColor,
                size: 40,
                api: store.api,
                avatarFileId: store.persona(msg.senderId)?.avatarFileId.isEmpty == false
                    ? store.persona(msg.senderId)!.avatarFileId
                    : null,
              ),
            ),
          if (!mine) const SizedBox(width: 8),
          Flexible(
            child: Column(
              crossAxisAlignment: mine ? CrossAxisAlignment.end : CrossAxisAlignment.start,
              children: [
                if (conv.isGroup && !mine) Padding(padding: const EdgeInsets.only(bottom: 2, left: 2), child: Text(senderName, style: const TextStyle(fontSize: 12, color: AppColors.sub))),
                Row(
                  mainAxisSize: MainAxisSize.min,
                  crossAxisAlignment: CrossAxisAlignment.center,
                  children: [
                    if (mine && msg.state == SendState.failed)
                      IconButton(onPressed: onRetry, icon: const Icon(Icons.error, color: AppColors.badge, size: 20), padding: EdgeInsets.zero, constraints: const BoxConstraints()),
                    if (mine && msg.state == SendState.sending)
                      const Padding(padding: EdgeInsets.only(right: 4), child: SizedBox(width: 14, height: 14, child: CircularProgressIndicator(strokeWidth: 2))),
                    // 语音/图片等已有固有宽度，勿用 Flexible 拉满屏宽
                    msg.kind == MsgKind.voice || msg.kind == MsgKind.image || msg.kind == MsgKind.video || msg.kind == MsgKind.link
                        ? bubble
                        : Flexible(child: bubble),
                    if (!mine && msg.kind == MsgKind.voice && !msg.heard)
                      const Padding(padding: EdgeInsets.only(left: 6), child: Icon(Icons.circle, size: 8, color: AppColors.badge)),
                  ],
                ),
                // 微信样式：转写文字在语音气泡下方独立一块，不塞进绿/白条里
                if (msg.kind == MsgKind.voice) _VoiceAsrBelow(msg: msg, mine: mine),
              ],
            ),
          ),
          if (mine) const SizedBox(width: 8),
          if (mine)
            GestureDetector(
              onTap: () {
                final fid = store.meAvatarFileId;
                if (fid.isEmpty) return;
                openAvatarPreview(context, api: store.api, fileId: fid, label: store.meName);
              },
              child: Avatar(
                store.meName,
                const Color(0xFF4C8DFF),
                size: 40,
                api: store.api,
                avatarFileId: store.meAvatarFileId.isEmpty ? null : store.meAvatarFileId,
              ),
            ),
        ],
      ),
    );
  }
}

/// 新版微信多图：一张卡片叠层错开，卡内左右滑切图；单图仍是大方图。点按进全屏。
class _ImageCard extends StatefulWidget {
  const _ImageCard({required this.store, required this.msg});
  final AppStore store;
  final Msg msg;

  @override
  State<_ImageCard> createState() => _ImageCardState();
}

class _ImageCardState extends State<_ImageCard> {
  Widget _slideChild(ImageSlide s) {
    if (s.bytes != null) {
      return Image.memory(
        s.bytes!,
        fit: BoxFit.cover,
        width: double.infinity,
        height: double.infinity,
        cacheWidth: 720,
        gaplessPlayback: true,
      );
    }
    if (s.fileId != null) {
      // 气泡用较高清预览；点开全屏会自动拉原图
      return AuthFileImage(
        api: widget.store.api,
        fileId: s.fileId!,
        preview: true,
        decodeWidth: 720,
      );
    }
    return const ColoredBox(
      color: Color(0xFFD0D0D0),
      child: Center(child: Icon(Icons.image, color: Colors.white70)),
    );
  }

  @override
  Widget build(BuildContext context) {
    final meta = widget.msg.meta ?? const <String, dynamic>{};
    final slides = slidesFromImageMsg(widget.msg);
    final n = slides.isEmpty ? 1 : slides.length.clamp(1, 9);
    final caption = (meta['caption'] as String?)?.trim();
    final showCaption = (caption != null && caption.isNotEmpty) ||
        (widget.msg.text.isNotEmpty && !widget.msg.text.startsWith('['));

    void openAt(int i) {
      final g = sessionImageGallery(
        store: widget.store,
        convId: widget.msg.convId,
        current: widget.msg,
        localIndex: i,
      );
      openFullscreenImages(context, slides: g.slides, initialIndex: g.index, api: widget.store.api);
    }

    late Widget card;
    if (n == 1) {
      card = SizedBox(
        width: 120,
        height: 160,
        child: _FanCardShell(
          child: GestureDetector(
            onTap: () => openAt(0),
            child: slides.isEmpty ? const SizedBox.shrink() : _slideChild(slides.first),
          ),
        ),
      );
    } else {
      card = _FanStackCards(
        onOpenAt: openAt,
        children: [
          for (var i = 0; i < n; i++)
            slides.isEmpty ? const SizedBox.shrink() : _slideChild(slides[i]),
        ],
      );
    }

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      mainAxisSize: MainAxisSize.min,
      children: [
        card,
        if (showCaption)
          Padding(
            padding: const EdgeInsets.only(top: 6, left: 2, right: 2),
            child: Text(caption ?? widget.msg.text, style: const TextStyle(fontSize: 15, height: 1.3)),
          ),
      ],
    );
  }
}

/// 竖长方形黑底卡片壳。
class _FanCardShell extends StatelessWidget {
  const _FanCardShell({required this.child, this.width = 120, this.height = 160});
  final Widget child;
  final double width;
  final double height;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: width,
      height: height,
      decoration: BoxDecoration(
        color: Colors.black,
        borderRadius: BorderRadius.circular(8),
        boxShadow: const [
          BoxShadow(color: Color(0x44000000), blurRadius: 8, offset: Offset(0, 3)),
        ],
      ),
      clipBehavior: Clip.antiAlias,
      child: child,
    );
  }
}

/// 多图扇形堆叠：竖长方形、黑底、底中为旋转原点；
/// 顶卡 0°（竖着），后面错开角度；左右滑切换顶卡；点按进全屏预览。
class _FanStackCards extends StatefulWidget {
  const _FanStackCards({
    required this.children,
    required this.onOpenAt,
    this.onLongPress,
    this.cardWidth = 120,
    this.cardHeight = 160,
  });

  final List<Widget> children;
  final ValueChanged<int> onOpenAt;
  final VoidCallback? onLongPress;
  final double cardWidth;
  final double cardHeight;

  @override
  State<_FanStackCards> createState() => _FanStackCardsState();
}

class _FanStackCardsState extends State<_FanStackCards> with TickerProviderStateMixin {
  static const _maxVisible = 9;
  /// 只露 2 张后卡：露边就是「下一张」，避免更后面的图抢镜
  static const _maxBehind = 2;
  /// 半开向右：后卡顺时针错开，顶边露在右侧（实测负角会跑到左边）
  static const _stepDeg = 5.5;

  int _top = 0;
  double _dragDeg = 0;
  bool _busy = false;

  int get _n => widget.children.length.clamp(0, _maxVisible);

  /// depth=0 竖直；后面只往右偏
  double _depthRest(int depth) {
    if (depth <= 0) return 0;
    return _stepDeg * depth;
  }

  /// 堆叠顺序：顶卡 → 下一张 → 下下张（和左右滑切到的下一张一致）
  int _indexAtDepth(int depth) => (_top + depth) % _n;

  Future<void> _flipToNext({required bool forward}) async {
    if (_busy || _n <= 1) return;
    _busy = true;
    final startDrag = _dragDeg;
    // 右往左滑：顶卡往左甩走（负角），露出右侧下一张
    final endDrag = forward ? -52.0 : 52.0;
    final nextTop = forward ? (_top + 1) % _n : (_top + _n - 1) % _n;

    final t = AnimationController(vsync: this, duration: const Duration(milliseconds: 260));
    final curved = CurvedAnimation(parent: t, curve: Curves.easeInOut);
    void tick() {
      setState(() {
        _dragDeg = startDrag + (endDrag - startDrag) * curved.value;
      });
    }
    curved.addListener(tick);
    await t.forward();
    curved.removeListener(tick);
    t.dispose();
    if (!mounted) return;
    setState(() {
      _top = nextTop;
      _dragDeg = 0;
      _busy = false;
    });
  }

  void _onDragUpdate(DragUpdateDetails d) {
    if (_busy || _n <= 1) return;
    setState(() {
      _dragDeg += d.delta.dx * 0.22;
      _dragDeg = _dragDeg.clamp(-48.0, 48.0);
    });
  }

  void _onDragEnd(DragEndDetails d) {
    if (_busy || _n <= 1) return;
    final v = d.primaryVelocity ?? 0;
    if (_dragDeg.abs() > 16 || v.abs() > 500) {
      // 右往左滑（负向）→ 下一张 2/7；左往右 → 上一张
      final forward = _dragDeg < 0 || v < 0;
      unawaited(_flipToNext(forward: forward));
    } else {
      setState(() => _dragDeg = 0);
    }
  }

  @override
  Widget build(BuildContext context) {
    if (_n <= 0) return const SizedBox.shrink();
    // 右侧留足空间给半开露边，左侧几乎贴齐
    final padL = widget.cardWidth * 0.02;
    final padR = widget.cardWidth * 0.32;
    final boxW = widget.cardWidth + padL + padR;
    final boxH = widget.cardHeight + 6;
    final behind = (_n - 1).clamp(0, _maxBehind);

    return GestureDetector(
      onTap: () => widget.onOpenAt(_top),
      onLongPress: widget.onLongPress,
      onHorizontalDragUpdate: _onDragUpdate,
      onHorizontalDragEnd: _onDragEnd,
      child: SizedBox(
        width: boxW,
        height: boxH,
        child: Stack(
          alignment: Alignment.bottomLeft,
          clipBehavior: Clip.none,
          children: [
            Positioned(
              left: padL,
              bottom: 0,
              width: widget.cardWidth,
              height: widget.cardHeight,
              child: Stack(
                alignment: Alignment.bottomCenter,
                clipBehavior: Clip.none,
                children: [
                  for (var depth = behind; depth >= 0; depth--)
                    _buildLayer(depth),
                ],
              ),
            ),
            if (_n > 1)
              Positioned(
                left: padL + 4,
                bottom: 4,
                child: IgnorePointer(
                  child: Container(
                    padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 3),
                    decoration: BoxDecoration(
                      color: const Color(0x99000000),
                      borderRadius: BorderRadius.circular(10),
                    ),
                    child: Text(
                      '${_top + 1}/$_n',
                      style: const TextStyle(color: Colors.white, fontSize: 11, height: 1.1),
                    ),
                  ),
                ),
              ),
          ],
        ),
      ),
    );
  }

  Widget _buildLayer(int depth) {
    final idx = _indexAtDepth(depth);
    final rest = _depthRest(depth);
    double deg = rest;
    if (depth == 0) {
      deg = rest + _dragDeg;
    } else if (depth == 1) {
      // 拖动时下一张往竖直靠拢，切换后就是它
      final t = (_dragDeg.abs() / 52.0).clamp(0.0, 1.0);
      deg = rest * (1.0 - t);
    }
    final rad = deg * math.pi / 180;
    final opacity = depth == 0 && _dragDeg.abs() > 28
        ? (1 - ((_dragDeg.abs() - 28) / 24).clamp(0.0, 0.6))
        : 1.0;
    // 后层明显缩小，只露一点边，不会顶出前卡
    final scale = switch (depth) {
      0 => 1.0,
      1 => 0.86,
      _ => 0.74,
    };

    return Transform.rotate(
      angle: rad,
      alignment: Alignment.bottomCenter,
      child: Transform.scale(
        scale: scale,
        alignment: Alignment.bottomCenter,
        child: Opacity(
          opacity: opacity,
          child: _FanCardShell(
            width: widget.cardWidth,
            height: widget.cardHeight,
            child: widget.children[idx],
          ),
        ),
      ),
    );
  }
}

class _VoiceBubbleBody extends StatelessWidget {
  const _VoiceBubbleBody({
    required this.msg,
    required this.mine,
    required this.voicePlaying,
  });
  final Msg msg;
  final bool mine;
  final bool voicePlaying;

  @override
  Widget build(BuildContext context) {
    final secs = msg.seconds > 0 ? msg.seconds : (msg.meta?['seconds'] is num ? (msg.meta!['seconds'] as num).toInt() : 0);
    return SizedBox(
      height: 40,
      child: Row(
        mainAxisAlignment: mine ? MainAxisAlignment.end : MainAxisAlignment.start,
        children: mine
            ? [
                Text('$secs"', style: const TextStyle(fontSize: 15, color: Color(0xFF111111))),
                const SizedBox(width: 6),
                VoiceWaveIcon(pointRight: false, playing: voicePlaying),
              ]
            : [
                VoiceWaveIcon(pointRight: true, playing: voicePlaying),
                const SizedBox(width: 6),
                Text('$secs"', style: const TextStyle(fontSize: 15, color: Color(0xFF111111))),
              ],
      ),
    );
  }
}

/// 翻唱：微信式音乐卡片（点封面/播放钮 → 一起听弹层）。
class _SingMusicCard extends StatelessWidget {
  const _SingMusicCard({
    required this.msg,
    required this.store,
    required this.voicePlaying,
    required this.onPlay,
  });
  final Msg msg;
  final AppStore store;
  final bool voicePlaying;
  final VoidCallback onPlay;

  @override
  Widget build(BuildContext context) {
    final meta = msg.meta ?? const <String, dynamic>{};
    final title = (meta['title']?.toString() ?? meta['caption']?.toString() ?? msg.text).trim();
    final artist = (meta['artist']?.toString() ?? '').trim();
    final secs = msg.seconds > 0
        ? msg.seconds
        : (meta['seconds'] is num ? (meta['seconds'] as num).toInt() : 0);
    final mm = secs ~/ 60;
    final ss = (secs % 60).toString().padLeft(2, '0');
    final dur = secs > 0 ? '$mm:$ss' : '--:--';
    return Material(
      color: Colors.white,
      borderRadius: BorderRadius.circular(10),
      elevation: 0.4,
      shadowColor: const Color(0x22000000),
      child: Padding(
        padding: const EdgeInsets.fromLTRB(10, 10, 6, 10),
        child: Row(
          children: [
            GestureDetector(
              onTap: onPlay,
              child: Container(
                width: 52,
                height: 52,
                decoration: BoxDecoration(
                  borderRadius: BorderRadius.circular(8),
                  gradient: const LinearGradient(
                    begin: Alignment.topLeft,
                    end: Alignment.bottomRight,
                    colors: [Color(0xFF3D4450), Color(0xFF1C1F26)],
                  ),
                ),
                child: Icon(
                  voicePlaying ? Icons.pause_rounded : Icons.music_note_rounded,
                  color: Colors.white.withValues(alpha: 0.92),
                  size: 28,
                ),
              ),
            ),
            const SizedBox(width: 10),
            Expanded(
              child: GestureDetector(
                onTap: onPlay,
                behavior: HitTestBehavior.opaque,
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      title.isEmpty ? '翻唱' : title,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w600, color: Color(0xFF111111)),
                    ),
                    const SizedBox(height: 3),
                    Text(
                      [
                        '翻唱',
                        if (artist.isNotEmpty) artist,
                        dur,
                      ].join(' · '),
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(fontSize: 12, color: Color(0xFF8E8E93)),
                    ),
                  ],
                ),
              ),
            ),
            IconButton(
              visualDensity: VisualDensity.compact,
              padding: EdgeInsets.zero,
              constraints: const BoxConstraints(minWidth: 40, minHeight: 40),
              onPressed: onPlay,
              icon: Icon(
                voicePlaying ? Icons.pause_circle_filled_rounded : Icons.play_circle_filled_rounded,
                size: 34,
                color: const Color(0xFF07C160),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

/// 微信：长按「转文字」后才在语音条下方显示转写；默认隐藏，不另发一条消息。
class _VoiceAsrBelow extends StatelessWidget {
  const _VoiceAsrBelow({required this.msg, required this.mine});
  final Msg msg;
  final bool mine;

  @override
  Widget build(BuildContext context) {
    final visible = msg.meta?['asr_visible'] == true;
    if (!visible) return const SizedBox.shrink();
    final asr = (msg.meta?['asr'] as String?)?.trim() ?? '';
    final body = asr.isEmpty ? '暂无识别文字' : asr;
    return Padding(
      padding: const EdgeInsets.only(top: 6),
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 240),
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 9),
          decoration: BoxDecoration(
            color: mine ? AppColors.mine : Colors.white,
            borderRadius: BorderRadius.circular(6),
          ),
          child: Text(
            body,
            style: TextStyle(
              fontSize: 16,
              height: 1.35,
              color: asr.isEmpty ? const Color(0xFF888888) : const Color(0xFF111111),
            ),
          ),
        ),
      ),
    );
  }
}

class _StickerPackStackCard extends StatefulWidget {
  const _StickerPackStackCard({required this.msg, required this.meta, this.store});
  final Msg msg;
  final Map<String, dynamic> meta;
  final AppStore? store;

  @override
  State<_StickerPackStackCard> createState() => _StickerPackStackCardState();
}

class _StickerPackStackCardState extends State<_StickerPackStackCard> {
  late bool _subscribed;

  @override
  void initState() {
    super.initState();
    _subscribed = widget.meta['subscribed'] == true;
  }

  Future<List<ImageSlide>> _packSlides() async {
    final api = widget.store?.api;
    final packId = widget.meta['pack_id']?.toString() ?? '';
    final covers = (widget.meta['cover_file_ids'] is List)
        ? [for (final c in widget.meta['cover_file_ids'] as List) c.toString()]
        : const <String>[];
    if (api == null) {
      return [for (final f in covers.where((e) => e.isNotEmpty).take(9)) ImageSlide(fileId: f)];
    }
    if (packId.isEmpty) {
      return [for (final f in covers.where((e) => e.isNotEmpty).take(9)) ImageSlide(fileId: f)];
    }
    try {
      final row = await api.getStickerPack(packId);
      final items = (row['items'] as List?) ?? const [];
      final slides = <ImageSlide>[];
      for (final e in items) {
        if (e is! Map) continue;
        final fid = e['file_id']?.toString() ?? '';
        if (fid.isEmpty) continue;
        slides.add(ImageSlide(fileId: fid, label: e['label']?.toString() ?? ''));
        if (slides.length >= 9) break;
      }
      if (slides.isNotEmpty) return slides;
    } catch (_) {}
    return [for (final f in covers.where((e) => e.isNotEmpty).take(9)) ImageSlide(fileId: f)];
  }

  Future<void> _previewPack({int initial = 0}) async {
    final slides = await _packSlides();
    if (!mounted || slides.isEmpty) return;
    await openFullscreenImages(
      context,
      slides: slides,
      initialIndex: initial.clamp(0, slides.length - 1),
      api: widget.store?.api,
    );
  }

  Future<void> _confirmFork() async {
    final s = widget.store;
    final api = s?.api;
    final packId = widget.meta['pack_id']?.toString() ?? '';
    if (s == null || api == null || packId.isEmpty) return;
    if (_subscribed) {
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('已在「全」里')));
      return;
    }
    final ok = await showWxConfirmDialog(
      context,
      title: '加到表情？',
      message: '将「${widget.meta['title'] ?? '表情组'}」加入面板「全」分组，不会复制到「我的组」。',
      confirmText: '添加',
      busyText: '添加中…',
      runOnConfirm: () async {
        // 只订阅，不 fork 复制——避免一堆表情自动塞进「我的组」
        await api.subscribeStickerPack(packId);
        if (!mounted) return;
        setState(() => _subscribed = true);
        unawaited(s.refreshStickersFromApi());
      },
    );
    if (ok == true && mounted) {
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('已加到「全」')));
    }
  }

  void _showPackMenu(Rect anchor) {
    HapticFeedback.mediumImpact();
    final items = <_WxMenuItem>[
      _WxMenuItem(Icons.visibility_outlined, '预览', () => unawaited(_previewPack())),
      _WxMenuItem(
        _subscribed ? Icons.check_circle_outline : Icons.star_outline,
        _subscribed ? '已添加' : '收藏',
        _subscribed ? () {} : _confirmFork,
      ),
    ];
    final overlay = Overlay.of(context);
    late OverlayEntry entry;
    entry = OverlayEntry(
      builder: (ctx) => _WxMsgMenuOverlay(
        bubble: anchor,
        mine: false,
        items: items,
        onDismiss: () => entry.remove(),
      ),
    );
    overlay.insert(entry);
  }

  @override
  Widget build(BuildContext context) {
    final meta = widget.meta;
    final packTitle = meta['title']?.toString() ?? '表情包';
    final n = (meta['item_count'] is num) ? (meta['item_count'] as num).toInt() : 0;
    final covers = (meta['cover_file_ids'] is List)
        ? [for (final c in meta['cover_file_ids'] as List) c.toString()]
        : const <String>[];
    final api = widget.store?.api;
    final stackCovers = covers.where((e) => e.isNotEmpty).take(9).toList();
    final count = (n > 0 ? n : stackCovers.length).clamp(0, 9);

    Widget coverChild(String fid) {
      if (api == null) {
        return const ColoredBox(color: Colors.black, child: Icon(Icons.collections, color: Color(0xFF888888)));
      }
      return AuthFileImage(
        api: api,
        fileId: fid,
        placeholder: const ColoredBox(color: Colors.black),
      );
    }

    // 只用真实封面图，禁止用首张垫重复——否则露边图和滑到的下一张会对不上
    final fanChildren = <Widget>[
      for (final fid in stackCovers) coverChild(fid),
    ];

    void openMenu(BuildContext cellCtx) {
      final box = cellCtx.findRenderObject() as RenderBox?;
      if (box == null || !box.hasSize) return;
      _showPackMenu(box.localToGlobal(Offset.zero) & box.size);
    }

    final totalRaw = n > 0 ? n : stackCovers.length;
    final moreHint = totalRaw > 9 ? ' · 点开看更多' : '';

    return Builder(
      builder: (cellCtx) {
        return Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: [
            if (fanChildren.isEmpty)
              GestureDetector(
                onTap: () => unawaited(_previewPack()),
                onLongPress: () => openMenu(cellCtx),
                child: const _FanCardShell(
                  child: ColoredBox(
                    color: Colors.black,
                    child: Icon(Icons.collections, color: Color(0xFF888888), size: 40),
                  ),
                ),
              )
            else if (fanChildren.length == 1)
              GestureDetector(
                onTap: () => unawaited(_previewPack()),
                onLongPress: () => openMenu(cellCtx),
                child: _FanCardShell(child: fanChildren.first),
              )
            else
              _FanStackCards(
                onOpenAt: (i) => unawaited(_previewPack(initial: i)),
                onLongPress: () => openMenu(cellCtx),
                children: fanChildren,
              ),
            const SizedBox(height: 6),
            SizedBox(
              width: 200,
              child: Text(
                packTitle,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: const TextStyle(fontSize: 13, fontWeight: FontWeight.w600, color: Color(0xFF111111)),
              ),
            ),
            SizedBox(
              width: 200,
              child: Text(
                count > 0
                    ? (_subscribed ? '$count 张 · 已添加$moreHint' : '$count 张 · 长按收藏$moreHint')
                    : (_subscribed ? '已添加' : '长按收藏'),
                style: const TextStyle(fontSize: 11, color: Color(0xFF888888)),
              ),
            ),
          ],
        );
      },
    );
  }
}

class _LinkCardBody extends StatelessWidget {
  const _LinkCardBody({required this.msg, this.store, this.conv});
  final Msg msg;
  final AppStore? store;
  final Conv? conv;

  @override
  Widget build(BuildContext context) {
    final meta = msg.meta ?? const <String, dynamic>{};
    final pct = meta['progress'];
    final status = meta['status']?.toString() ?? '';
    final isListen = meta['card']?.toString() == 'listen_invite';
    final isVoiceCall = meta['card']?.toString() == 'voice_call';
    final isVoiceCallInvite = meta['card']?.toString() == 'voice_call_invite';
    final isPack = meta['card']?.toString() == 'sticker_pack_added';
    final isDeviceReq = meta['card']?.toString() == 'device_create_request';
    final isLoc = meta['card']?.toString() == 'location';
    final isFwd = meta['card']?.toString() == 'forward';
    final isOg = meta['card']?.toString() == 'og' || (meta['url'] != null && meta['job_id'] == null && status.isEmpty);

    if (isVoiceCallInvite) {
      final st = meta['status']?.toString() ?? 'ringing';
      final body = (meta['body']?.toString() ?? '').trim().isNotEmpty
          ? meta['body'].toString().trim()
          : '邀请你语音通话';
      final ringing = st == 'ringing' || st.isEmpty;
      final declined = st == 'declined' || st == 'canceled' || st == 'missed';
      return SizedBox(
        width: 228,
        child: Container(
          padding: const EdgeInsets.all(12),
          decoration: BoxDecoration(
            color: const Color(0xFF1C2A3A),
            borderRadius: BorderRadius.circular(10),
            border: Border.all(color: const Color(0xFF3A4A5C)),
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Container(
                    width: 36,
                    height: 36,
                    decoration: BoxDecoration(
                      color: const Color(0xFF07C160).withValues(alpha: 0.9),
                      borderRadius: BorderRadius.circular(10),
                    ),
                    child: const Icon(Icons.phone_in_talk_rounded, color: Colors.white, size: 20),
                  ),
                  const SizedBox(width: 10),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        const Text('语音通话', style: TextStyle(color: Colors.white, fontSize: 14, fontWeight: FontWeight.w600)),
                        Text(
                          declined ? '已拒绝' : (st == 'answered' ? '已接听' : body),
                          maxLines: 2,
                          overflow: TextOverflow.ellipsis,
                          style: TextStyle(color: Colors.white.withValues(alpha: 0.65), fontSize: 12),
                        ),
                      ],
                    ),
                  ),
                ],
              ),
              if (ringing) ...[
                const SizedBox(height: 10),
                Row(
                  children: [
                    Expanded(
                      child: TextButton(
                        onPressed: () {
                          final m = Map<String, dynamic>.from(msg.meta ?? const {});
                          m['status'] = 'declined';
                          m['body'] = '已拒绝';
                          msg.meta = m;
                          store?.ping();
                        },
                        style: TextButton.styleFrom(foregroundColor: const Color(0xFFAEAEB2)),
                        child: const Text('拒绝'),
                      ),
                    ),
                    Expanded(
                      child: FilledButton(
                        onPressed: () async {
                          final s = store;
                          final c = conv;
                          if (s == null || c == null) return;
                          final m = Map<String, dynamic>.from(msg.meta ?? const {});
                          m['status'] = 'answered';
                          m['body'] = '已接听';
                          msg.meta = m;
                          s.ping();
                          await launchRealtimeVoiceCall(context, s, c);
                        },
                        style: FilledButton.styleFrom(backgroundColor: const Color(0xFF07C160)),
                        child: const Text('接听'),
                      ),
                    ),
                  ],
                ),
              ],
            ],
          ),
        ),
      );
    }

    if (isVoiceCall) {
      // 微信样式：气泡内电话图标 + 文案（通话时长 / 语音通话 / 已取消）
      final st = meta['status']?.toString() ?? '';
      final dur = meta['duration_sec'];
      final durSec = dur is num ? dur.toInt() : int.tryParse('$dur') ?? msg.seconds;
      String label;
      if (st == 'canceled') {
        label = '已取消';
      } else if (st == 'ended' && durSec > 0) {
        final m = durSec ~/ 60;
        final s = durSec % 60;
        label = '通话时长 ${m.toString().padLeft(2, '0')}:${s.toString().padLeft(2, '0')}';
      } else if ((meta['body']?.toString() ?? '').trim().isNotEmpty) {
        label = meta['body'].toString().trim();
      } else if (msg.text.trim().isNotEmpty) {
        label = msg.text.trim();
      } else {
        label = '语音通话';
      }
      final fg = msg.mine ? const Color(0xFF111111) : const Color(0xFF111111);
      return Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(Icons.phone, size: 20, color: fg),
          const SizedBox(width: 6),
          Text(label, style: TextStyle(color: fg, fontSize: 16, height: 1.2)),
        ],
      );
    }
    final title = meta['title']?.toString() ?? msg.text;
    final url = meta['url']?.toString() ?? '';
    final desc = meta['description']?.toString() ?? '';
    final imageUrl = meta['image']?.toString() ?? '';

    if (isDeviceReq) {
      final reqId = meta['request_id']?.toString() ?? '';
      final label = meta['label']?.toString().isNotEmpty == true
          ? meta['label'].toString()
          : ((meta['kind']?.toString() == 'android') ? '虚拟手机' : '虚拟电脑');
      final reason = meta['reason']?.toString() ?? '';
      final st = meta['status']?.toString() ?? 'pending';
      final pending = st == 'pending';
      return SizedBox(
        width: 240,
        child: Container(
          padding: const EdgeInsets.all(12),
          decoration: BoxDecoration(
            color: const Color(0xFF1C2A3A),
            borderRadius: BorderRadius.circular(10),
            border: Border.all(color: const Color(0xFF3A4A5C)),
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Text('申请创建设备', style: TextStyle(color: Colors.white, fontSize: 13, fontWeight: FontWeight.w600)),
              const SizedBox(height: 6),
              Text(label, style: const TextStyle(color: Color(0xFFF2F2F7), fontSize: 15, fontWeight: FontWeight.w600)),
              if (reason.isNotEmpty) ...[
                const SizedBox(height: 4),
                Text(reason, style: const TextStyle(color: Color(0xFFAEAEB2), fontSize: 12)),
              ],
              const SizedBox(height: 10),
              if (!pending)
                Text(
                  st == 'approved' ? '已批准创建' : (st == 'denied' ? '已拒绝' : st),
                  style: TextStyle(
                    color: st == 'approved' ? const Color(0xFF07C160) : const Color(0xFFAEAEB2),
                    fontSize: 12,
                  ),
                )
              else
                Row(
                  children: [
                    Expanded(
                      child: TextButton(
                        onPressed: () async {
                          final api = store?.api;
                          if (api == null || reqId.isEmpty) return;
                          try {
                            await api.denyDeviceCreateRequest(reqId);
                            if (context.mounted) {
                              ScaffoldMessenger.of(context).showSnackBar(
                                const SnackBar(content: Text('已拒绝创建')),
                              );
                            }
                          } catch (e) {
                            if (context.mounted) {
                              ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$e')));
                            }
                          }
                        },
                        style: TextButton.styleFrom(foregroundColor: const Color(0xFFAEAEB2)),
                        child: const Text('拒绝'),
                      ),
                    ),
                    Expanded(
                      child: FilledButton(
                        onPressed: () async {
                          final api = store?.api;
                          if (api == null || reqId.isEmpty) return;
                          try {
                            await api.approveDeviceCreateRequest(reqId);
                            if (context.mounted) {
                              ScaffoldMessenger.of(context).showSnackBar(
                                const SnackBar(content: Text('已批准，设备已创建')),
                              );
                            }
                          } catch (e) {
                            if (context.mounted) {
                              ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$e')));
                            }
                          }
                        },
                        style: FilledButton.styleFrom(backgroundColor: const Color(0xFF07C160)),
                        child: const Text('批准'),
                      ),
                    ),
                  ],
                ),
            ],
          ),
        ),
      );
    }

    if (isPack) {
      return _StickerPackStackCard(msg: msg, store: store, meta: meta);
    }

    if (isLoc) {
      final lat = (meta['lat'] as num?)?.toDouble();
      final lng = (meta['lng'] as num?)?.toDouble();
      var name = meta['name']?.toString() ?? '位置';
      var addr = meta['address']?.toString() ?? '';
      if (lat != null && lng != null) {
        final r = PlaceApprox.resolve(lat: lat, lng: lng, name: name, address: addr);
        name = r.name;
        addr = r.address;
      }
      return Container(
        width: 220,
        padding: const EdgeInsets.all(10),
        decoration: BoxDecoration(
          color: const Color(0xFF2C2C2E),
          borderRadius: BorderRadius.circular(10),
          border: Border.all(color: const Color(0xFF3A3A3C)),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Row(children: [
              Icon(Icons.location_on, color: Color(0xFFFE2C55), size: 20),
              SizedBox(width: 6),
              Text('位置', style: TextStyle(color: Colors.white, fontWeight: FontWeight.w600)),
            ]),
            const SizedBox(height: 6),
            Text(name, style: const TextStyle(color: Color(0xFFF2F2F7), fontSize: 14)),
            if (addr.isNotEmpty && addr != name)
              Text(addr, style: const TextStyle(color: Color(0xFFAEAEB2), fontSize: 12)),
          ],
        ),
      );
    }
    if (isFwd) {
      final n = meta['count'] ?? (meta['items'] is List ? (meta['items'] as List).length : 0);
      return Container(
        width: 220,
        padding: const EdgeInsets.all(10),
        decoration: BoxDecoration(
          color: const Color(0xFF2C2C2E),
          borderRadius: BorderRadius.circular(10),
          border: Border.all(color: const Color(0xFF3A3A3C)),
        ),
        child: Text(
          '${meta['title'] ?? '聊天记录'} · $n 条',
          style: const TextStyle(color: Color(0xFFF2F2F7), fontSize: 14),
        ),
      );
    }

    if (isListen) {
      final mood = meta['mood']?.toString() ?? '随心情';
      final n = meta['track_count'];
      final sid = meta['session_id']?.toString() ?? '';
      final body = meta['body']?.toString() ?? '一起听 · $mood';
      final ended = (meta['status']?.toString() ?? '') == 'ended';
      return SizedBox(
        width: 228,
        child: InkWell(
          onTap: ended
              ? () {
                  ScaffoldMessenger.of(context).showSnackBar(
                    const SnackBar(content: Text('这场一起听已结束，需要的话让对方再邀一次')),
                  );
                }
              : () {
                  final s = store;
                  if (s == null || sid.isEmpty) return;
                  final name = conv?.personaId != null ? s.persona(conv!.personaId!)?.display : null;
                  Navigator.of(context).push(
                    MaterialPageRoute(
                      builder: (_) => ListenTogetherPage(
                        store: s,
                        sessionId: sid,
                        convId: conv?.id,
                        personaName: name,
                      ),
                    ),
                  );
                },
          child: Opacity(
            opacity: ended ? 0.55 : 1,
            child: Container(
              padding: const EdgeInsets.all(10),
              decoration: BoxDecoration(
                gradient: const LinearGradient(
                  colors: [Color(0xFF2A1840), Color(0xFF1A1028)],
                  begin: Alignment.topLeft,
                  end: Alignment.bottomRight,
                ),
                borderRadius: BorderRadius.circular(10),
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      Container(
                        width: 36,
                        height: 36,
                        decoration: BoxDecoration(
                          color: const Color(0xFFFE2C55).withValues(alpha: 0.9),
                          borderRadius: BorderRadius.circular(10),
                        ),
                        child: const Icon(Icons.headphones, color: Colors.white, size: 20),
                      ),
                      const SizedBox(width: 10),
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            const Text('一起听歌', style: TextStyle(color: Colors.white, fontSize: 14, fontWeight: FontWeight.w600)),
                            Text(
                              n == null ? mood : '$mood · $n 首',
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis,
                              style: TextStyle(color: Colors.white.withValues(alpha: 0.65), fontSize: 12),
                            ),
                          ],
                        ),
                      ),
                      Container(
                        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
                        decoration: BoxDecoration(
                          color: ended ? const Color(0xFF555555) : const Color(0xFFFE2C55),
                          borderRadius: BorderRadius.circular(14),
                        ),
                        child: Text(
                          ended ? '已结束' : '进入',
                          style: const TextStyle(color: Colors.white, fontSize: 12, fontWeight: FontWeight.w600),
                        ),
                      ),
                    ],
                  ),
                  if (body.isNotEmpty)
                    Padding(
                      padding: const EdgeInsets.only(top: 8),
                      child: Text(body, maxLines: 2, overflow: TextOverflow.ellipsis, style: TextStyle(color: Colors.white.withValues(alpha: 0.75), fontSize: 12)),
                    ),
                ],
              ),
            ),
          ),
        ),
      );
    }

    // 链接类自带卡片底（灰底描边），不依赖外层白气泡
    const cardBg = Color(0xFF2C2C2E);
    const cardBorder = Color(0xFF3A3A3C);
    const titleColor = Color(0xFFF2F2F7);
    const subColor = Color(0xFFAEAEB2);

    if (isOg) {
      return Container(
        width: 228,
        padding: const EdgeInsets.all(10),
        decoration: BoxDecoration(
          color: cardBg,
          borderRadius: BorderRadius.circular(10),
          border: Border.all(color: cardBorder),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            if (imageUrl.startsWith('http'))
              ClipRRect(
                borderRadius: BorderRadius.circular(6),
                child: AspectRatio(
                  aspectRatio: 1.9,
                  child: Image.network(
                    imageUrl,
                    fit: BoxFit.cover,
                    errorBuilder: (_, __, ___) => const ColoredBox(
                      color: Color(0xFF3A3A3C),
                      child: Center(child: Icon(Icons.link, color: subColor)),
                    ),
                  ),
                ),
              )
            else
              const Padding(
                padding: EdgeInsets.only(bottom: 4),
                child: Icon(Icons.link, size: 20, color: Color(0xFFFE2C55)),
              ),
            const SizedBox(height: 6),
            Text(title, maxLines: 2, overflow: TextOverflow.ellipsis, style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w500, color: titleColor)),
            if (desc.isNotEmpty)
              Padding(
                padding: const EdgeInsets.only(top: 4),
                child: Text(desc, maxLines: 2, overflow: TextOverflow.ellipsis, style: const TextStyle(fontSize: 12, color: subColor, height: 1.3)),
              ),
            if (url.isNotEmpty)
              Padding(
                padding: const EdgeInsets.only(top: 4),
                child: Text(url, maxLines: 1, overflow: TextOverflow.ellipsis, style: const TextStyle(fontSize: 11, color: subColor)),
              ),
          ],
        ),
      );
    }

    return Container(
      width: 220,
      padding: const EdgeInsets.all(10),
      decoration: BoxDecoration(
        color: cardBg,
        borderRadius: BorderRadius.circular(10),
        border: Border.all(color: cardBorder),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(children: [
            const Icon(Icons.link, size: 18, color: Color(0xFFFE2C55)),
            const SizedBox(width: 6),
            Expanded(
              child: Text(
                title,
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
                style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w500, color: titleColor),
              ),
            ),
          ]),
          if (url.isNotEmpty)
            Padding(
              padding: const EdgeInsets.only(top: 4),
              child: Text(url, maxLines: 1, overflow: TextOverflow.ellipsis, style: const TextStyle(fontSize: 12, color: subColor)),
            ),
          if (status == 'running' || status == 'queued' || msg.state == SendState.sending)
            Padding(
              padding: const EdgeInsets.only(top: 8),
              child: LinearProgressIndicator(
                value: pct is num && pct > 0 ? pct.toDouble() / 100 : null,
                minHeight: 3,
                backgroundColor: const Color(0xFF3A3A3C),
                color: const Color(0xFFFE2C55),
              ),
            ),
          if (meta['duration'] != null)
            Padding(
              padding: const EdgeInsets.only(top: 4),
              child: Text('时长 ${meta['duration']}s', style: const TextStyle(fontSize: 12, color: subColor)),
            ),
        ],
      ),
    );
  }
}
