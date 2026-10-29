import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:uuid/uuid.dart';

import 'api/api_client.dart';
import 'api/realtime.dart';
import 'api/session.dart';
import 'format.dart';
import 'local_cache.dart';
import 'media/auth_image.dart';
import 'media/image_compress.dart';
import 'models.dart';

enum TopToastKind { message, listenInvite }

class TopToast {
  TopToast({
    required this.id,
    required this.kind,
    required this.title,
    this.subtitle = '',
    this.convId,
    this.sessionId,
    this.personaId,
  });

  final String id;
  final TopToastKind kind;
  final String title;
  final String subtitle;
  final String? convId;
  final String? sessionId;
  final String? personaId;
}

class AppStore extends ChangeNotifier {
  /// 当前登录会话（听歌浮窗等根 Overlay 用）。
  static AppStore? active;

  /// 本地已删除、禁止再被缓存/增量 sync 加回来的会话 id。
  final Set<String> deletedConvIds = {};

  /// 已登录远程服务器：先空壳，再 `applySync`。
  AppStore.remote({
    required this.serverUrl,
    required this.serverName,
    required this.accountName,
    required this.meId,
    required this.meName,
    this.meAvatarFileId = '',
    required ApiClient apiClient,
    required this.accessToken,
    required this.refreshToken,
  }) : demo = false {
    AppStore.active = this;
    api = apiClient;
    api!.accessToken = accessToken;
    api!.refreshToken = refreshToken;
    api!.onTokensUpdated = (tokens) async {
      accessToken = tokens.accessToken;
      refreshToken = tokens.refreshToken;
      api?.accessToken = tokens.accessToken;
      api?.refreshToken = tokens.refreshToken;
      await SessionStore().save(serverUrl: serverUrl, tokens: tokens);
    };
    channels = [
      Channel('ds', 'DeepSeek', 'deepseek-chat', 'https://api.deepseek.com'),
      Channel('qwen', '通义千问', 'qwen-plus', 'https://dashscope.aliyuncs.com'),
      Channel('zhipu', '智谱', 'glm-4-flash', 'https://open.bigmodel.cn'),
      Channel('kimi', 'Kimi', 'moonshot-v1-8k', 'https://api.moonshot.cn'),
      Channel('mm', 'MiniMax', 'abab6.5s-chat', 'https://api.minimax.chat'),
      Channel('db', '豆包', 'doubao-pro', 'https://ark.cn-beijing.volces.com'),
    ];
    personas = [];
    groups = [];
    tags = [];
    conversations = [];
    messages = {};
    moments = [];
    notices = [];
    coverHint = '点封面可更换';
  }

  late String meId;
  late String meName;
  String meAvatarFileId = '';
  String meSignature = '';
  String meCoverFileId = '';
  late bool demo;
  late String serverUrl;
  late String serverName;
  late String accountName;
  late List<Channel> channels;
  late List<Persona> personas;
  late List<Group> groups;
  late List<TagGroup> tags;
  late List<Conv> conversations;
  late Map<String, List<Msg>> messages;
  late List<Moment> moments;
  late List<MomentNotice> notices;
  late String coverHint;

  ApiClient? api;
  String? accessToken;
  String? refreshToken;
  RealtimeClient? _realtime;
  Timer? _healthTimer;

  /// 后端是否可达。断联时 UI 顶栏会提示，避免和业务错误混在一起。
  bool serverOnline = true;
  String? lastOfflineReason;

  /// 会话正在输入（微信：标题显示「对方正在输入」）。
  final Set<String> typingConvIds = {};
  final Map<String, String> typingNames = {};
  /// 虚拟设备操作进度（看 AI / 聊天可展示）
  final Map<String, String> deviceProgress = {};
  /// 最近一轮对话上下文/缓存命中（WS turn_metrics）
  final Map<String, Map<String, dynamic>> turnMetrics = {};

  bool isTyping(String convId) => typingConvIds.contains(convId);

  String typingTitle(String convId, String fallback) {
    // 微信固定文案，不带具体昵称
    if (!isTyping(convId)) return fallback;
    return '对方正在输入…';
  }

  bool greet = true;
  bool momentsGlobal = true;
  String quietStart = '23:00';
  String quietEnd = '08:00';

  /// 回复整形总开关（关=出站不整形，与旧行为一致）。
  bool shapeEnabled = true;
  /// 长回复分句发送（可选）。
  bool splitSend = false;
  /// 最近一次整形提示（如连续回声）。
  String? shapeHint;
  Timer? _draftTimer;

  /// 当前正在看的会话：该会话新消息不涨未读、不弹顶栏。
  String? activeConvId;

  /// 本地已点进会话清过未读：同步时不被服务端旧 unread 盖回来（后端断联时尤其关键）。
  final Set<String> _locallyReadConvIds = {};
  final Set<String> _pendingMarkRead = {};

  /// 顶栏居中通知（跨会话消息 / 一起听邀请）。
  TopToast? topToast;
  Timer? _topToastTimer;
  void Function(TopToast toast)? onTopToastNavigate;

  /// 自定义表情：优先从 `/api/v1/stickers` 拉取；本地色块作兜底。
  final List<Sticker> stickers = [];
  final List<StickerPack> stickerPacks = [];

  Future<void> refreshStickersFromApi() async {
    final client = api;
    if (client == null) return;
    try {
      final raw = await client.listStickers();
      stickers
        ..clear()
        ..addAll(
          raw.whereType<Map>().map((e) => Sticker.fromApi(Map<String, dynamic>.from(e))),
        );
      final packs = await client.listStickerPacks();
      stickerPacks
        ..clear()
        ..addAll(
          packs.whereType<Map>().map((e) => StickerPack.fromApi(Map<String, dynamic>.from(e))),
        );
      ping();
    } catch (_) {}
  }

  Future<List<Sticker>> suggestStickers(String q) async {
    final client = api;
    if (client == null || q.trim().isEmpty) return const [];
    try {
      final raw = await client.suggestStickers(q: q.trim());
      return raw.whereType<Map>().map((e) => Sticker.fromApi(Map<String, dynamic>.from(e))).toList();
    } catch (_) {
      return const [];
    }
  }

  void addSticker(Sticker s) {
    stickers.insert(0, s);
    ping();
  }

  void removeSticker(String id) {
    stickers.removeWhere((s) => s.id == id);
    ping();
  }

  int get unreadTotal => conversations.where((c) => !c.muted && !c.hidden).fold(0, (s, c) => s + c.unread);
  int get requestCount => personas.where((p) => p.state == FriendState.pendingIn || p.state == FriendState.pendingOut).length;
  int get noticeUnread => notices.where((n) => !n.read).length;

  List<Persona> get friends => personas.where((p) => p.state == FriendState.friend).toList();
  List<Persona> get requests => personas.where((p) => p.state == FriendState.pendingIn || p.state == FriendState.pendingOut).toList();

  Persona? persona(String id) {
    for (final p in personas) {
      if (p.id == id) return p;
    }
    return null;
  }

  Group? group(String id) {
    for (final g in groups) {
      if (g.id == id) return g;
    }
    return null;
  }

  Channel? channel(String id) {
    for (final c in channels) {
      if (c.id == id) return c;
    }
    return null;
  }

  Conv? conv(String id) {
    for (final c in conversations) {
      if (c.id == id) return c;
    }
    return null;
  }

  String titleOf(Conv c) {
    if (c.groupId != null) {
      final g = group(c.groupId!);
      if (g == null) return '群聊';
      // 有群备注时列表显示备注（仅自己可见）
      if (g.remark.trim().isNotEmpty) return g.remark.trim();
      return g.name;
    }
    final pid = c.personaId;
    if (pid == null || pid.isEmpty) return '会话';
    return persona(pid)?.display ?? '会话';
  }

  Color colorOf(Conv c) {
    if (c.groupId != null) return const Color(0xFF07C160);
    final pid = c.personaId;
    if (pid == null || pid.isEmpty) return Colors.grey;
    return persona(pid)?.color ?? Colors.grey;
  }

  String nameOfSender(String id, {String? groupId}) {
    if (id == meId) {
      if (groupId != null) {
        final nick = group(groupId)?.myNick.trim();
        if (nick != null && nick.isNotEmpty) return nick;
      }
      return meName;
    }
    return persona(id)?.display ?? id;
  }

  String memberDisplay(String id, Group g) {
    if (id == meId) {
      final nick = g.myNick.trim();
      return nick.isEmpty ? meName : nick;
    }
    if (g.showNicks) return persona(id)?.display ?? id;
    return persona(id)?.name ?? id;
  }

  List<Msg> msgs(String convId) => messages.putIfAbsent(convId, () => []);

  void ping() => notifyListeners();

  String _nid(String p) => '$p${DateTime.now().microsecondsSinceEpoch}';

  Conv ensureConv(Persona p) {
    for (final c in conversations) {
      if (c.personaId == p.id) return c;
    }
    final c = Conv(id: 'c-${p.id}', personaId: p.id, preview: '', time: DateTime.now());
    conversations.insert(0, c);
    messages[c.id] = [];
    return c;
  }

  /// 通讯录「发消息」：云端 open-chat（恢复软删会话），并清本地 deleted 黑名单。
  Future<Conv> openChatWithPersona(Persona p) async {
    if (demo || api == null) return ensureConv(p);
    final raw = await api!.openPersonaChat(p.id);
    final convRaw = Map<String, dynamic>.from(raw['conversation'] as Map? ?? raw);
    final c = _convFromJson(convRaw);
    deletedConvIds.remove(c.id);
    conversations.removeWhere((e) => e.personaId == p.id || e.id == c.id);
    conversations.insert(0, c);
    messages.putIfAbsent(c.id, () => []);
    try {
      final cache = LocalSyncCache(serverUrl, meId);
      await cache.saveDeletedConvIds(deletedConvIds);
      await cache.save(_snapshotForCache(), cursor: (await cache.loadWithCursor())?.cursor);
    } catch (_) {}
    ping();
    return c;
  }

  void accept(Persona p) {
    p.state = FriendState.friend;
    final c = ensureConv(p);
    final m = Msg(id: _nid('sys'), convId: c.id, mine: false, senderId: 'system', kind: MsgKind.system, text: '我通过了你的朋友验证请求，现在我们可以开始聊天了', time: DateTime.now());
    msgs(c.id).add(m);
    c.preview = m.text;
    c.time = m.time;
    c.unread = 1;
    if (p.momentsOn) {
      moments.insert(0, Moment(id: _nid('mo'), authorId: p.id, text: '今晚的风有点凉。', time: DateTime.now(), sample: true));
    }
    ping();
  }

  void ignore(Persona p) {
    personas.remove(p);
    ping();
  }

  void addOutgoing(String name, String verify) {
    personas.add(Persona(
      id: _nid('p'),
      name: name,
      color: Color(0xFF000000 | (name.hashCode & 0xFFFFFF)),
      bio: '新添加的人。',
      channelId: channels.first.id,
      state: FriendState.pendingOut,
      verify: verify,
      isBot: true,
    ));
    ping();
  }

  /// 新建人设（直接成为好友并开会话）。远端走 POST /personas，再 sync。
  Future<Persona> createPersona({
    required String name,
    String bio = '',
    Color? color,
    String? channelId,
    bool greet = true,
    bool momentsOn = true,
    String avatarFileId = '',
  }) async {
    final trimmed = name.trim();
    if (trimmed.isEmpty) throw StateError('名字不能为空');
    final col = color ?? Color(0xFF000000 | (trimmed.hashCode & 0xFFFFFF));
    final chId = (channelId != null && channelId.isNotEmpty)
        ? channelId
        : (channels.where((c) => c.keyFilled).firstOrNull?.id ?? channels.firstOrNull?.id ?? '');

    if (!demo && api != null) {
      final raw = await api!.createPersona(
        name: trimmed,
        bio: bio.trim(),
        color: col.toARGB32(),
        channelId: chId,
        greet: greet,
        momentsOn: momentsOn,
        avatarFileId: avatarFileId,
      );
      await pullSync();
      final created = persona(raw['id'] as String? ?? '');
      if (created != null) return created;
      // sync 失败时仍用返回体拼一份
      final p = _personaFromJson(Map<String, dynamic>.from(raw));
      if (!personas.any((e) => e.id == p.id)) personas.add(p);
      ping();
      return p;
    }

    final p = Persona(
      id: _nid('p'),
      name: trimmed,
      color: col,
      bio: bio.trim().isEmpty ? '（还没写人设说明）' : bio.trim(),
      channelId: chId.isEmpty ? channels.first.id : chId,
      state: FriendState.friend,
      isBot: true,
      greet: greet,
      momentsOn: momentsOn,
      avatarFileId: avatarFileId,
    );
    personas.add(p);
    final c = Conv(id: 'c-${p.id}', personaId: p.id, preview: '', time: DateTime.now());
    conversations.insert(0, c);
    messages[c.id] = [];
    ping();
    return p;
  }

  void sendText(Conv c, String text, {String? quoteId}) {
    if (!demo && api != null) {
      _sendTextRemote(c, text, quoteId: quoteId);
      return;
    }
    final list = msgs(c.id);
    list.add(Msg(id: _nid('m'), convId: c.id, mine: true, senderId: meId, kind: MsgKind.text, text: text, time: DateTime.now(), state: SendState.sent, quoteId: quoteId));
    final bot = Msg(id: _nid('b'), convId: c.id, mine: false, senderId: c.personaId ?? (group(c.groupId!)?.memberIds.first ?? 'lin'), kind: MsgKind.text, text: '收到：「$text」。还没接到模型。', time: DateTime.now().add(const Duration(milliseconds: 200)));
    list.add(bot);
    c.preview = bot.text;
    c.time = bot.time;
    c.draft = '';
    ping();
  }

  Future<void> _sendTextRemote(Conv c, String text, {String? quoteId}) async {
    final clientId = const Uuid().v4();
    final optimistic = Msg(
      id: clientId,
      convId: c.id,
      mine: true,
      senderId: meId,
      kind: MsgKind.text,
      text: text,
      time: DateTime.now(),
      state: SendState.sending,
      quoteId: quoteId,
    );
    msgs(c.id).add(optimistic);
    c.preview = text;
    c.time = optimistic.time;
    c.draft = '';
    // 本地先进入「正在输入」，等 WS 对齐；失败时清掉
    if (c.personaId != null) {
      typingConvIds.add(c.id);
      typingNames[c.id] = persona(c.personaId!)?.display ?? '对方';
    }
    ping();
    try {
      final raw = await api!.sendMessage(convId: c.id, clientId: clientId, text: text, quoteId: quoteId);
      final message = Map<String, dynamic>.from(raw['message'] as Map? ?? raw);
      _upsertRemoteMessage(message, replaceClientId: clientId);
      // 不再预插空白机器人气泡；等待态走 typing 标题
      for (final r in (raw['replies'] as List? ?? const [])) {
        final map = Map<String, dynamic>.from(r as Map);
        final t = map['text'] as String? ?? '';
        if (t.isEmpty && (map['state'] as String? ?? '') == 'sending') continue;
        _upsertRemoteMessage(map);
      }
      ping();
    } catch (_) {
      optimistic.state = SendState.failed;
      typingConvIds.remove(c.id);
      ping();
    }
  }

  Future<void> pullSync() async {
    if (demo || api == null) return;
    final cache = LocalSyncCache(serverUrl, meId);
    deletedConvIds
      ..clear()
      ..addAll(await cache.loadDeletedConvIds());
    // 先本地缓存秒开；有游标则增量，否则整包 sync
    final cached = await cache.loadWithCursor();
    if (cached != null) {
      applySync(cached.data);
    }
    try {
      if (cached?.cursor != null && cached!.cursor!.isNotEmpty) {
        final delta = await api!.syncDelta(after: cached.cursor!);
        applyDelta(delta);
        // 增量不含人设/资料/会话背景等元数据，再拉整包合并（保留已增量的消息）
        try {
          final full = await api!.sync();
          _applyMetaFromSync(full);
        } catch (_) {}
        final snapshot = _snapshotForCache();
        await cache.save(snapshot, cursor: delta['cursor'] as String? ?? DateTime.now().toUtc().toIso8601String());
      } else {
        final data = await api!.sync();
        applySync(data);
        await cache.save(data, cursor: DateTime.now().toUtc().toIso8601String());
      }
    } catch (_) {
      // 增量失败回退整包；整包也失败则留在本地缓存（离线 / 后端未启动）
      try {
        final data = await api!.sync();
        applySync(data);
        await cache.save(data, cursor: DateTime.now().toUtc().toIso8601String());
      } catch (_) {}
    }
    try {
      await loadPrefs();
    } catch (_) {}
    try {
      await loadMoments();
    } catch (_) {}
    try {
      startRealtime();
    } catch (_) {}
  }

  /// 用整包 sync 刷新人设 / 我 / 会话背景，不丢本地已有消息。
  void _applyMetaFromSync(Map<String, dynamic> data) {
    final me = Map<String, dynamic>.from(data['me'] as Map? ?? {});
    if (me['id'] != null) meId = me['id'] as String;
    if (me['display_name'] != null) meName = me['display_name'] as String;
    if (me['username'] != null) accountName = me['username'] as String;
    if (me['avatar_file_id'] != null) meAvatarFileId = me['avatar_file_id'] as String? ?? '';
    if (me['signature'] != null) meSignature = me['signature'] as String? ?? '';
    if (me['moments_cover_file_id'] != null) meCoverFileId = me['moments_cover_file_id'] as String? ?? '';

    final remotePersonas = data['personas'] as List? ?? const [];
    if (remotePersonas.isNotEmpty) {
      personas = [
        for (final raw in remotePersonas) _personaFromJson(Map<String, dynamic>.from(raw as Map)),
      ];
    }

    final remoteRaw = data['conversations'] as List? ?? const [];
    final remoteIds = <String>{};
    final byId = {for (final c in conversations) c.id: c};
    for (final raw in remoteRaw) {
      final next = _convFromJson(Map<String, dynamic>.from(raw as Map));
      if (deletedConvIds.contains(next.id) || next.hidden) continue;
      remoteIds.add(next.id);
      deletedConvIds.remove(next.id); // 服务端又出现=用户从通讯录重新打开
      final old = byId[next.id];
      if (old != null) {
        old.bgName = next.bgName;
        old.bgFileId = next.bgFileId;
        old.pinned = next.pinned;
        old.muted = next.muted;
        old.hidden = next.hidden;
        old.draft = next.draft;
        old.preview = next.preview;
        old.unread = _mergeUnread(old.id, local: old.unread, remote: next.unread);
        old.time = next.time;
        if (old.bgFileId.isEmpty) old.bgImageBytes = null;
      } else {
        next.unread = _mergeUnread(next.id, local: 0, remote: next.unread);
        conversations.add(next);
      }
    }
    // 服务端已删 / 不在列表：本地也去掉（修「删了重启又回来」）
    conversations.removeWhere((c) => !remoteIds.contains(c.id) || deletedConvIds.contains(c.id) || c.hidden);
    for (final id in [...messages.keys]) {
      if (!remoteIds.contains(id)) messages.remove(id);
    }

    final remoteChannels = data['channels'] as List? ?? const [];
    if (remoteChannels.isNotEmpty) {
      channels = [
        for (final raw in remoteChannels) _channelFromJson(Map<String, dynamic>.from(raw as Map)),
      ];
    }

    // 修补旧本地缓存：媒体 text 曾被存成「[图片×n]」导致 files 丢失
    for (final raw in (data['messages'] as List? ?? const [])) {
      final remote = _msgFromJson(Map<String, dynamic>.from(raw as Map));
      if (remote.kind != MsgKind.image &&
          remote.kind != MsgKind.video &&
          remote.kind != MsgKind.file &&
          remote.kind != MsgKind.voice) {
        continue;
      }
      final files = remote.meta?['files'];
      if (files is! List || files.isEmpty) continue;
      final list = msgs(remote.convId);
      final i = list.indexWhere((e) => e.id == remote.id);
      if (i < 0) continue;
      final localFiles = list[i].meta?['files'];
      if (localFiles is List && localFiles.isNotEmpty) continue;
      list[i].meta = {
        ...?list[i].meta,
        ...?remote.meta,
      };
      if (list[i].text.isEmpty || list[i].text.startsWith('[')) {
        list[i].text = remote.text;
      }
    }
    ping();
  }

  /// 设置页改后端地址：更新本地会话与 API，并尝试重新同步。
  Future<void> updateServerUrl(String raw) async {
    if (api == null) return;
    final url = normalizeServerUrl(raw);
    if (url.isEmpty) return;
    serverUrl = url;
    final host = Uri.tryParse(url)?.host;
    serverName = (host != null && host.isNotEmpty) ? host : url;
    final token = api!.accessToken;
    api!.close();
    api = ApiClient(url)..accessToken = token;
    accessToken = token;
    await SessionStore().setServerUrl(url);
    ping();
    await pullSync();
  }

  Future<void> loadMoments() async {
    if (demo || api == null) return;
    try {
      final raw = await api!.listMoments(limit: 50);
      final list = raw['moments'] as List? ?? const [];
      moments = [
        for (final item in list) _momentFromJson(Map<String, dynamic>.from(item as Map)),
      ];
      ping();
    } catch (_) {}
  }

  Moment _momentFromJson(Map<String, dynamic> j) {
    final media = j['media'] as List? ?? const [];
    final likesRaw = j['likes'] as List? ?? const [];
    final commentsRaw = j['comments'] as List? ?? const [];
    DateTime time;
    try {
      time = DateTime.parse(j['created_at'] as String? ?? '').toLocal();
    } catch (_) {
      time = DateTime.now();
    }
    final mediaIds = [for (final m in media) m.toString()];
    return Moment(
      id: j['id'] as String,
      authorId: j['author_id'] as String? ?? '',
      text: j['text'] as String? ?? '',
      time: time,
      images: mediaIds.length,
      mediaIds: mediaIds,
      likes: [for (final id in likesRaw) nameOfSender(id as String)],
      comments: [
        for (final c in commentsRaw)
          MomentComment(
            nameOfSender((c as Map)['actor_id'] as String? ?? ''),
            c['text'] as String? ?? '',
            replyTo: c['reply_to'] == null ? null : nameOfSender(c['reply_to'] as String),
          ),
      ],
    );
  }

  Map<String, dynamic> _snapshotForCache() {
    return {
      'me': {
        'id': meId,
        'display_name': meName,
        'username': accountName,
        'avatar_file_id': meAvatarFileId,
        'signature': meSignature,
        'moments_cover_file_id': meCoverFileId,
      },
      'personas': [
        for (final p in personas)
          {
            'id': p.id,
            'name': p.name,
            'color': p.color.toARGB32(),
            'bio': p.bio,
            'channel_id': p.channelId,
            'state': p.state.name,
            'avatar_file_id': p.avatarFileId,
            'remark': p.remark,
            'greet': p.greet,
            'moments_on': p.momentsOn,
          },
      ],
      'conversations': [
        for (final c in conversations)
          {
            'id': c.id,
            'persona_id': c.personaId,
            'group_id': c.groupId,
            'preview': c.preview,
            'last_time': c.time.toUtc().toIso8601String(),
            'unread': c.unread,
            'pinned': c.pinned,
            'muted': c.muted,
            'draft': c.draft,
            'last_seq': 0,
            'bg_name': c.bgName,
            'bg_file_id': c.bgFileId,
          },
      ],
      'messages': [
        for (final list in messages.values)
          for (final m in list)
            {
              'id': m.id,
              'client_id': m.id,
              'seq': 0,
              'conv_id': m.convId,
              'sender_id': m.senderId,
              'mine': m.mine,
              'kind': m.kind.name,
              'text': _persistMsgText(m),
              'state': m.state.name,
              'created_at': m.time.toUtc().toIso8601String(),
              'seconds': m.seconds,
              'bytes': m.bytes,
              if (m.meta != null) 'meta': {
                for (final e in m.meta!.entries)
                  // 本地预览 bytes 不进磁盘（体积大）；file_id 足够
                  if (e.key != 'local_previews') e.key: e.value,
              },
            },
      ],
      'channels': [
        for (final ch in channels)
          {
            'id': ch.id,
            'name': ch.name,
            'model': ch.model,
            'base_url': ch.baseUrl,
            'key_filled': ch.keyFilled,
            'shared': ch.shared,
          },
      ],
    };
  }

  void applyDelta(Map<String, dynamic> data) {
    for (final raw in (data['conversations'] as List? ?? const [])) {
      final j = Map<String, dynamic>.from(raw as Map);
      final c = _convFromJson(j);
      if (deletedConvIds.contains(c.id) || c.hidden) {
        deletedConvIds.add(c.id);
        conversations.removeWhere((e) => e.id == c.id);
        messages.remove(c.id);
        continue;
      }
      final i = conversations.indexWhere((e) => e.id == c.id);
      if (i >= 0) {
        conversations[i] = c;
      } else {
        conversations.add(c);
      }
    }
    for (final raw in (data['messages'] as List? ?? const [])) {
      _upsertRemoteMessage(Map<String, dynamic>.from(raw as Map));
    }
    conversations.sort((a, b) {
      if (a.pinned != b.pinned) return a.pinned ? -1 : 1;
      return b.time.compareTo(a.time);
    });
    ping();
  }

  Future<void> loadPrefs() async {
    if (demo || api == null) return;
    try {
      final p = await api!.getPrefs();
      shapeEnabled = p['shape_enabled'] as bool? ?? true;
      splitSend = p['split_send'] as bool? ?? false;
      ping();
    } catch (_) {}
  }

  Future<void> saveShapePrefs() async {
    if (demo || api == null) return;
    try {
      await api!.putPrefs({
        'shape_enabled': shapeEnabled,
        'split_send': splitSend,
      });
    } catch (_) {}
  }

  void scheduleDraftSave(String convId, String draft) {
    final c = conv(convId);
    if (c != null) c.draft = draft;
    ping();
    if (demo || api == null) return;
    _draftTimer?.cancel();
    _draftTimer = Timer(const Duration(milliseconds: 700), () async {
      try {
        await api!.patchConversationDraft(convId, draft);
      } catch (_) {}
    });
  }

  Future<void> flushDraft(String convId, String draft) async {
    _draftTimer?.cancel();
    final c = conv(convId);
    if (c != null) c.draft = draft;
    ping();
    if (demo || api == null) return;
    try {
      await api!.patchConversationDraft(convId, draft);
    } catch (_) {}
  }

  Future<int> cleanConversationResidue(String convId) async {
    if (demo || api == null) return 0;
    final r = await api!.cleanResidue(convId);
    return (r['marked'] as num?)?.toInt() ?? 0;
  }

  int _mergeUnread(String convId, {required int local, required int remote}) {
    if (activeConvId == convId) return 0;
    if (_locallyReadConvIds.contains(convId)) {
      if (remote > 0) unawaited(markConversationRead(convId, localOnly: false));
      return 0;
    }
    return remote;
  }

  Timer? _markReadDebounce;
  final Set<String> _markReadQueued = {};

  /// 进入会话 / 在聊收到新消息 / 离开会话：本地立刻清零，并尽量打到服务端。
  Future<void> markConversationRead(String convId, {bool localOnly = false}) async {
    final c = conv(convId);
    if (c != null && c.unread != 0) {
      c.unread = 0;
      ping();
    }
    _locallyReadConvIds.add(convId);
    if (localOnly || demo || api == null) return;
    if (_pendingMarkRead.contains(convId)) return;
    _pendingMarkRead.add(convId);
    try {
      await api!.markRead(convId);
      // 仍在看该会话时保留本地已读标记，避免同步把服务端滞后 unread 盖回来
      if (activeConvId != convId) {
        _locallyReadConvIds.remove(convId);
      }
    } catch (_) {
      // 断联时保留本地已读；恢复后再同步时会重试
    } finally {
      _pendingMarkRead.remove(convId);
    }
  }

  /// 在聊时对方连发：合并打已读，避免每条都打 API。
  void scheduleMarkConversationRead(String convId) {
    if (convId.isEmpty) return;
    final c = conv(convId);
    if (c != null && c.unread != 0) {
      c.unread = 0;
      ping();
    }
    _locallyReadConvIds.add(convId);
    _markReadQueued.add(convId);
    _markReadDebounce?.cancel();
    _markReadDebounce = Timer(const Duration(milliseconds: 400), () {
      final ids = _markReadQueued.toList();
      _markReadQueued.clear();
      for (final id in ids) {
        unawaited(markConversationRead(id));
      }
    });
  }

  void applySync(Map<String, dynamic> data) {
    final me = Map<String, dynamic>.from(data['me'] as Map? ?? {});
    if (me['id'] != null) meId = me['id'] as String;
    if (me['display_name'] != null) meName = me['display_name'] as String;
    if (me['username'] != null) accountName = me['username'] as String;
    if (me['avatar_file_id'] != null) meAvatarFileId = me['avatar_file_id'] as String? ?? '';
    if (me['signature'] != null) meSignature = me['signature'] as String? ?? '';
    if (me['moments_cover_file_id'] != null) meCoverFileId = me['moments_cover_file_id'] as String? ?? '';

    personas = [
      for (final raw in (data['personas'] as List? ?? const []))
        _personaFromJson(Map<String, dynamic>.from(raw as Map)),
    ];

    conversations = [
      for (final raw in (data['conversations'] as List? ?? const []))
        () {
          final c = _convFromJson(Map<String, dynamic>.from(raw as Map));
          if (deletedConvIds.contains(c.id) || c.hidden) return null;
          c.unread = _mergeUnread(c.id, local: 0, remote: c.unread);
          return c;
        }(),
    ].whereType<Conv>().toList();

    messages = {};
    for (final raw in (data['messages'] as List? ?? const [])) {
      final m = _msgFromJson(Map<String, dynamic>.from(raw as Map));
      msgs(m.convId).add(m);
    }
    for (final list in messages.values) {
      list.sort((a, b) => a.time.compareTo(b.time));
    }

    final remoteChannels = data['channels'] as List? ?? const [];
    if (remoteChannels.isNotEmpty) {
      channels = [
        for (final raw in remoteChannels) _channelFromJson(Map<String, dynamic>.from(raw as Map)),
      ];
    }

    // 远程第一版朋友圈仍空，避免演示数据串进来
    moments = [];
    notices = [];
    groups = [];
    tags = [];
    ping();
  }

  Channel _channelFromJson(Map<String, dynamic> j) {
    return Channel(
      j['id'] as String,
      j['name'] as String? ?? '',
      j['model'] as String? ?? '',
      j['base_url'] as String? ?? '',
      keyFilled: j['key_filled'] as bool? ?? false,
      shared: j['shared'] as bool? ?? false,
    );
  }

  Future<void> saveChannelRemote({
    Channel? existing,
    required String name,
    required String model,
    required String baseUrl,
    String? apiKey,
    bool shared = false,
    bool clearKey = false,
  }) async {
    if (demo || api == null) return;
    if (existing == null) {
      final raw = await api!.createChannel(
        name: name,
        model: model,
        baseUrl: baseUrl,
        apiKey: apiKey,
        shared: shared,
      );
      channels.add(_channelFromJson(raw));
    } else {
      final raw = await api!.updateChannel(
        id: existing.id,
        name: name,
        model: model,
        baseUrl: baseUrl,
        apiKey: clearKey ? '' : apiKey,
      );
      final updated = _channelFromJson(raw);
      final i = channels.indexWhere((c) => c.id == existing.id);
      if (i >= 0) channels[i] = updated;
    }
    ping();
  }

  Future<void> deleteChannelRemote(Channel ch) async {
    if (demo || api == null) return;
    await api!.deleteChannel(ch.id);
    channels.removeWhere((c) => c.id == ch.id);
    ping();
  }

  Future<void> setPersonaChannel(Persona p, String channelId) async {
    p.channelId = channelId;
    ping();
    if (demo || api == null) return;
    try {
      await api!.patchPersona(p.id, {'channel_id': channelId});
    } catch (_) {}
  }

  final List<void Function(String event, Map<String, dynamic> payload)> _voiceListeners = [];

  void addVoiceRealtimeListener(void Function(String event, Map<String, dynamic> payload) fn) {
    _voiceListeners.add(fn);
  }

  void removeVoiceRealtimeListener(void Function(String event, Map<String, dynamic> payload) fn) {
    _voiceListeners.remove(fn);
  }

  void startRealtime() {
    if (demo || api == null || accessToken == null) return;
    _realtime?.close();
    _realtime = RealtimeClient(
      urlBuilder: () => api!.wsUrl(accessToken ?? ''),
      onBeforeReconnect: () async {
        final rt = refreshToken;
        if (rt == null || rt.isEmpty || api == null) return;
        try {
          final tokens = await api!.refresh(rt);
          accessToken = tokens.accessToken;
          refreshToken = tokens.refreshToken;
          api!.accessToken = tokens.accessToken;
          api!.refreshToken = tokens.refreshToken;
          await SessionStore().save(serverUrl: serverUrl, tokens: tokens);
        } catch (_) {}
      },
      onEvent: (event, payload) {
        if (event.startsWith('voice_')) {
          for (final l in List.of(_voiceListeners)) {
            try {
              l(event, payload);
            } catch (_) {}
          }
        }
        if (event == 'typing') {
          final convId = payload['conv_id'] as String?;
          if (convId == null) return;
          final active = payload['active'] == true;
          final name = payload['name'] as String? ?? '';
          if (active) {
            typingConvIds.add(convId);
            if (name.isNotEmpty) typingNames[convId] = name;
          } else {
            typingConvIds.remove(convId);
          }
          ping();
          return;
        }
        if (event.startsWith('job.')) {
          _applyJobEvent(event, payload);
          return;
        }
        if (event == 'shape_hint') {
          shapeHint = payload['text'] as String? ?? '建议写一条课程记忆';
          ping();
          return;
        }
        if (event == 'listen_invite') {
          _onListenInvite(payload);
          return;
        }
        if (event == 'listen_update') {
          ping();
          return;
        }
        if (event == 'device_progress') {
          final convId = payload['conv_id'] as String? ?? '';
          final text = payload['text'] as String? ?? payload['phase'] as String? ?? '';
          if (text.isNotEmpty) {
            deviceProgress[convId.isEmpty ? '_' : convId] = text;
            ping();
          }
          return;
        }
        if (event == 'turn_metrics') {
          final convId = payload['conv_id'] as String? ?? '';
          if (convId.isEmpty) return;
          turnMetrics[convId] = Map<String, dynamic>.from(payload);
          ping();
          return;
        }
        if (event == 'message') {
          final convId = payload['conv_id'] as String?;
          if (convId != null) typingConvIds.remove(convId);
          _upsertRemoteMessage(payload);
          ping();
        } else if (event == 'message_delta') {
          final id = payload['id'] as String?;
          final convId = payload['conv_id'] as String?;
          final delta = payload['delta'] as String? ?? '';
          if (id == null || convId == null || delta.isEmpty) return;
          typingConvIds.remove(convId);
          final list = msgs(convId);
          final i = list.indexWhere((m) => m.id == id);
          if (i < 0) return;
          list[i].text = '${list[i].text}$delta';
          list[i].state = SendState.sending;
          final c = conv(convId);
          if (c != null) {
            c.preview = list[i].text;
            c.time = DateTime.now();
          }
          ping();
        }
      },
      onConnectionChanged: (ok) {
        if (ok) {
          markServerOnline();
        } else {
          markServerOffline('实时连接已断开');
        }
      },
    )..connect();
    _startHealthProbe();
  }

  void _startHealthProbe() {
    _healthTimer?.cancel();
    if (demo || api == null) return;
    _healthTimer = Timer.periodic(const Duration(seconds: 8), (_) => probeServer());
    // 立刻探一次
    unawaited(probeServer());
  }

  Future<void> probeServer() async {
    if (demo || api == null) return;
    try {
      await api!.fetchInfo();
      markServerOnline();
    } catch (e) {
      markServerOffline('$e');
    }
  }

  void markServerOnline() {
    final wasOff = !serverOnline;
    if (!serverOnline || lastOfflineReason != null) {
      serverOnline = true;
      lastOfflineReason = null;
      ping();
    }
    if (wasOff && _locallyReadConvIds.isNotEmpty) {
      for (final id in _locallyReadConvIds.toList()) {
        unawaited(markConversationRead(id));
      }
    }
  }

  void markServerOffline(String reason) {
    final short = reason.length > 80 ? '${reason.substring(0, 80)}…' : reason;
    if (!serverOnline && lastOfflineReason == short) return;
    serverOnline = false;
    lastOfflineReason = short;
    ping();
  }

  void disposeRealtime() {
    _healthTimer?.cancel();
    _healthTimer = null;
    _realtime?.close();
    _realtime = null;
  }

  void _upsertRemoteMessage(Map<String, dynamic> raw, {String? replaceClientId}) {
    final m = _msgFromJson(raw);
    // 跳过空气泡（旧版服务端曾预插过）
    if (!m.mine && m.kind == MsgKind.text && m.text.isEmpty && m.state == SendState.sending) {
      return;
    }
    final list = msgs(m.convId);
    final clientId = raw['client_id'] as String? ?? replaceClientId;
    // 同 id 一律覆盖（一起听卡 ended、设备申请状态等依赖更新）
    final i = list.indexWhere((e) => e.id == m.id || (clientId != null && e.id == clientId));
    if (i >= 0) {
      // 保留本地「转文字」显隐，避免 sync/WS 覆盖后气泡文字又消失
      final prevVisible = list[i].meta?['asr_visible'] == true;
      final prevBytes = list[i].meta?['local_bytes'];
      if (prevVisible || prevBytes != null) {
        m.meta = {
          ...?m.meta,
          if (prevVisible) 'asr_visible': true,
          if (prevBytes != null) 'local_bytes': prevBytes,
        };
      }
      list[i] = m;
    } else {
      list.add(m);
    }
    list.sort((a, b) => a.time.compareTo(b.time));
    final c = conv(m.convId);
    if (c != null) {
      // 列表预览：一起听卡用 body
      final previewText = () {
        final card = m.meta?['card']?.toString();
        if (card == 'listen_invite' || card == 'voice_call') {
          return m.meta?['body']?.toString() ?? m.text;
        }
        return m.text;
      }();
      c.preview = previewText;
      c.time = m.time;
      final viewing = activeConvId != null && activeConvId == m.convId;
      if (!m.mine && viewing) {
        // 正在看这个会话：本地不涨未读，并尽快把服务端 unread 清掉
        scheduleMarkConversationRead(m.convId);
      } else if (!m.mine && !viewing) {
        c.unread += 1;
        final isListenCard = m.meta?['card']?.toString() == 'listen_invite';
        // 一起听由 listen_invite WS 弹专用条；普通跨会话消息才弹消息条
        if (activeConvId != null && !isListenCard) {
          final title = titleOf(c);
          final sub = previewText.length > 40 ? '${previewText.substring(0, 40)}…' : previewText;
          showTopToast(
            TopToast(
              id: 'msg_${m.id}',
              kind: TopToastKind.message,
              title: title,
              subtitle: sub,
              convId: m.convId,
              personaId: c.personaId,
            ),
          );
        }
      }
    }
  }

  void setActiveConv(String? convId) {
    if (activeConvId == convId) return;
    final prev = activeConvId;
    activeConvId = convId;
    // 离开会话时再打一次已读，避免在聊期间服务端累计的 unread 残留
    if (prev != null && prev != convId) {
      unawaited(markConversationRead(prev));
    }
    // 进入会话时清掉该会话的顶栏
    if (convId != null && topToast?.convId == convId && topToast?.kind == TopToastKind.message) {
      consumeTopToast();
    }
  }

  void showTopToast(TopToast toast, {Duration ttl = const Duration(seconds: 6)}) {
    _topToastTimer?.cancel();
    topToast = toast;
    ping();
    _topToastTimer = Timer(ttl, () {
      if (topToast?.id == toast.id) {
        topToast = null;
        ping();
      }
    });
  }

  TopToast? consumeTopToast() {
    _topToastTimer?.cancel();
    final t = topToast;
    topToast = null;
    ping();
    return t;
  }

  void onTopToastAction(TopToast toast) {
    onTopToastNavigate?.call(toast);
  }

  void onTopToastOpen(TopToast toast) {
    onTopToastNavigate?.call(toast);
  }

  void _onListenInvite(Map<String, dynamic> payload) {
    final sid = payload['session_id'] as String? ?? '';
    final convId = payload['conv_id'] as String? ?? '';
    final mood = payload['mood'] as String? ?? '一起听';
    final reason = payload['reason'] as String? ?? '';
    final count = payload['track_count'];
    final preview = payload['preview'] as String? ?? '';
    final personaId = payload['persona_id'] as String? ?? '';
    final name = personaId.isNotEmpty ? (persona(personaId)?.display ?? 'TA') : 'TA';
    final sub = [
      if (reason.isNotEmpty) reason else '想和你一起听「$mood」',
      if (count != null) '$count 首',
      if (preview.isNotEmpty) preview,
    ].join(' · ');
    showTopToast(
      TopToast(
        id: 'listen_$sid',
        kind: TopToastKind.listenInvite,
        title: '$name 邀请一起听',
        subtitle: sub,
        convId: convId.isEmpty ? null : convId,
        sessionId: sid.isEmpty ? null : sid,
        personaId: personaId.isEmpty ? null : personaId,
      ),
      ttl: const Duration(seconds: 12),
    );
  }

  /// 用人设 PATCH 返回值更新内存，并写入本地缓存，避免刷新又变回旧数据。
  Future<void> applyPersonaRemote(Map<String, dynamic> raw) async {
    final next = _personaFromJson(Map<String, dynamic>.from(raw));
    final i = personas.indexWhere((p) => p.id == next.id);
    if (i >= 0) {
      final old = personas[i];
      next.tagNames
        ..clear()
        ..addAll(old.tagNames);
      next.muted = old.muted;
      next.starred = old.starred;
      next.hideHisMoments = old.hideHisMoments;
      next.hideMyMoments = old.hideMyMoments;
      next.blacklisted = old.blacklisted;
      next.phone = old.phone;
      next.desc = old.desc;
      next.quietStart = old.quietStart;
      next.quietEnd = old.quietEnd;
      next.greetCap = old.greetCap;
      next.momentsCap = old.momentsCap;
      next.gapMinutes = old.gapMinutes;
      if ((raw['voice_reply_pref'] as String?) == null || (raw['voice_reply_pref'] as String).isEmpty) {
        next.voiceReplyPref = old.voiceReplyPref;
      }
      if (next.memories.isEmpty && old.memories.isNotEmpty) {
        next.memories.addAll(old.memories);
      }
      personas[i] = next;
    } else {
      personas.add(next);
    }
    ping();
    await _persistCache();
  }

  Future<void> _persistCache() async {
    if (demo || api == null) return;
    try {
      final cache = LocalSyncCache(serverUrl, meId);
      final cached = await cache.loadWithCursor();
      await cache.save(_snapshotForCache(), cursor: cached?.cursor ?? DateTime.now().toUtc().toIso8601String());
    } catch (_) {}
  }

  Persona _personaFromJson(Map<String, dynamic> j) {
    final colorInt = (j['color'] as num?)?.toInt() ?? 0xFF2F6BFF;
    final stateRaw = j['state'] as String? ?? 'friend';
    final state = switch (stateRaw) {
      'pendingIn' => FriendState.pendingIn,
      'pendingOut' => FriendState.pendingOut,
      _ => FriendState.friend,
    };
    final mems = <MemoryItem>[
      for (final m in (j['memories'] as List? ?? const []))
        MemoryItem((m as Map)['id'] as String? ?? _nid('m'), m['text'] as String? ?? ''),
    ];
    return Persona(
      id: j['id'] as String,
      name: j['name'] as String? ?? '',
      color: Color(colorInt),
      bio: j['bio'] as String? ?? '',
      channelId: (j['channel_id'] as String?)?.isNotEmpty == true ? j['channel_id'] as String : channels.first.id,
      state: state,
      isBot: j['is_bot'] as bool? ?? true,
      verify: j['verify'] as String? ?? '',
      remark: j['remark'] as String? ?? '',
      greet: j['greet'] as bool? ?? false,
      momentsOn: j['moments_on'] as bool? ?? false,
      avatarFileId: j['avatar_file_id'] as String? ?? '',
      voiceReplyPref: () {
        final v = (j['voice_reply_pref'] as String?)?.trim() ?? 'sometimes';
        return (v == 'never' || v == 'always' || v == 'sometimes') ? v : 'sometimes';
      }(),
      memories: mems,
    );
  }

  Conv _convFromJson(Map<String, dynamic> j) {
    final t = DateTime.tryParse(j['last_time'] as String? ?? '') ?? DateTime.now();
    final bgFileId = j['bg_file_id'] as String? ?? '';
    final bgName = j['bg_name'] as String? ?? (bgFileId.isEmpty ? '默认' : '相册图片');
    return Conv(
      id: j['id'] as String,
      personaId: j['persona_id'] as String?,
      groupId: j['group_id'] as String?,
      hidden: j['hidden'] == true || j['hidden'] == 1,
      preview: readableConvPreview(j['preview'] as String? ?? ''),
      time: t.toLocal(),
      unread: j['unread'] as int? ?? 0,
      pinned: j['pinned'] as bool? ?? false,
      muted: j['muted'] as bool? ?? false,
      draft: j['draft'] as String? ?? '',
      bgName: bgName,
      bgFileId: bgFileId,
    );
  }

  /// 本地缓存要能还原媒体：存可解析 JSON，而不是展示文案「[图片×n]」。
  String _persistMsgText(Msg m) {
    final meta = m.meta;
    if (meta == null) return m.text;
    if (m.kind == MsgKind.image || m.kind == MsgKind.video || m.kind == MsgKind.file || m.kind == MsgKind.voice) {
      final files = meta['files'];
      if (files is List && files.isNotEmpty) {
        return jsonEncode({
          'v': 1,
          'files': [for (final f in files) f.toString()],
          'caption': meta['caption']?.toString() ??
              ((m.text.startsWith('[') ? '' : m.text)),
          if (meta['seconds'] != null) 'seconds': meta['seconds'],
          if (meta['asr'] != null) 'asr': meta['asr'],
          if (meta['kind_extra'] != null) 'kind_extra': meta['kind_extra'],
          if (meta['sing'] != null) 'sing': meta['sing'],
        });
      }
    }
    if (m.kind == MsgKind.link && meta['card'] != null) {
      return jsonEncode(meta);
    }
    return m.text;
  }

  Msg _msgFromJson(Map<String, dynamic> j) {
    final kindRaw = j['kind'] as String? ?? 'text';
    final kind = switch (kindRaw) {
      'image' => MsgKind.image,
      'voice' => MsgKind.voice,
      'file' => MsgKind.file,
      'system' => MsgKind.system,
      'link' || 'location' || 'forward' => MsgKind.link,
      'video' => MsgKind.video,
      _ => MsgKind.text,
    };
    final stateRaw = j['state'] as String? ?? 'sent';
    final state = switch (stateRaw) {
      'sending' => SendState.sending,
      'failed' => SendState.failed,
      _ => SendState.sent,
    };
    final t = DateTime.tryParse(j['created_at'] as String? ?? '') ?? DateTime.now();
    var text = j['text'] as String? ?? '';
    Map<String, dynamic>? meta;
    // 本地缓存可能把解析后的 meta 单独存了，而 text 已是「[图片×n]」
    final incomingMeta = j['meta'];
    if (incomingMeta is Map) {
      meta = Map<String, dynamic>.from(incomingMeta);
    }
    if (kind == MsgKind.system) {
      try {
        final decoded = jsonDecode(text);
        if (decoded is Map && decoded['body'] != null) {
          meta = {'level': decoded['level']?.toString() ?? 'info'};
          text = decoded['body'].toString();
        }
      } catch (_) {}
    } else if (kind == MsgKind.image || kind == MsgKind.video || kind == MsgKind.file || kind == MsgKind.voice || kind == MsgKind.link) {
      try {
        final decoded = jsonDecode(text);
        if (decoded is Map) {
          final m = Map<String, dynamic>.from(decoded);
          final files = m['files'];
          if (files is List) {
            meta = {
              'files': [for (final f in files) f.toString()],
              'caption': m['caption']?.toString() ?? '',
              if (m['seconds'] != null) 'seconds': m['seconds'],
              if (m['asr'] != null) 'asr': m['asr']?.toString() ?? '',
              if (m['job_id'] != null) 'job_id': m['job_id'],
              if (m['url'] != null) 'url': m['url'],
              if (m['status'] != null) 'status': m['status'],
              if (m['kind_extra'] != null) 'kind_extra': m['kind_extra'],
              if (m['sing'] != null) 'sing': m['sing'],
            };
            final cap = meta['caption'] as String? ?? '';
            final isSing = m['kind_extra']?.toString() == 'sing';
            text = cap.isNotEmpty
                ? cap
                : (kind == MsgKind.image
                    ? '[图片×${files.length}]'
                    : kind == MsgKind.video
                        ? '[视频]'
                        : kind == MsgKind.voice
                            ? (isSing ? '唱歌' : '语音')
                            : '[文件]');
          } else if (m['card'] == 'listen_invite') {
            meta = {
              'card': 'listen_invite',
              'session_id': m['session_id']?.toString() ?? '',
              'mood': m['mood']?.toString() ?? '',
              'track_count': m['track_count'],
              'body': m['body']?.toString() ?? '',
              'status': m['status']?.toString() ?? '',
            };
            text = (m['body'] ?? '一起听').toString();
          } else if (m['card'] == 'voice_call') {
            final dur = m['duration_sec'];
            meta = {
              'card': 'voice_call',
              'media': m['media']?.toString() ?? 'audio',
              'status': m['status']?.toString() ?? '',
              'duration_sec': dur is num ? dur.toInt() : int.tryParse('$dur') ?? 0,
              'call_id': m['call_id']?.toString() ?? '',
              'body': m['body']?.toString() ?? '',
            };
            text = (m['body'] ?? '语音通话').toString();
          } else if (m['card'] == 'sticker_pack_added') {
            final covers = m['cover_file_ids'];
            meta = {
              'card': 'sticker_pack_added',
              'pack_id': m['pack_id']?.toString() ?? '',
              'title': m['title']?.toString() ?? '',
              'tags': m['tags']?.toString() ?? '',
              'scope': m['scope']?.toString() ?? '',
              'item_count': m['item_count'],
              'cover_file_ids': covers is List ? [for (final c in covers) c.toString()] : const <String>[],
              'subscribed': m['subscribed'] == true,
              'body': m['body']?.toString() ?? '',
            };
            text = (m['body'] ?? m['title'] ?? '表情包').toString();
          } else if (m['card'] == 'device_create_request') {
            meta = {
              'card': 'device_create_request',
              'request_id': m['request_id']?.toString() ?? '',
              'kind': m['kind']?.toString() ?? 'desktop',
              'label': m['label']?.toString() ?? '',
              'persona_id': m['persona_id']?.toString() ?? '',
              'reason': m['reason']?.toString() ?? '',
              'status': m['status']?.toString() ?? 'pending',
              'device_id': m['device_id']?.toString() ?? '',
              'body': m['body']?.toString() ?? '',
            };
            text = (m['body'] ?? '申请创建设备').toString();
          } else if (m['card'] == 'location' || kindRaw == 'location') {
            meta = {
              'card': 'location',
              'name': m['name']?.toString() ?? '位置',
              'address': m['address']?.toString() ?? '',
              'lat': m['lat'],
              'lng': m['lng'],
            };
            text = (m['name'] ?? '位置').toString();
          } else if (m['card'] == 'forward' || kindRaw == 'forward') {
            final items = m['items'] is List ? m['items'] as List : const [];
            meta = {
              'card': 'forward',
              'title': m['title']?.toString() ?? '聊天记录',
              'items': items,
              'count': m['count'] ?? items.length,
            };
            text = '${meta['title']} · ${meta['count']} 条';
          } else if (m['card'] == 'og' || (m['url'] != null && m['job_id'] == null && m['preview'] == null && kind == MsgKind.link)) {
            meta = {
              'card': 'og',
              'url': m['url']?.toString() ?? '',
              'title': m['title']?.toString() ?? '',
              'description': m['description']?.toString() ?? '',
              'image': m['image']?.toString() ?? '',
            };
            text = (m['title'] ?? m['url'] ?? text).toString();
          } else if (m['job_id'] != null || m['preview'] != null) {
            meta = {
              'job_id': m['job_id'],
              'url': m['url'],
              'status': m['status'] ?? 'queued',
              'title': m['title'] ?? m['preview'],
              'mode': m['action'] == 'probe' ? 'probe' : (m['capability'] == 'video.understand' ? 'video' : 'download'),
              if (m['then_understand'] == true) 'then_understand': true,
            };
            text = (m['preview'] ?? m['title'] ?? m['url'] ?? text).toString();
          }
        }
      } catch (_) {}
      // text 已是展示文案时，仍用缓存里的 meta.files 还原
      if (meta != null) {
        final files = meta['files'];
        if (files is List && files.isNotEmpty) {
          meta['files'] = [for (final f in files) f.toString()];
          if (text.isEmpty || text.startsWith('[')) {
            final cap = meta['caption']?.toString() ?? '';
            if (cap.isNotEmpty) {
              text = cap;
            } else if (kind == MsgKind.image) {
              text = '[图片×${files.length}]';
            }
          }
        }
      }
    }
    return Msg(
      id: j['id'] as String,
      convId: j['conv_id'] as String,
      mine: j['mine'] as bool? ?? false,
      senderId: j['sender_id'] as String? ?? '',
      kind: kind,
      text: text,
      time: t.toLocal(),
      state: state,
      quoteId: j['quote_id'] as String?,
      seconds: j['seconds'] as int? ?? 0,
      bytes: j['bytes'] as int? ?? 0,
      meta: meta,
    );
  }

  void sendKind(Conv c, MsgKind kind, String text, {int seconds = 0, int bytes = 0}) {
    final list = msgs(c.id);
    list.add(Msg(id: _nid('m'), convId: c.id, mine: true, senderId: meId, kind: kind, text: text, time: DateTime.now(), seconds: seconds, bytes: bytes, state: SendState.sent));
    list.add(Msg(id: _nid('b'), convId: c.id, mine: false, senderId: c.personaId ?? 'lin', kind: MsgKind.text, text: '收到一则${_kindName(kind)}。还没接到模型。', time: DateTime.now().add(const Duration(milliseconds: 200))));
    c.preview = text;
    c.time = DateTime.now();
    ping();
  }

  /// 语音：上传音频后发 voice 消息（media_ids + seconds）。
  Future<void> sendVoiceMessage(Conv c, {required List<int> bytes, required int seconds, String filename = 'voice.m4a'}) async {
    final clientId = const Uuid().v4();
    final optimistic = Msg(
      id: clientId,
      convId: c.id,
      mine: true,
      senderId: meId,
      kind: MsgKind.voice,
      text: '语音',
      time: DateTime.now(),
      state: SendState.sending,
      seconds: seconds,
      bytes: bytes.length,
      meta: {'local_path': true},
    );
    msgs(c.id).add(optimistic);
    c.preview = '[语音]';
    c.time = optimistic.time;
    c.draft = '';
    ping();

    if (demo || api == null) {
      optimistic.state = SendState.sent;
      ping();
      return;
    }
    try {
      final up = await api!.uploadChatMedia(
        bytes: Uint8List.fromList(bytes),
        filename: filename,
        kind: 'file',
        contentType: filename.endsWith('.wav') ? 'audio/wav' : 'audio/mp4',
      );
      final fid = up['file_id']?.toString();
      if (fid == null || fid.isEmpty) throw ApiException('上传未返回 file_id');
      final raw = await api!.sendMessage(
        convId: c.id,
        clientId: clientId,
        text: '',
        kind: 'voice',
        mediaIds: [fid],
        seconds: seconds,
        bytes: bytes.length,
      );
      final message = Map<String, dynamic>.from(raw['message'] as Map? ?? raw);
      _upsertRemoteMessage(message, replaceClientId: clientId);
      final list = msgs(c.id);
      final i = list.indexWhere((m) => m.id == clientId || m.id == (message['id'] as String?));
      if (i >= 0) {
        // 保留服务端 asr / caption，只补本地播放用字段
        list[i].meta = {
          ...?list[i].meta,
          'files': [fid],
          'local_bytes': bytes,
        };
      }
      ping();
    } catch (e) {
      optimistic.state = SendState.failed;
      ping();
      rethrow;
    }
  }

  /// 多图/视频/文件：上传后合并成一条媒体卡片消息。
  Future<void> sendMediaMessage(
    Conv c, {
    required List<({List<int> bytes, String name, String kind, String? contentType})> items,
    String caption = '',
  }) async {
    if (items.isEmpty) return;
    final primaryKind = items.every((e) => e.kind == 'image')
        ? MsgKind.image
        : items.any((e) => e.kind == 'video')
            ? MsgKind.video
            : MsgKind.file;
    final clientId = const Uuid().v4();
    final optimistic = Msg(
      id: clientId,
      convId: c.id,
      mine: true,
      senderId: meId,
      kind: primaryKind,
      text: caption.isEmpty ? (primaryKind == MsgKind.image ? '[图片]' : '[文件]') : caption,
      time: DateTime.now(),
      state: SendState.sending,
      bytes: items.fold<int>(0, (a, b) => a + b.bytes.length),
      meta: {
        'local_previews': [
          for (final it in items.where((e) => e.kind == 'image'))
            {'name': it.name, 'bytes': it.bytes},
        ],
        'count': items.length,
      },
    );
    msgs(c.id).add(optimistic);
    c.preview = optimistic.text;
    c.time = optimistic.time;
    ping();

    if (demo || api == null) {
      optimistic.state = SendState.sent;
      optimistic.meta = {
        ...?optimistic.meta,
        'demo': true,
      };
      msgs(c.id).add(Msg(
        id: _nid('b'),
        convId: c.id,
        mine: false,
        senderId: c.personaId ?? 'lin',
        kind: MsgKind.text,
        text: '收到${items.length}个媒体（演示）。',
        time: DateTime.now().add(const Duration(milliseconds: 200)),
      ));
      ping();
      return;
    }

    try {
      final ids = <String>[];
      var totalBytes = 0;
      for (final it in items) {
        var bytes = Uint8List.fromList(it.bytes);
        var filename = it.name;
        var ctype = (it.contentType ?? '').trim();
        if (it.kind == 'image') {
          // 发送前再压一道，兼容旧暂存/其它入口未压缩的大图
          final cimg = await compressImageForUpload(bytes, filename: filename);
          bytes = cimg.bytes;
          filename = cimg.filename;
          ctype = cimg.contentType;
        } else if (ctype.isEmpty) {
          ctype = 'application/octet-stream';
        }
        totalBytes += bytes.length;
        Map<String, dynamic> up;
        try {
          up = await api!.uploadChatMedia(
            bytes: bytes,
            filename: filename,
            kind: it.kind,
            contentType: ctype,
          );
        } on ApiException catch (e) {
          // 超时/网络：再压一档后重试一次，并确保连接已在 upload 内强制释放
          final retryable = e.statusCode == 408 ||
              e.message.contains('超时') ||
              e.message.contains('网络') ||
              e.message.contains('失败');
          if (!retryable || it.kind != 'image') rethrow;
          final harder = await compressImageForUpload(
            bytes,
            filename: filename,
            maxSide: 960,
            quality: 55,
            targetBytes: 280 * 1024,
            hardMaxBytes: 400 * 1024,
          );
          up = await api!.uploadChatMedia(
            bytes: harder.bytes,
            filename: harder.filename,
            kind: it.kind,
            contentType: harder.contentType,
            timeout: const Duration(seconds: 120),
          );
          bytes = harder.bytes;
        }
        final fid = up['file_id']?.toString();
        if (fid == null || fid.isEmpty) throw ApiException('上传未返回 file_id');
        ids.add(fid);
        // 上传后立刻进内存+磁盘缓存，退出再进也直接命中
        if (it.kind == 'image' && bytes.isNotEmpty) {
          AuthImageCache.put(fid, bytes, contentType: up['content_type']?.toString() ?? ctype);
        }
      }
      optimistic.bytes = totalBytes;
      final raw = await api!.sendMessage(
        convId: c.id,
        clientId: clientId,
        text: caption,
        kind: primaryKind.name,
        mediaIds: ids,
        bytes: totalBytes,
      );
      final message = Map<String, dynamic>.from(raw['message'] as Map? ?? raw);
      _upsertRemoteMessage(message, replaceClientId: clientId);
      final i = msgs(c.id).indexWhere((m) => m.id == clientId || m.id == message['id']);
      if (i >= 0) {
        final prevPreviews = msgs(c.id)[i].meta?['local_previews'];
        msgs(c.id)[i].meta = {
          ...?msgs(c.id)[i].meta,
          'files': ids,
          'auth_urls': [for (final id in ids) api!.mediaAuthUrl(id)],
          if (prevPreviews != null) 'local_previews': prevPreviews,
        };
        msgs(c.id)[i].state = SendState.sent;
      }
      unawaited(_persistCache());
      ping();
    } catch (e) {
      optimistic.state = SendState.failed;
      final msg = e is ApiException ? e.message : '$e';
      // 避免「发送失败：上传失败：…」叠两层
      optimistic.text = msg.startsWith('发送失败') ? msg : '发送失败：$msg';
      ping();
    }
  }

  String _kindName(MsgKind k) => switch (k) {
        MsgKind.image => '图片',
        MsgKind.voice => '语音',
        MsgKind.file => '文件',
        MsgKind.link => '链接',
        MsgKind.video => '视频',
        _ => '消息',
      };

  /// 发送已有媒体（表情收藏等），不再二次上传。
  Future<void> sendExistingMedia(
    Conv c, {
    required String fileId,
    String kind = 'image',
    String caption = '',
  }) async {
    if (fileId.isEmpty) return;
    final clientId = const Uuid().v4();
    final msgKind = kind == 'voice' ? MsgKind.voice : MsgKind.image;
    final optimistic = Msg(
      id: clientId,
      convId: c.id,
      mine: true,
      senderId: meId,
      kind: msgKind,
      text: caption.isEmpty ? (msgKind == MsgKind.voice ? '[语音]' : '[图片]') : caption,
      time: DateTime.now(),
      state: SendState.sending,
      meta: {
        'files': [fileId],
        if (api != null) 'auth_urls': [api!.mediaAuthUrl(fileId)],
      },
    );
    msgs(c.id).add(optimistic);
    c.preview = readableConvPreview(optimistic.text);
    c.time = optimistic.time;
    ping();
    if (demo || api == null) {
      optimistic.state = SendState.sent;
      ping();
      return;
    }
    try {
      final raw = await api!.sendMessage(
        convId: c.id,
        clientId: clientId,
        text: caption,
        kind: msgKind.name,
        mediaIds: [fileId],
      );
      final message = Map<String, dynamic>.from(raw['message'] as Map? ?? raw);
      _upsertRemoteMessage(message, replaceClientId: clientId);
      ping();
    } catch (e) {
      optimistic.state = SendState.failed;
      optimistic.text = '发送失败：$e';
      ping();
    }
  }

  /// job_id → 本地消息 id，用于 WebSocket 进度回写。
  final Map<String, String> jobMsgIds = {};

  Future<Msg?> submitLinkJob({
    required Conv c,
    required String url,
    required String mode, // download | video | probe
    bool thenUnderstand = false,
  }) async {
    if (demo || api == null) return null;
    final probing = mode == 'probe';
    final clientId = const Uuid().v4();
    final meta = <String, dynamic>{
      'url': url,
      'status': 'queued',
      'progress': 0,
      'mode': mode,
      if (thenUnderstand) 'then_understand': true,
    };
    final local = Msg(
      id: clientId,
      convId: c.id,
      mine: true,
      senderId: meId,
      kind: MsgKind.link,
      text: probing ? '解析中…' : (mode == 'video' ? '视频理解中…' : '下载中…'),
      time: DateTime.now(),
      state: SendState.sending,
      meta: meta,
    );
    msgs(c.id).add(local);
    c.preview = local.text;
    c.time = DateTime.now();
    ping();
    try {
      final job = mode == 'video'
          ? await api!.createJob(
              capability: 'video.understand',
              pluginId: 'video-mage',
              input: {'url': url, 'prompt': '简要描述视频内容'},
              timeoutSeconds: 600,
              conversationId: c.id,
              personaId: c.personaId ?? '',
              clientMsgId: clientId,
            )
          : await api!.createJob(
              capability: 'media.download',
              pluginId: 'download',
              input: {
                'action': probing ? 'probe' : 'download',
                'url': url,
                if (thenUnderstand) 'then_understand': true,
              },
              needUpload: !probing,
              timeoutSeconds: 600,
              conversationId: c.id,
              personaId: c.personaId ?? '',
              clientMsgId: clientId,
            );
      final jid = job['id']?.toString() ?? '';
      final serverMsgId = job['message_id']?.toString() ?? '';
      meta['job_id'] = jid;
      meta['status'] = job['status'] ?? 'running';
      local.state = SendState.sending;
      Msg tracked = local;
      if (serverMsgId.isNotEmpty && serverMsgId != clientId) {
        final list = msgs(c.id);
        list.removeWhere((m) => m.id == clientId);
        tracked = Msg(
          id: serverMsgId,
          convId: c.id,
          mine: true,
          senderId: meId,
          kind: MsgKind.link,
          text: local.text,
          time: local.time,
          state: SendState.sending,
          meta: meta,
        );
        list.add(tracked);
      }
      if (jid.isNotEmpty) {
        jobMsgIds[jid] = tracked.id;
        unawaited(_pollJob(jid));
      }
      ping();
      return tracked;
    } catch (e) {
      meta['status'] = 'failed';
      meta['error'] = e.toString();
      local.text = '失败：$e';
      local.state = SendState.failed;
      ping();
      return local;
    }
  }

  Future<void> _pollJob(String jobId) async {
    final api = this.api;
    if (api == null) return;
    for (var i = 0; i < 120; i++) {
      await Future<void>.delayed(const Duration(seconds: 2));
      if (!jobMsgIds.containsKey(jobId)) return;
      try {
        final j = await api.getJob(jobId);
        final status = j['status']?.toString() ?? '';
        final event = status == 'done'
            ? 'job.done'
            : status == 'failed'
                ? 'job.failed'
                : status == 'cancelled'
                    ? 'job.cancelled'
                    : 'job.progress';
        _applyJobEvent(event, {
          'job_id': jobId,
          ...j,
        });
        if (status == 'done' || status == 'failed' || status == 'cancelled' || status == 'interrupted') {
          return;
        }
      } catch (_) {
        /* keep polling */
      }
    }
  }

  void _applyJobEvent(String event, Map<String, dynamic> payload) {
    final jid = payload['job_id']?.toString() ?? payload['id']?.toString() ?? '';
    if (jid.isEmpty) return;
    final mid = jobMsgIds[jid];
    if (mid == null) return;
    Msg? msg;
    for (final list in messages.values) {
      final i = list.indexWhere((m) => m.id == mid);
      if (i >= 0) {
        msg = list[i];
        break;
      }
    }
    if (msg == null) return;
    final meta = Map<String, dynamic>.from(msg.meta ?? {});
    final status = payload['status']?.toString() ?? meta['status'];
    meta['status'] = status;
    meta['progress'] = payload['progress_pct'] ?? meta['progress'] ?? 0;
    meta['error'] = payload['error'] ?? meta['error'];
    final result = payload['result'];
    if (result is Map) {
      meta.addAll(Map<String, dynamic>.from(result));
    }
    msg.meta = meta;
    if (event == 'job.progress' || event == 'job.running') {
      final pct = meta['progress'] ?? 0;
      msg.text = '${meta['title'] ?? meta['url'] ?? '任务'} · $pct%';
      msg.state = SendState.sending;
    } else if (event == 'job.done') {
      final title = meta['title']?.toString() ?? meta['url']?.toString() ?? '完成';
      if (meta['file_id'] != null || meta['mode'] == 'download') {
        msg.kind = MsgKind.video;
        msg.text = title;
        msg.seconds = (meta['duration'] is num) ? (meta['duration'] as num).round() : 0;
        msg.bytes = (meta['size'] is num) ? (meta['size'] as num).round() : 0;
      } else if (meta['mode'] == 'probe') {
        msg.kind = MsgKind.link;
        msg.text = meta['title']?.toString() ?? title;
      } else {
        msg.text = title;
      }
      msg.state = SendState.sent;
      jobMsgIds.remove(jid);
      // 接力理解改由网关编排；若服务端未挂上 chained_job_id，App 仍兜底一次
      if (meta['then_understand'] == true &&
          meta['mode'] == 'download' &&
          (meta['chained_job_id'] == null || meta['chained_job_id'].toString().isEmpty)) {
        final url = meta['url']?.toString() ?? '';
        final c = conv(msg.convId);
        if (c != null && url.isNotEmpty) {
          unawaited(submitLinkJob(c: c, url: url, mode: 'video'));
        }
      }
    } else if (event == 'job.failed' || event == 'job.cancelled') {
      msg.text = meta['error']?.toString() ?? '任务失败';
      msg.state = SendState.failed;
      jobMsgIds.remove(jid);
    }
    final c = conv(msg.convId);
    if (c != null) {
      c.preview = msg.text;
      c.time = DateTime.now();
    }
    ping();
  }

  void failLastMine(Conv c) {
    final list = msgs(c.id).reversed.where((m) => m.mine && m.kind != MsgKind.system);
    if (list.isEmpty) return;
    list.first.state = SendState.failed;
    ping();
  }

  void retry(Msg m) {
    m.state = SendState.sent;
    ping();
  }

  /// 微信式：长按「转文字 / 隐藏文字」只切换气泡下方转写显隐，不发新消息。
  void toggleVoiceAsrVisible(Msg m) {
    if (m.kind != MsgKind.voice) return;
    final cur = m.meta?['asr_visible'] == true;
    m.meta = {
      ...?m.meta,
      'asr_visible': !cur,
    };
    ping();
  }

  void revoke(Msg m) {
    if (!m.mine) return;
    m.backup = m.text;
    m.kind = MsgKind.system;
    m.text = '你撤回了一条消息';
    ping();
  }

  void deleteMsgs(Conv c, Set<String> ids) {
    msgs(c.id).removeWhere((m) => ids.contains(m.id));
    ping();
  }

  void clearConv(Conv c) {
    msgs(c.id).clear();
    c.preview = '';
    c.unread = 0;
    ping();
  }

  Future<void> postMoment(
    String text,
    int images, {
    bool private = false,
    MomentVisibility visibility = MomentVisibility.open,
    String? location,
    List<String>? mentionIds,
    List<String>? audienceIds,
    List<String> mediaIds = const [],
  }) async {
    if (!demo && api != null) {
      final raw = await api!.createMoment(text: text, mediaIds: mediaIds);
      moments.insert(0, _momentFromJson(raw));
      ping();
      return;
    }
    final vis = private ? MomentVisibility.privateOnly : visibility;
    moments.insert(
      0,
      Moment(
        id: _nid('mo'),
        authorId: meId,
        text: text,
        time: DateTime.now(),
        images: mediaIds.isNotEmpty ? mediaIds.length : images,
        mediaIds: List<String>.from(mediaIds),
        private: vis == MomentVisibility.privateOnly,
        visibility: vis,
        location: location,
        mentionIds: mentionIds,
        audienceIds: audienceIds,
      ),
    );
    for (final id in mentionIds ?? const <String>[]) {
      final name = nameOfSender(id);
      notices.insert(0, MomentNotice(_nid('n'), moments.first.id, name, '提醒你看一条朋友圈', DateTime.now()));
    }
    ping();
  }

  /// 保存聊天背景到服务器（色块名或相册图 file_id）。
  Future<void> saveConversationBackground(Conv c, {required String bgName, String bgFileId = '', Uint8List? previewBytes}) async {
    c.bgName = bgName;
    c.bgFileId = bgFileId;
    c.bgImageBytes = previewBytes;
    ping();
    if (demo || api == null) return;
    final raw = await api!.patchConversation(c.id, bgName: bgName, bgFileId: bgFileId);
    final synced = _convFromJson(Map<String, dynamic>.from(raw));
    c.bgName = synced.bgName;
    c.bgFileId = synced.bgFileId;
    if (previewBytes != null) c.bgImageBytes = previewBytes;
    ping();
    await _persistCache();
  }

  Future<void> saveMyProfile({String? displayName, String? avatarFileId, String? signature, String? momentsCoverFileId}) async {
    if (demo || api == null) {
      if (displayName != null) meName = displayName;
      if (avatarFileId != null) meAvatarFileId = avatarFileId;
      if (signature != null) meSignature = signature;
      if (momentsCoverFileId != null) meCoverFileId = momentsCoverFileId;
      ping();
      return;
    }
    final me = await api!.patchMe(
      displayName: displayName,
      avatarFileId: avatarFileId,
      signature: signature,
      momentsCoverFileId: momentsCoverFileId,
    );
    meName = me['display_name'] as String? ?? meName;
    meAvatarFileId = me['avatar_file_id'] as String? ?? meAvatarFileId;
    meSignature = me['signature'] as String? ?? meSignature;
    meCoverFileId = me['moments_cover_file_id'] as String? ?? meCoverFileId;
    ping();
    await _persistCache();
  }

  Future<void> toggleLike(Moment m) async {
    if (!demo && api != null) {
      try {
        final raw = await api!.toggleMomentLike(m.id);
        final liked = raw['liked'] == true;
        m.likes.removeWhere((n) => n == meName);
        if (liked) m.likes.add(meName);
        ping();
      } catch (_) {}
      return;
    }
    if (m.likes.contains(meName)) {
      m.likes.remove(meName);
    } else {
      m.likes.add(meName);
    }
    ping();
  }

  Future<void> addComment(Moment m, String text, {String? replyTo}) async {
    if (!demo && api != null) {
      try {
        final raw = await api!.addMomentComment(m.id, text: text, replyTo: replyTo);
        m.comments.add(
          MomentComment(
            nameOfSender(raw['actor_id'] as String? ?? meId),
            raw['text'] as String? ?? text,
            replyTo: raw['reply_to'] as String?,
          ),
        );
        ping();
      } catch (_) {}
      return;
    }
    m.comments.add(MomentComment(meName, text, replyTo: replyTo));
    ping();
  }

  Future<void> deleteMoment(Moment m) async {
    if (!demo && api != null) {
      try {
        await api!.deleteMoment(m.id);
        moments.removeWhere((x) => x.id == m.id);
        ping();
      } catch (_) {}
      return;
    }
    moments.remove(m);
    ping();
  }

  void markNoticesRead() {
    for (final n in notices) {
      n.read = true;
    }
    ping();
  }

  void createGroup(String name, List<String> memberIds) {
    final ids = {...memberIds, meId}.toList();
    final g = Group(_nid('g'), name, ids, ownerId: meId);
    groups.add(g);
    final c = Conv(id: 'c-${g.id}', groupId: g.id, preview: '你创建了群聊', time: DateTime.now());
    conversations.insert(0, c);
    messages[c.id] = [
      Msg(
        id: _nid('sys'),
        convId: c.id,
        mine: false,
        senderId: 'system',
        kind: MsgKind.system,
        text: '你邀请${memberIds.map(nameOfSender).join('、')}加入了群聊',
        time: DateTime.now(),
      ),
    ];
    ping();
  }

  /// 从单聊「+」发起群聊。
  Conv startGroupWith(List<String> otherIds) {
    final name = otherIds.map(nameOfSender).take(3).join('、');
    createGroup(name, otherIds);
    return conversations.first;
  }

  void addGroupMembers(Group g, List<String> ids) {
    for (final id in ids) {
      if (!g.memberIds.contains(id)) g.memberIds.add(id);
    }
    final c = conversations.where((e) => e.groupId == g.id).firstOrNull;
    if (c != null) {
      final text = '你邀请${ids.map(nameOfSender).join('、')}加入了群聊';
      msgs(c.id).add(Msg(id: _nid('sys'), convId: c.id, mine: false, senderId: 'system', kind: MsgKind.system, text: text, time: DateTime.now()));
      c.preview = text;
      c.time = DateTime.now();
    }
    ping();
  }

  void removeGroupMember(Group g, String id) {
    if (id == g.ownerId) return;
    g.memberIds.remove(id);
    g.watchIds.remove(id);
    final c = conversations.where((e) => e.groupId == g.id).firstOrNull;
    if (c != null) {
      final text = '你将${nameOfSender(id)}移出了群聊';
      msgs(c.id).add(Msg(id: _nid('sys'), convId: c.id, mine: false, senderId: 'system', kind: MsgKind.system, text: text, time: DateTime.now()));
      c.preview = text;
      c.time = DateTime.now();
    }
    ping();
  }

  void leaveGroup(Conv c) {
    final g = c.groupId == null ? null : group(c.groupId!);
    if (g != null) {
      g.memberIds.remove(meId);
      if (g.ownerId == meId && g.memberIds.isNotEmpty) {
        g.ownerId = g.memberIds.first;
      }
    }
    conversations.remove(c);
    messages.remove(c.id);
    ping();
  }

  void deleteContact(Persona p, {bool clearHistory = false}) {
    final c = conversations.where((e) => e.personaId == p.id).firstOrNull;
    if (clearHistory && c != null) {
      conversations.remove(c);
      messages.remove(c.id);
    }
    personas.remove(p);
    ping();
    if (!demo && api != null) {
      unawaited(() async {
        try {
          await api!.deletePersona(p.id);
        } catch (_) {}
      }());
    }
  }

  /// 微信式删除聊天：本地+云端都消失；人设仍在通讯录。
  Future<void> deleteConversation(Conv c) async {
    deletedConvIds.add(c.id);
    conversations.removeWhere((e) => e.id == c.id);
    messages.remove(c.id);
    ping();
    final cache = LocalSyncCache(serverUrl, meId);
    // 先落盘黑名单+缓存，再打云端，避免杀进程后 sync 把会话加回来
    try {
      await cache.saveDeletedConvIds(deletedConvIds);
      await cache.save(_snapshotForCache(), cursor: (await cache.loadWithCursor())?.cursor);
    } catch (_) {}
    if (demo || api == null) return;
    try {
      await api!.deleteConversation(c.id);
      // 再写一次：确保 cursor 之后的全量 sync 也不会带回
      try {
        await cache.saveDeletedConvIds(deletedConvIds);
        await cache.save(_snapshotForCache(), cursor: (await cache.loadWithCursor())?.cursor);
      } catch (_) {}
    } catch (e) {
      // 云端失败也保持本地删除，避免「看起来删了重启又回来」
      rethrow;
    }
  }

  Future<void> hideConversation(Conv c) async {
    c.hidden = true;
    deletedConvIds.add(c.id);
    conversations.removeWhere((e) => e.id == c.id);
    ping();
    final cache = LocalSyncCache(serverUrl, meId);
    try {
      await cache.saveDeletedConvIds(deletedConvIds);
      await cache.save(_snapshotForCache(), cursor: (await cache.loadWithCursor())?.cursor);
    } catch (_) {}
    if (demo || api == null) return;
    try {
      await api!.patchConversation(c.id, hidden: true);
    } catch (_) {}
  }
}

/// 收藏表情：远端 file_id 优先；无则演示色块。
class Sticker {
  Sticker(
    this.id,
    this.color, {
    this.label = '表情',
    this.fileId = '',
    this.emotion = '',
    this.packId = '',
  });
  final String id;
  final Color color;
  final String label;
  final String fileId;
  final String emotion;
  final String packId;

  factory Sticker.fromApi(Map<String, dynamic> m) {
    final id = m['id'] as String? ?? 'stk';
    final emotion = (m['emotion'] as String?)?.trim() ?? '';
    final label = (m['label'] as String?)?.trim().isNotEmpty == true
        ? m['label'] as String
        : (emotion.isNotEmpty ? emotion : '表情');
    final fileId = m['file_id'] as String? ?? '';
    final packId = m['pack_id'] as String? ?? '';
    final hue = (id.hashCode.abs() % 360).toDouble();
    return Sticker(
      id,
      HSVColor.fromAHSV(1, hue, 0.35, 0.92).toColor(),
      label: label,
      fileId: fileId,
      emotion: emotion,
      packId: packId,
    );
  }
}

class StickerPack {
  StickerPack({
    required this.id,
    required this.title,
    this.scope = 'account',
    this.tags = '',
    this.itemCount = 0,
    this.coverFileIds = const [],
    this.source = 'user',
  });
  final String id;
  final String title;
  final String scope;
  final String tags;
  final int itemCount;
  final List<String> coverFileIds;
  final String source;

  factory StickerPack.fromApi(Map<String, dynamic> m) {
    final covers = m['cover_file_ids'];
    return StickerPack(
      id: m['id']?.toString() ?? '',
      title: (m['title']?.toString() ?? '').trim().isNotEmpty ? m['title'].toString() : '表情组',
      scope: m['scope']?.toString() ?? 'account',
      tags: m['tags']?.toString() ?? '',
      itemCount: (m['item_count'] is num) ? (m['item_count'] as num).toInt() : 0,
      coverFileIds: covers is List ? [for (final c in covers) c.toString()] : const [],
      source: m['source']?.toString() ?? 'user',
    );
  }
}

