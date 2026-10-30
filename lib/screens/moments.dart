import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../format.dart';
import '../media/auth_image.dart';
import '../media/media_pick.dart';
import '../models.dart';
import '../store.dart';
import '../theme.dart';
import '../widgets.dart';
import 'persona.dart';

class MomentsPage extends StatefulWidget {
  const MomentsPage({super.key, required this.store});
  final AppStore store;
  @override
  State<MomentsPage> createState() => _MomentsPageState();
}

class _MomentsPageState extends State<MomentsPage> {
  String? openActionId;
  var _coverBusy = false;
  /// 点封面后显示「换封面」，不改变封面高度（对齐微信，无拉伸动画）。
  var _editingCover = false;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      widget.store.loadMoments();
    });
  }

  Future<void> _changeCover() async {
    if (_coverBusy) return;
    final store = widget.store;
    if (!store.demo && !store.serverOnline) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('本地服务已断开，无法上传。请先恢复 API 再试')),
        );
      }
      return;
    }
    final shots = await MediaPick.pickImagesFromGallery(already: 0);
    if (shots.isEmpty || !mounted) return;
    final bytes = shots.first.bytes;
    if (bytes == null || bytes.isEmpty) return;
    setState(() => _coverBusy = true);
    try {
      var fileId = '';
      if (!store.demo && store.api != null) {
        final up = await store.api!.uploadChatMedia(
          bytes: bytes,
          filename: shots.first.name,
          kind: 'image',
        );
        fileId = up['file_id']?.toString() ?? '';
        if (fileId.isEmpty) throw Exception('上传未返回文件 id');
        AuthImageCache.put(fileId, bytes);
      }
      await store.saveMyProfile(momentsCoverFileId: fileId);
      store.coverHint = '点封面可更换';
      store.markServerOnline();
      if (mounted) {
        setState(() => _editingCover = false);
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('朋友圈封面已更新'), duration: Duration(seconds: 1)),
        );
      }
    } catch (e) {
      final msg = '$e';
      if (msg.contains('本地服务已断开') || msg.contains('Connection refused')) {
        store.markServerOffline(msg);
      }
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('更换封面失败：$e')));
      }
    } finally {
      if (mounted) setState(() => _coverBusy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final store = widget.store;

    return ListenableBuilder(
      listenable: store,
      builder: (context, _) {
        // 不看他朋友圈 / 他人私密动态过滤
        final feed = store.moments.where((m) {
          if (m.authorId == store.meId) return true;
          final p = store.persona(m.authorId);
          if (p?.hideHisMoments == true) return false;
          if (m.visibility == MomentVisibility.privateOnly) return false;
          return true;
        }).toList();

        return Scaffold(
          backgroundColor: Colors.white,
          body: GestureDetector(
            onTap: () => setState(() {
              openActionId = null;
              _editingCover = false;
            }),
            behavior: HitTestBehavior.translucent,
            child: CustomScrollView(
              slivers: [
                SliverAppBar(
                  pinned: true,
                  backgroundColor: Colors.white,
                  title: const Text('朋友圈'),
                  actions: [
                    IconButton(
                      tooltip: '互动消息',
                      onPressed: () => Navigator.push(context, MaterialPageRoute(builder: (_) => MomentNoticesPage(store: store))),
                      icon: Badge(
                        isLabelVisible: store.noticeUnread > 0,
                        smallSize: 8,
                        child: const Icon(Icons.notifications_none_outlined),
                      ),
                    ),
                    GestureDetector(
                      onLongPress: () => Navigator.push(context, MaterialPageRoute(builder: (_) => ComposeMomentPage(store: store, textOnly: true))),
                      child: IconButton(
                        onPressed: () => Navigator.push(context, MaterialPageRoute(builder: (_) => ComposeMomentPage(store: store))),
                        icon: const Icon(Icons.camera_alt_outlined),
                      ),
                    ),
                  ],
                ),
                SliverToBoxAdapter(
                  child: _MomentsCoverBlock(
                    store: store,
                    coverFileId: store.meCoverFileId,
                    name: store.meName,
                    avatarFileId: store.meAvatarFileId,
                    color: const Color(0xFF4C8DFF),
                    editing: _editingCover,
                    busy: _coverBusy,
                    onTapCover: () => setState(() => _editingCover = !_editingCover),
                    onLongPressCover: _changeCover,
                    onChangeCover: _changeCover,
                    onTapAvatar: () => Navigator.push(
                      context,
                      MaterialPageRoute(builder: (_) => UserMomentsPage(store: store, authorId: store.meId)),
                    ),
                    normalRatio: 0.32,
                    editingRatio: 0.72,
                  ),
                ),
                if (store.meSignature.isNotEmpty)
                  SliverToBoxAdapter(
                    child: Align(
                      alignment: Alignment.centerRight,
                      child: Padding(
                        padding: const EdgeInsets.fromLTRB(16, 6, 18, 0),
                        child: ConstrainedBox(
                          constraints: const BoxConstraints(maxWidth: 220),
                          child: Text(
                            store.meSignature,
                            textAlign: TextAlign.right,
                            style: const TextStyle(color: AppColors.sub, fontSize: 13, height: 1.3),
                          ),
                        ),
                      ),
                    ),
                  ),
                if (store.noticeUnread > 0)
                  SliverToBoxAdapter(
                    child: Padding(
                      padding: const EdgeInsets.fromLTRB(16, 8, 16, 4),
                      child: Center(
                        child: InkWell(
                          onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => MomentNoticesPage(store: store))),
                          borderRadius: BorderRadius.circular(4),
                          child: Container(
                            padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
                            decoration: BoxDecoration(color: const Color(0xFF4C4C4C), borderRadius: BorderRadius.circular(4)),
                            child: Text('${store.noticeUnread}条新消息', style: const TextStyle(color: Colors.white, fontSize: 14)),
                          ),
                        ),
                      ),
                    ),
                  )
                else
                  const SliverToBoxAdapter(child: SizedBox(height: 12)),
                SliverList(
                  delegate: SliverChildBuilderDelegate(
                    (context, i) => _MomentTile(
                      store: store,
                      moment: feed[i],
                      actionOpen: openActionId == feed[i].id,
                      onToggleAction: () => setState(() => openActionId = openActionId == feed[i].id ? null : feed[i].id),
                      onCloseAction: () => setState(() => openActionId = null),
                    ),
                    childCount: feed.length,
                  ),
                ),
                const SliverToBoxAdapter(child: SizedBox(height: 48)),
              ],
            ),
          ),
        );
      },
    );
  }
}

class _MomentTile extends StatefulWidget {
  const _MomentTile({
    required this.store,
    required this.moment,
    required this.actionOpen,
    required this.onToggleAction,
    required this.onCloseAction,
  });
  final AppStore store;
  final Moment moment;
  final bool actionOpen;
  final VoidCallback onToggleAction;
  final VoidCallback onCloseAction;
  @override
  State<_MomentTile> createState() => _MomentTileState();
}

class _MomentTileState extends State<_MomentTile> {
  bool expanded = false;

  @override
  Widget build(BuildContext context) {
    final store = widget.store;
    final m = widget.moment;
    final author = m.authorId == store.meId ? store.meName : (store.persona(m.authorId)?.display ?? m.authorId);
    final color = m.authorId == store.meId ? const Color(0xFF4C8DFF) : (store.persona(m.authorId)?.color ?? Colors.grey);
    final long = m.text.characters.length > 90;
    final liked = m.likes.contains(store.meName);

    return Padding(
      padding: const EdgeInsets.fromLTRB(14, 14, 14, 0),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          GestureDetector(
            onTap: () {
              if (m.authorId == store.meId) {
                Navigator.push(context, MaterialPageRoute(builder: (_) => UserMomentsPage(store: store, authorId: m.authorId)));
                return;
              }
              final p = store.persona(m.authorId);
              if (p != null) Navigator.push(context, MaterialPageRoute(builder: (_) => PersonaPage(store: store, persona: p)));
            },
            child: Avatar(
              author,
              color,
              size: 42,
              api: store.api,
              avatarFileId: m.authorId == store.meId
                  ? (store.meAvatarFileId.isEmpty ? null : store.meAvatarFileId)
                  : (store.persona(m.authorId)?.avatarFileId.isEmpty == false
                      ? store.persona(m.authorId)!.avatarFileId
                      : null),
            ),
          ),
          const SizedBox(width: 10),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(author, style: const TextStyle(color: AppColors.name, fontSize: 16, fontWeight: FontWeight.w600, height: 1.2)),
                if (m.text.isNotEmpty) ...[
                  const SizedBox(height: 5),
                  Text(
                    long && !expanded ? '${m.text.characters.take(90)}…' : m.text,
                    style: const TextStyle(fontSize: 16, height: 1.4, color: Color(0xFF111111)),
                  ),
                  if (long)
                    GestureDetector(
                      onTap: () => setState(() => expanded = !expanded),
                      child: Padding(
                        padding: const EdgeInsets.only(top: 2),
                        child: Text(expanded ? '收起' : '全文', style: const TextStyle(color: AppColors.name, fontSize: 15)),
                      ),
                    ),
                ],
                if (m.mediaIds.isNotEmpty && store.api != null)
                  Padding(
                    padding: const EdgeInsets.only(top: 8),
                    child: _AuthImageGrid(api: store.api!, fileIds: m.mediaIds),
                  )
                else if (m.images > 0)
                  Padding(padding: const EdgeInsets.only(top: 8), child: _ImageGrid(count: m.images, color: color)),
                if (m.location != null && m.location!.isNotEmpty)
                  Padding(
                    padding: const EdgeInsets.only(top: 6),
                    child: Text(m.location!, style: const TextStyle(fontSize: 13, color: AppColors.name)),
                  ),
                if (m.visibility != MomentVisibility.open)
                  Padding(
                    padding: const EdgeInsets.only(top: 4),
                    child: Text(m.visibilityLabel, style: const TextStyle(fontSize: 12, color: AppColors.muted)),
                  ),
                const SizedBox(height: 8),
                // 时间行 + 微信式横向赞评条
                Row(
                  children: [
                    Text(formatMomentTime(m.time), style: const TextStyle(color: AppColors.muted, fontSize: 13)),
                    if (m.authorId == store.meId) ...[
                      const SizedBox(width: 12),
                      GestureDetector(
                        onTap: () => store.deleteMoment(m),
                        child: const Text('删除', style: TextStyle(fontSize: 13, color: AppColors.name)),
                      ),
                    ],
                    const Spacer(),
                    _WxActionBar(
                      open: widget.actionOpen,
                      liked: liked,
                      onToggle: widget.onToggleAction,
                      onLike: () {
                        store.toggleLike(m);
                        widget.onCloseAction();
                      },
                      onComment: () {
                        widget.onCloseAction();
                        _comment(m);
                      },
                    ),
                  ],
                ),
                if (m.likes.isNotEmpty || m.comments.isNotEmpty)
                  Container(
                    width: double.infinity,
                    margin: const EdgeInsets.only(top: 8, bottom: 4),
                    padding: const EdgeInsets.fromLTRB(8, 6, 8, 6),
                    decoration: BoxDecoration(
                      color: const Color(0xFFF3F3F5),
                      borderRadius: BorderRadius.circular(2),
                    ),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        if (m.likes.isNotEmpty)
                          Text.rich(
                            TextSpan(children: [
                              const WidgetSpan(
                                alignment: PlaceholderAlignment.middle,
                                child: Padding(
                                  padding: EdgeInsets.only(right: 4),
                                  child: Icon(Icons.favorite, size: 13, color: AppColors.name),
                                ),
                              ),
                              TextSpan(text: m.likes.join('，'), style: const TextStyle(color: AppColors.name, fontSize: 14, height: 1.35)),
                            ]),
                          ),
                        if (m.likes.isNotEmpty && m.comments.isNotEmpty)
                          const Padding(
                            padding: EdgeInsets.symmetric(vertical: 4),
                            child: Divider(height: 1, thickness: 0.5, color: Color(0xFFE0E0E0)),
                          ),
                        for (final c in m.comments)
                          Padding(
                            padding: const EdgeInsets.only(bottom: 2),
                            child: GestureDetector(
                              onTap: () => _comment(m, replyTo: c.author),
                              child: Text.rich(
                                TextSpan(children: [
                                  TextSpan(text: c.author, style: const TextStyle(color: AppColors.name, fontSize: 14, fontWeight: FontWeight.w500)),
                                  if (c.replyTo != null) ...[
                                    const TextSpan(text: '回复', style: TextStyle(fontSize: 14, color: Color(0xFF111111))),
                                    TextSpan(text: c.replyTo, style: const TextStyle(color: AppColors.name, fontSize: 14, fontWeight: FontWeight.w500)),
                                  ],
                                  TextSpan(text: '：${c.text}', style: const TextStyle(fontSize: 14, color: Color(0xFF111111), height: 1.35)),
                                ]),
                              ),
                            ),
                          ),
                      ],
                    ),
                  ),
                const SizedBox(height: 10),
                const Divider(height: 1, thickness: 0.5, color: Color(0xFFEEEEEE)),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Future<void> _comment(Moment m, {String? replyTo}) async {
    final values = await showFieldsDialog(context, title: replyTo == null ? '评论' : '回复 $replyTo', labels: const ['内容'], confirm: '发送');
    if (values == null || values.first.isEmpty) return;
    widget.store.addComment(m, values.first, replyTo: replyTo);
  }
}

/// 微信：点「··」向左弹出深色横条「赞 | 评论」。
class _WxActionBar extends StatelessWidget {
  const _WxActionBar({
    required this.open,
    required this.liked,
    required this.onToggle,
    required this.onLike,
    required this.onComment,
  });
  final bool open;
  final bool liked;
  final VoidCallback onToggle;
  final VoidCallback onLike;
  final VoidCallback onComment;

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        AnimatedSize(
          duration: const Duration(milliseconds: 160),
          curve: Curves.easeOut,
          child: open
              ? Container(
                  margin: const EdgeInsets.only(right: 6),
                  height: 34,
                  decoration: BoxDecoration(color: const Color(0xFF4C4C4C), borderRadius: BorderRadius.circular(4)),
                  child: Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      InkWell(
                        onTap: onLike,
                        child: Padding(
                          padding: const EdgeInsets.symmetric(horizontal: 14),
                          child: Row(
                            children: [
                              Icon(liked ? Icons.favorite : Icons.favorite_border, size: 16, color: Colors.white),
                              const SizedBox(width: 4),
                              Text(liked ? '取消' : '赞', style: const TextStyle(color: Colors.white, fontSize: 14)),
                            ],
                          ),
                        ),
                      ),
                      Container(width: 0.5, height: 18, color: const Color(0x55FFFFFF)),
                      InkWell(
                        onTap: onComment,
                        child: const Padding(
                          padding: EdgeInsets.symmetric(horizontal: 14),
                          child: Row(
                            children: [
                              Icon(Icons.chat_bubble_outline, size: 15, color: Colors.white),
                              SizedBox(width: 4),
                              Text('评论', style: TextStyle(color: Colors.white, fontSize: 14)),
                            ],
                          ),
                        ),
                      ),
                    ],
                  ),
                )
              : const SizedBox.shrink(),
        ),
        GestureDetector(
          onTap: onToggle,
          child: Container(
            width: 32,
            height: 20,
            alignment: Alignment.center,
            decoration: BoxDecoration(color: const Color(0xFFF7F7F7), borderRadius: BorderRadius.circular(2)),
            child: const Text('··', style: TextStyle(letterSpacing: 1.5, fontWeight: FontWeight.bold, color: AppColors.name, height: 1)),
          ),
        ),
      ],
    );
  }
}

class _AuthImageGrid extends StatelessWidget {
  const _AuthImageGrid({required this.api, required this.fileIds});
  final ApiClient api;
  final List<String> fileIds;

  @override
  Widget build(BuildContext context) {
    final n = fileIds.length.clamp(1, 9);
    if (n == 1) {
      return SizedBox(
        width: 170,
        height: 170,
        child: AuthFileImage(api: api, fileId: fileIds.first, fit: BoxFit.cover),
      );
    }
    final cross = n == 4 ? 2 : (n == 2 ? 2 : 3);
    final cell = cross == 2 ? 88.0 : 78.0;
    final width = cross == 2 ? cell * 2 + 4 : cell * 3 + 8;
    return SizedBox(
      width: width,
      child: GridView.builder(
        shrinkWrap: true,
        physics: const NeverScrollableScrollPhysics(),
        itemCount: n,
        gridDelegate: SliverGridDelegateWithFixedCrossAxisCount(
          crossAxisCount: cross,
          crossAxisSpacing: 4,
          mainAxisSpacing: 4,
          childAspectRatio: 1,
        ),
        itemBuilder: (context, i) => AuthFileImage(api: api, fileId: fileIds[i], fit: BoxFit.cover),
      ),
    );
  }
}

class _ImageGrid extends StatelessWidget {
  const _ImageGrid({required this.count, required this.color});
  final int count;
  final Color color;

  @override
  Widget build(BuildContext context) {
    final n = count.clamp(1, 9);
    if (n == 1) {
      return Container(
        width: 170,
        height: 170,
        color: color.withValues(alpha: 0.4),
        alignment: Alignment.center,
        child: Icon(Icons.image, color: Colors.white.withValues(alpha: 0.7), size: 36),
      );
    }
    final cross = n == 4 ? 2 : (n == 2 ? 2 : 3);
    final cell = cross == 2 ? 88.0 : 78.0;
    final width = cross == 2 ? cell * 2 + 4 : cell * 3 + 8;
    return SizedBox(
      width: width,
      child: GridView.builder(
        shrinkWrap: true,
        physics: const NeverScrollableScrollPhysics(),
        itemCount: n,
        gridDelegate: SliverGridDelegateWithFixedCrossAxisCount(
          crossAxisCount: cross,
          crossAxisSpacing: 4,
          mainAxisSpacing: 4,
          childAspectRatio: 1,
        ),
        itemBuilder: (context, i) => ColoredBox(
          color: color.withValues(alpha: 0.28 + (i % 3) * 0.12),
        ),
      ),
    );
  }
}

class ComposeMomentPage extends StatefulWidget {
  const ComposeMomentPage({
    super.key,
    required this.store,
    this.textOnly = false,
    this.initialVisibility = MomentVisibility.open,
  });
  final AppStore store;
  /// 长按相机进入：纯文字，不展示选图。
  final bool textOnly;
  final MomentVisibility initialVisibility;
  @override
  State<ComposeMomentPage> createState() => _ComposeMomentPageState();
}

class _ComposeMomentPageState extends State<ComposeMomentPage> {
  final text = TextEditingController();
  final photos = <StagedMedia>[];
  late MomentVisibility visibility = widget.initialVisibility;
  String? location;
  final mentionIds = <String>{};
  final audienceIds = <String>{};
  var posting = false;

  bool get canPost => !posting && (text.text.trim().isNotEmpty || photos.isNotEmpty);

  @override
  void dispose() {
    text.dispose();
    super.dispose();
  }

  String get _visibilitySubtitle {
    switch (visibility) {
      case MomentVisibility.open:
        return '公开';
      case MomentVisibility.privateOnly:
        return '私密';
      case MomentVisibility.partial:
        return audienceIds.isEmpty ? '部分可见' : '部分可见 · ${audienceIds.length}人';
      case MomentVisibility.exclude:
        return audienceIds.isEmpty ? '不给谁看' : '不给谁看 · ${audienceIds.length}人';
    }
  }

  Future<void> _addPhotos() async {
    final remain = 9 - photos.length;
    if (remain <= 0) return;
    final source = await showModalBottomSheet<String>(
      context: context,
      backgroundColor: Colors.white,
      builder: (ctx) => SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            ListTile(
              leading: const Icon(Icons.photo_library_outlined),
              title: const Text('从相册选择'),
              onTap: () => Navigator.pop(ctx, 'gallery'),
            ),
            ListTile(
              leading: const Icon(Icons.photo_camera_outlined),
              title: const Text('拍照'),
              onTap: () => Navigator.pop(ctx, 'camera'),
            ),
          ],
        ),
      ),
    );
    if (source == null || !mounted) return;
    if (source == 'camera') {
      final shot = await MediaPick.takePhoto();
      if (shot == null || !mounted) return;
      setState(() => photos.add(shot));
      return;
    }
    final picked = await MediaPick.pickImagesFromGallery(already: photos.length);
    if (picked.isEmpty || !mounted) return;
    setState(() => photos.addAll(picked.take(remain)));
  }

  Future<void> _pickLocation() async {
    const places = ['不显示位置', '上海市', '静安寺', '外滩', '公司附近', '家里'];
    final sel = await showModalBottomSheet<String>(
      context: context,
      backgroundColor: Colors.white,
      builder: (ctx) => SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const ListTile(title: Text('所在位置', style: TextStyle(fontWeight: FontWeight.w600))),
            for (final p in places)
              ListTile(
                title: Text(p == '不显示位置' ? p : p, style: TextStyle(color: p == '不显示位置' ? AppColors.sub : const Color(0xFF181818))),
                trailing: (location == p || (p == '不显示位置' && location == null)) ? const Icon(Icons.check, color: AppColors.green) : null,
                onTap: () => Navigator.pop(ctx, p),
              ),
          ],
        ),
      ),
    );
    if (sel == null) return;
    setState(() => location = sel == '不显示位置' ? null : sel);
  }

  Future<void> _pickMentions() async {
    final friends = widget.store.friends;
    final selected = {...mentionIds};
    await showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.white,
      builder: (ctx) {
        return StatefulBuilder(builder: (ctx, setLocal) {
          return SafeArea(
            child: SizedBox(
              height: MediaQuery.sizeOf(ctx).height * 0.55,
              child: Column(
                children: [
                  Padding(
                    padding: const EdgeInsets.fromLTRB(8, 8, 8, 0),
                    child: Row(
                      children: [
                        TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('取消')),
                        const Expanded(child: Center(child: Text('提醒谁看', style: TextStyle(fontWeight: FontWeight.w600)))),
                        TextButton(
                          onPressed: () {
                            setState(() {
                              mentionIds
                                ..clear()
                                ..addAll(selected);
                            });
                            Navigator.pop(ctx);
                          },
                          child: Text('完成(${selected.length})', style: const TextStyle(color: AppColors.green)),
                        ),
                      ],
                    ),
                  ),
                  const Padding(
                    padding: EdgeInsets.fromLTRB(16, 4, 16, 8),
                    child: Text('最多提醒 10 人', style: TextStyle(fontSize: 12, color: AppColors.sub)),
                  ),
                  const Divider(height: 1),
                  Expanded(
                    child: ListView(
                      children: [
                        for (final f in friends)
                          CheckboxListTile(
                            value: selected.contains(f.id),
                            secondary: Avatar(f.display, f.color, size: 40),
                            title: Text(f.display),
                            onChanged: (v) => setLocal(() {
                              if (v == true) {
                                if (selected.length >= 10) return;
                                selected.add(f.id);
                              } else {
                                selected.remove(f.id);
                              }
                            }),
                          ),
                      ],
                    ),
                  ),
                ],
              ),
            ),
          );
        });
      },
    );
  }

  Future<void> _pickVisibility() async {
    final result = await Navigator.push<(MomentVisibility, Set<String>)>(
      context,
      MaterialPageRoute(
        builder: (_) => _WhoCanSeePage(
          store: widget.store,
          initial: visibility,
          initialAudience: audienceIds,
        ),
      ),
    );
    if (result == null) return;
    setState(() {
      visibility = result.$1;
      audienceIds
        ..clear()
        ..addAll(result.$2);
    });
  }

  Future<void> _publish() async {
    if (!canPost) return;
    final body = text.text.trim();
    setState(() => posting = true);
    try {
      final mediaIds = <String>[];
      final api = widget.store.api;
      if (!widget.store.demo && api != null) {
        for (final p in photos) {
          final bytes = p.bytes;
          if (bytes == null || bytes.isEmpty) continue;
          final up = await api.uploadChatMedia(bytes: bytes, filename: p.name, kind: 'image');
          final fid = up['file_id']?.toString() ?? '';
          if (fid.isEmpty) throw Exception('图片上传失败');
          AuthImageCache.put(fid, bytes);
          mediaIds.add(fid);
        }
      }
      await widget.store.postMoment(
        body,
        photos.length,
        visibility: visibility,
        location: location,
        mentionIds: mentionIds.toList(),
        audienceIds: audienceIds.toList(),
        mediaIds: mediaIds,
      );
      if (!mounted) return;
      Navigator.pop(context);
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('发表失败：$e')));
      }
    } finally {
      if (mounted) setState(() => posting = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.white,
      appBar: AppBar(
        backgroundColor: Colors.white,
        title: const SizedBox.shrink(),
        actions: [
          Padding(
            padding: const EdgeInsets.only(right: 12, top: 10, bottom: 10),
            child: FilledButton(
              onPressed: canPost ? _publish : null,
              style: FilledButton.styleFrom(
                backgroundColor: AppColors.green,
                disabledBackgroundColor: const Color(0xFFB2E5C7),
                disabledForegroundColor: Colors.white70,
                minimumSize: const Size(64, 32),
                padding: const EdgeInsets.symmetric(horizontal: 14),
                shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(4)),
              ),
              child: posting
                  ? const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                  : const Text('发表', style: TextStyle(fontSize: 15)),
            ),
          ),
        ],
      ),
      body: ListView(
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 8, 16, 0),
            child: TextField(
              controller: text,
              maxLines: 5,
              minLines: 3,
              maxLength: 1500,
              style: const TextStyle(fontSize: 17, height: 1.35),
              decoration: const InputDecoration(
                hintText: '这一刻的想法...',
                hintStyle: TextStyle(color: Color(0xFFB2B2B2), fontSize: 17),
                border: InputBorder.none,
                counterText: '',
              ),
              onChanged: (_) => setState(() {}),
            ),
          ),
          if (!widget.textOnly)
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 8, 16, 16),
              child: LayoutBuilder(
                builder: (context, box) {
                  const gap = 6.0;
                  final cell = (box.maxWidth - gap * 2) / 3;
                  return Wrap(
                    spacing: gap,
                    runSpacing: gap,
                    children: [
                      for (var i = 0; i < photos.length; i++)
                        SizedBox(
                          width: cell,
                          height: cell,
                          child: Stack(
                            fit: StackFit.expand,
                            children: [
                              if (photos[i].bytes != null)
                                Image.memory(photos[i].bytes!, fit: BoxFit.cover)
                              else
                                const ColoredBox(color: Color(0xFFD8D8D8)),
                              Positioned(
                                right: 0,
                                top: 0,
                                child: GestureDetector(
                                  onTap: () => setState(() => photos.removeAt(i)),
                                  child: Container(
                                    color: const Color(0x99000000),
                                    padding: const EdgeInsets.all(2),
                                    child: const Icon(Icons.close, size: 14, color: Colors.white),
                                  ),
                                ),
                              ),
                            ],
                          ),
                        ),
                      if (photos.length < 9)
                        InkWell(
                          onTap: _addPhotos,
                          child: Container(
                            width: cell,
                            height: cell,
                            color: const Color(0xFFF7F7F7),
                            child: const Icon(Icons.add, size: 40, color: Color(0xFF888888)),
                          ),
                        ),
                    ],
                  );
                },
              ),
            ),
          const Divider(height: 1),
          // 微信底部三行入口
          _ComposeRow(
            icon: Icons.location_on_outlined,
            title: '所在位置',
            subtitle: location,
            onTap: _pickLocation,
          ),
          const Divider(height: 1, indent: 56),
          _ComposeRow(
            icon: Icons.alternate_email,
            title: '提醒谁看',
            subtitle: mentionIds.isEmpty ? null : mentionIds.map(widget.store.nameOfSender).join('、'),
            onTap: _pickMentions,
          ),
          const Divider(height: 1, indent: 56),
          _ComposeRow(
            icon: visibility == MomentVisibility.privateOnly ? Icons.lock_outline : Icons.public,
            title: '谁可以看',
            subtitle: _visibilitySubtitle,
            onTap: _pickVisibility,
          ),
          const Divider(height: 1),
        ],
      ),
    );
  }
}

class _ComposeRow extends StatelessWidget {
  const _ComposeRow({required this.icon, required this.title, this.subtitle, required this.onTap});
  final IconData icon;
  final String title;
  final String? subtitle;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return ListTile(
      leading: Icon(icon, color: const Color(0xFF181818)),
      title: Text(title),
      subtitle: subtitle == null || subtitle!.isEmpty ? null : Text(subtitle!, style: const TextStyle(color: AppColors.name, fontSize: 13)),
      trailing: const Icon(Icons.chevron_right, color: Color(0xFFC8C8C8)),
      onTap: onTap,
    );
  }
}

class _WhoCanSeePage extends StatefulWidget {
  const _WhoCanSeePage({required this.store, required this.initial, required this.initialAudience});
  final AppStore store;
  final MomentVisibility initial;
  final Set<String> initialAudience;
  @override
  State<_WhoCanSeePage> createState() => _WhoCanSeePageState();
}

class _WhoCanSeePageState extends State<_WhoCanSeePage> {
  late MomentVisibility vis = widget.initial;
  late final selected = {...widget.initialAudience};

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.bg,
      appBar: AppBar(
        title: const Text('谁可以看'),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, (vis, selected)),
            child: const Text('完成', style: TextStyle(color: AppColors.green)),
          ),
        ],
      ),
      body: ListView(
        children: [
          Section(children: [
            _VisTile(
              title: '公开',
              subtitle: '所有朋友可见',
              icon: Icons.public,
              selected: vis == MomentVisibility.open,
              onTap: () => setState(() {
                vis = MomentVisibility.open;
                selected.clear();
              }),
            ),
            _VisTile(
              title: '私密',
              subtitle: '仅自己可见',
              icon: Icons.lock_outline,
              selected: vis == MomentVisibility.privateOnly,
              onTap: () => setState(() {
                vis = MomentVisibility.privateOnly;
                selected.clear();
              }),
            ),
            _VisTile(
              title: '部分可见',
              subtitle: '选中的朋友可见',
              icon: Icons.people_outline,
              selected: vis == MomentVisibility.partial,
              onTap: () => setState(() => vis = MomentVisibility.partial),
            ),
            _VisTile(
              title: '不给谁看',
              subtitle: '选中的朋友不可见',
              icon: Icons.person_off_outlined,
              selected: vis == MomentVisibility.exclude,
              onTap: () => setState(() => vis = MomentVisibility.exclude),
            ),
          ]),
          if (vis == MomentVisibility.partial || vis == MomentVisibility.exclude) ...[
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 16, 16, 8),
              child: Text(vis == MomentVisibility.partial ? '选择朋友' : '不给谁看', style: const TextStyle(color: AppColors.sub, fontSize: 13)),
            ),
            Section(children: [
              for (final f in widget.store.friends)
                CheckboxListTile(
                  value: selected.contains(f.id),
                  secondary: Avatar(f.display, f.color, size: 36),
                  title: Text(f.display),
                  onChanged: (v) => setState(() {
                    if (v == true) {
                      selected.add(f.id);
                    } else {
                      selected.remove(f.id);
                    }
                  }),
                ),
            ]),
          ],
        ],
      ),
    );
  }
}

class _VisTile extends StatelessWidget {
  const _VisTile({required this.title, required this.subtitle, required this.icon, required this.selected, required this.onTap});
  final String title;
  final String subtitle;
  final IconData icon;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return ListTile(
      leading: Icon(icon),
      title: Text(title),
      subtitle: Text(subtitle, style: const TextStyle(fontSize: 12, color: AppColors.sub)),
      trailing: selected ? const Icon(Icons.check, color: AppColors.green) : null,
      onTap: onTap,
    );
  }
}

class MomentNoticesPage extends StatefulWidget {
  const MomentNoticesPage({super.key, required this.store});
  final AppStore store;
  @override
  State<MomentNoticesPage> createState() => _MomentNoticesPageState();
}

class _MomentNoticesPageState extends State<MomentNoticesPage> {
  @override
  void initState() {
    super.initState();
    // 勿在 build 里 ping，否则会 setState during build
    WidgetsBinding.instance.addPostFrameCallback((_) => widget.store.markNoticesRead());
  }

  @override
  Widget build(BuildContext context) {
    final store = widget.store;
    return Scaffold(
      appBar: AppBar(title: const Text('消息')),
      backgroundColor: Colors.white,
      body: ListView(
        children: [
          for (final n in store.notices)
            ListTile(
              leading: Avatar(n.who, AppColors.name),
              title: Text(n.who),
              subtitle: Text(n.text),
              trailing: Text(formatMomentTime(n.time), style: const TextStyle(fontSize: 12, color: AppColors.muted)),
            ),
        ],
      ),
    );
  }
}


/// 微信「我的朋友圈」封面：常态约半屏；点封面动画拉高到约 4/5 屏并显示「换封面」。
class _MomentsCoverBlock extends StatelessWidget {
  const _MomentsCoverBlock({
    required this.store,
    required this.coverFileId,
    required this.name,
    required this.avatarFileId,
    required this.color,
    required this.editing,
    required this.busy,
    required this.onChangeCover,
    this.onTapCover,
    this.onLongPressCover,
    this.onTapAvatar,
    this.normalRatio = 0.46,
    this.editingRatio = 0.78,
  });

  final AppStore store;
  final String coverFileId;
  final String name;
  final String avatarFileId;
  final Color color;
  final bool editing;
  final bool busy;
  final VoidCallback onChangeCover;
  final VoidCallback? onTapCover;
  final VoidCallback? onLongPressCover;
  final VoidCallback? onTapAvatar;
  final double normalRatio;
  final double editingRatio;

  static const double avatarSize = 70;

  @override
  Widget build(BuildContext context) {
    final screenH = MediaQuery.sizeOf(context).height;
    final coverH = screenH * (editing ? editingRatio : normalRatio);
    // 头像下探到白底的重叠量（换封面态不显示头像）
    final overlap = editing ? 0.0 : 36.0;

    return AnimatedContainer(
      duration: const Duration(milliseconds: 260),
      curve: Curves.easeOutCubic,
      height: coverH + overlap,
      child: Stack(
        clipBehavior: Clip.none,
        children: [
          Positioned(
            top: 0,
            left: 0,
            right: 0,
            height: coverH,
            child: GestureDetector(
              onTap: onTapCover,
              onLongPress: onLongPressCover,
              behavior: HitTestBehavior.opaque,
              child: Stack(
                fit: StackFit.expand,
                children: [
                  if (coverFileId.isNotEmpty && store.api != null)
                    AuthFileImage(api: store.api, fileId: coverFileId, fit: BoxFit.cover)
                  else
                    const DecoratedBox(
                      decoration: BoxDecoration(
                        gradient: LinearGradient(
                          begin: Alignment.topCenter,
                          end: Alignment.bottomCenter,
                          colors: [Color(0xFF7EB6E8), Color(0xFFB8D4EC), Color(0xFFE8EEF3)],
                        ),
                      ),
                    ),
                  // 上下压暗，贴近微信封面
                  const DecoratedBox(
                    decoration: BoxDecoration(
                      gradient: LinearGradient(
                        begin: Alignment.topCenter,
                        end: Alignment.bottomCenter,
                        colors: [
                          Color(0x99000000),
                          Color(0x00000000),
                          Color(0x00000000),
                          Color(0x66000000),
                        ],
                        stops: [0, 0.18, 0.72, 1],
                      ),
                    ),
                  ),
                  if (editing)
                    Positioned(
                      right: 18,
                      bottom: 20,
                      child: GestureDetector(
                        onTap: busy ? null : onChangeCover,
                        behavior: HitTestBehavior.opaque,
                        child: Opacity(
                          opacity: busy ? 0.45 : 1,
                          child: const Column(
                            mainAxisSize: MainAxisSize.min,
                            children: [
                              Icon(Icons.image_outlined, color: Colors.white, size: 30),
                              SizedBox(height: 6),
                              Text('换封面', style: TextStyle(color: Colors.white, fontSize: 13, height: 1, fontWeight: FontWeight.w400)),
                            ],
                          ),
                        ),
                      ),
                    ),
                ],
              ),
            ),
          ),
          if (!editing)
            Positioned(
              right: 14,
              bottom: 0,
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.end,
                children: [
                  Padding(
                    padding: const EdgeInsets.only(bottom: 40, right: 10),
                    child: Text(
                      name,
                      style: TextStyle(
                        color: Colors.white,
                        fontSize: 17,
                        fontWeight: FontWeight.w600,
                        shadows: [Shadow(color: Colors.black.withValues(alpha: 0.5), blurRadius: 5)],
                      ),
                    ),
                  ),
                  GestureDetector(
                    onTap: onTapAvatar,
                    child: Container(
                      decoration: BoxDecoration(
                        borderRadius: BorderRadius.circular(6),
                        border: Border.all(color: const Color(0xFFEDEDED), width: 1),
                        boxShadow: [BoxShadow(color: Colors.black.withValues(alpha: 0.2), blurRadius: 3, offset: const Offset(0, 1))],
                      ),
                      child: ClipRRect(
                        borderRadius: BorderRadius.circular(5),
                        child: Avatar(
                          name,
                          color,
                          size: avatarSize,
                          api: store.api,
                          avatarFileId: avatarFileId.isEmpty ? null : avatarFileId,
                        ),
                      ),
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

/// 「我 → 朋友圈」：微信相册（按月网格，再点「我的朋友圈」进时间线）。
class MomentsAlbumPage extends StatefulWidget {
  const MomentsAlbumPage({super.key, required this.store});
  final AppStore store;

  @override
  State<MomentsAlbumPage> createState() => _MomentsAlbumPageState();
}

class _MomentsAlbumPageState extends State<MomentsAlbumPage> with SingleTickerProviderStateMixin {
  late final TabController _tabs = TabController(length: 2, vsync: this);

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => widget.store.loadMoments());
  }

  @override
  void dispose() {
    _tabs.dispose();
    super.dispose();
  }

  /// 按年月分组的朋友圈条目（新→旧）；多图一条保留为一组，用于堆叠卡片。
  List<_AlbumMonthGroup> _monthGroups(AppStore store) {
    final posts = store.moments
        .where((m) => m.authorId == store.meId && (m.mediaIds.isNotEmpty || m.images > 0))
        .toList()
      ..sort((a, b) => b.time.compareTo(a.time));
    final groups = <_AlbumMonthGroup>[];
    for (final m in posts) {
      final y = m.time.year;
      final mo = m.time.month;
      final entry = _AlbumPost(
        momentId: m.id,
        time: m.time,
        text: m.text,
        fileIds: List<String>.from(m.mediaIds),
      );
      if (groups.isEmpty || groups.last.year != y || groups.last.month != mo) {
        groups.add(_AlbumMonthGroup(year: y, month: mo, posts: [entry]));
      } else {
        groups.last.posts.add(entry);
      }
    }
    return groups;
  }

  @override
  Widget build(BuildContext context) {
    final store = widget.store;
    return ListenableBuilder(
      listenable: store,
      builder: (context, _) {
        final groups = _monthGroups(store);
        final nowY = DateTime.now().year;
        return Scaffold(
          backgroundColor: Colors.white,
          appBar: AppBar(
            backgroundColor: Colors.white,
            title: const Text('相册'),
            bottom: PreferredSize(
              preferredSize: const Size.fromHeight(44),
              child: TabBar(
                controller: _tabs,
                onTap: (_) => setState(() {}),
                labelColor: AppColors.green,
                unselectedLabelColor: const Color(0xFF111111),
                indicatorColor: AppColors.green,
                indicatorWeight: 2.5,
                labelStyle: const TextStyle(fontSize: 15, fontWeight: FontWeight.w600),
                unselectedLabelStyle: const TextStyle(fontSize: 15, fontWeight: FontWeight.w400),
                tabs: const [
                  Tab(text: '朋友圈相册'),
                  Tab(text: '状态'),
                ],
              ),
            ),
          ),
          body: TabBarView(
            controller: _tabs,
            children: [
              CustomScrollView(
                slivers: [
                  SliverToBoxAdapter(
                    child: Align(
                      alignment: Alignment.centerRight,
                      child: TextButton(
                        onPressed: () => Navigator.push(
                          context,
                          MaterialPageRoute(builder: (_) => UserMomentsPage(store: store, authorId: store.meId)),
                        ),
                        style: TextButton.styleFrom(
                          foregroundColor: AppColors.name,
                          padding: const EdgeInsets.fromLTRB(16, 10, 16, 6),
                          minimumSize: Size.zero,
                          tapTargetSize: MaterialTapTargetSize.shrinkWrap,
                        ),
                        child: const Text('我的朋友圈 >', style: TextStyle(fontSize: 14)),
                      ),
                    ),
                  ),
                  if (groups.isEmpty)
                    const SliverFillRemaining(hasScrollBody: false, child: EmptyHint('相册还没有照片'))
                  else
                    SliverPadding(
                      padding: const EdgeInsets.fromLTRB(12, 4, 12, 40),
                      sliver: SliverList(
                        delegate: SliverChildBuilderDelegate(
                          (context, i) {
                            final g = groups[i];
                            final showYear = g.year != nowY && (i == 0 || groups[i - 1].year != g.year);
                            return _AlbumMonthBlock(
                              store: store,
                              group: g,
                              showYear: showYear,
                              hideYearOnMonth: g.year == nowY,
                            );
                          },
                          childCount: groups.length,
                        ),
                      ),
                    ),
                ],
              ),
              const Center(child: Text('暂无状态', style: TextStyle(color: AppColors.muted, fontSize: 15))),
            ],
          ),
        );
      },
    );
  }
}

class _AlbumPost {
  _AlbumPost({
    required this.momentId,
    required this.time,
    required this.text,
    required this.fileIds,
  });
  final String momentId;
  final DateTime time;
  final String text;
  final List<String> fileIds;
}

class _AlbumMonthGroup {
  _AlbumMonthGroup({required this.year, required this.month, required this.posts});
  final int year;
  final int month;
  final List<_AlbumPost> posts;
}

class _AlbumMonthBlock extends StatelessWidget {
  const _AlbumMonthBlock({
    required this.store,
    required this.group,
    required this.showYear,
    required this.hideYearOnMonth,
  });
  final AppStore store;
  final _AlbumMonthGroup group;
  final bool showYear;
  final bool hideYearOnMonth;

  @override
  Widget build(BuildContext context) {
    final label = hideYearOnMonth ? '${group.month}月' : '${group.month}月';
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        if (showYear)
          Padding(
            padding: const EdgeInsets.fromLTRB(4, 18, 4, 10),
            child: Text('${group.year}年', style: const TextStyle(fontSize: 22, fontWeight: FontWeight.w700, color: Color(0xFF111111))),
          ),
        Padding(
          padding: EdgeInsets.only(top: showYear ? 0 : 14, bottom: 8),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              SizedBox(
                width: 52,
                child: Text(label, style: const TextStyle(fontSize: 20, fontWeight: FontWeight.w700, height: 1.1, color: Color(0xFF111111))),
              ),
              Expanded(
                child: LayoutBuilder(
                  builder: (context, c) {
                    const gap = 4.0;
                    final cell = (c.maxWidth - gap * 2) / 3;
                    return Wrap(
                      spacing: gap,
                      runSpacing: gap + 4,
                      children: [
                        for (final post in group.posts)
                          SizedBox(
                            width: cell,
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                SizedBox(
                                  width: cell,
                                  height: cell,
                                  child: _AlbumStackedPhotos(
                                    store: store,
                                    fileIds: post.fileIds,
                                    size: cell,
                                  ),
                                ),
                                if (post.text.trim().isNotEmpty)
                                  Padding(
                                    padding: const EdgeInsets.only(top: 4),
                                    child: Text(
                                      post.text.trim(),
                                      maxLines: 2,
                                      overflow: TextOverflow.ellipsis,
                                      style: const TextStyle(fontSize: 11, color: Color(0xFF666666), height: 1.25),
                                    ),
                                  ),
                              ],
                            ),
                          ),
                      ],
                    );
                  },
                ),
              ),
            ],
          ),
        ),
      ],
    );
  }
}

/// 微信相册式：多图堆叠卡片（后层微偏、露边），单图铺满。
class _AlbumStackedPhotos extends StatelessWidget {
  const _AlbumStackedPhotos({
    required this.store,
    required this.fileIds,
    required this.size,
  });
  final AppStore store;
  final List<String> fileIds;
  final double size;

  @override
  Widget build(BuildContext context) {
    final ids = fileIds.where((e) => e.isNotEmpty).take(3).toList();
    if (ids.isEmpty) {
      return const ColoredBox(color: Color(0xFFE8E8E8));
    }
    if (ids.length == 1 || store.api == null) {
      return ClipRRect(
        borderRadius: BorderRadius.circular(2),
        child: store.api == null
            ? const ColoredBox(color: Color(0xFFE8E8E8))
            : AuthFileImage(api: store.api, fileId: ids.first, fit: BoxFit.cover),
      );
    }
    // 堆叠：底层最大，上层依次缩小并右下偏移，露出下层边缘
    final layers = ids.reversed.toList();
    return Stack(
      clipBehavior: Clip.none,
      children: [
        for (var i = 0; i < layers.length; i++)
          Positioned(
            left: i * (size * 0.06),
            top: i * (size * 0.05),
            child: Transform.rotate(
              angle: (i - (layers.length - 1) / 2) * 0.04,
              child: Container(
                width: size * (1 - (layers.length - 1 - i) * 0.08),
                height: size * (1 - (layers.length - 1 - i) * 0.08),
                decoration: BoxDecoration(
                  borderRadius: BorderRadius.circular(2),
                  border: Border.all(color: Colors.white, width: 1.5),
                  boxShadow: const [
                    BoxShadow(color: Color(0x33000000), blurRadius: 3, offset: Offset(0, 1)),
                  ],
                ),
                clipBehavior: Clip.antiAlias,
                child: AuthFileImage(api: store.api, fileId: layers[i], fit: BoxFit.cover),
              ),
            ),
          ),
      ],
    );
  }
}

/// 「我的朋友圈」时间线（相册里点进 / 点头像）。
class UserMomentsPage extends StatefulWidget {
  const UserMomentsPage({super.key, required this.store, required this.authorId});
  final AppStore store;
  final String authorId;

  @override
  State<UserMomentsPage> createState() => _UserMomentsPageState();
}

class _UserMomentsPageState extends State<UserMomentsPage> {
  var _editingCover = false;
  var _coverBusy = false;

  bool get _isMe => widget.authorId == widget.store.meId;

  Future<void> _changeCover() async {
    if (!_isMe || _coverBusy) return;
    final store = widget.store;
    if (!store.demo && !store.serverOnline) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('本地服务已断开，无法上传。请先恢复 API 再试')),
        );
      }
      return;
    }
    final shots = await MediaPick.pickImagesFromGallery(already: 0);
    if (shots.isEmpty || !mounted) return;
    final bytes = shots.first.bytes;
    if (bytes == null || bytes.isEmpty) return;
    setState(() => _coverBusy = true);
    try {
      var fileId = '';
      if (!store.demo && store.api != null) {
        final up = await store.api!.uploadChatMedia(bytes: bytes, filename: shots.first.name, kind: 'image');
        fileId = up['file_id']?.toString() ?? '';
        if (fileId.isEmpty) throw Exception('上传未返回文件 id');
        AuthImageCache.put(fileId, bytes);
      }
      await store.saveMyProfile(momentsCoverFileId: fileId);
      store.markServerOnline();
      if (mounted) {
        setState(() => _editingCover = false);
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('封面已更新'), duration: Duration(seconds: 1)),
        );
      }
    } catch (e) {
      final msg = '$e';
      if (msg.contains('本地服务已断开') || msg.contains('Connection refused')) {
        store.markServerOffline(msg);
      }
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('更换封面失败：$e')));
    } finally {
      if (mounted) setState(() => _coverBusy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final store = widget.store;
    final topPad = MediaQuery.paddingOf(context).top;
    return ListenableBuilder(
      listenable: store,
      builder: (context, _) {
        final name = _isMe ? store.meName : (store.persona(widget.authorId)?.display ?? widget.authorId);
        final color = _isMe ? const Color(0xFF4C8DFF) : (store.persona(widget.authorId)?.color ?? Colors.grey);
        final avatarId = _isMe ? store.meAvatarFileId : (store.persona(widget.authorId)?.avatarFileId ?? '');
        final coverId = _isMe ? store.meCoverFileId : '';
        // AI 人设用短签名，禁止把整段人设提示词当朋友圈签名
        final signature = _isMe
            ? store.meSignature
            : (store.persona(widget.authorId)?.signature ?? '');
        final list = store.moments.where((m) => m.authorId == widget.authorId).toList()
          ..sort((a, b) => b.time.compareTo(a.time));

        return Scaffold(
          backgroundColor: Colors.white,
          body: Stack(
            children: [
              GestureDetector(
                onTap: () {
                  if (_editingCover) setState(() => _editingCover = false);
                },
                behavior: HitTestBehavior.translucent,
                child: CustomScrollView(
                slivers: [
                  SliverToBoxAdapter(
                    child: _MomentsCoverBlock(
                      store: store,
                      coverFileId: coverId,
                      name: name,
                      avatarFileId: avatarId,
                      color: color,
                      editing: _isMe && _editingCover,
                      busy: _coverBusy,
                      onTapCover: _isMe ? () => setState(() => _editingCover = !_editingCover) : null,
                      onLongPressCover: _isMe ? _changeCover : null,
                      onChangeCover: _changeCover,
                    ),
                  ),
                  // 换封面展开时不显示签名（微信同款：封面占满大部分屏）
                  if (!_editingCover && signature.isNotEmpty)
                    SliverToBoxAdapter(
                      child: Align(
                        alignment: Alignment.centerRight,
                        child: Padding(
                          padding: const EdgeInsets.fromLTRB(16, 4, 18, 10),
                          child: ConstrainedBox(
                            constraints: const BoxConstraints(maxWidth: 240),
                            child: Text(
                              signature,
                              textAlign: TextAlign.right,
                              style: const TextStyle(color: AppColors.sub, fontSize: 13, height: 1.35),
                            ),
                          ),
                        ),
                      ),
                    )
                  else if (!_editingCover)
                    const SliverToBoxAdapter(child: SizedBox(height: 8)),
                  if (_editingCover)
                    const SliverToBoxAdapter(child: SizedBox(height: 10)),
                  if (_isMe)
                    SliverToBoxAdapter(
                      child: Padding(
                        padding: const EdgeInsets.fromLTRB(14, 4, 14, 8),
                        child: Row(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            const SizedBox(
                              width: 58,
                              child: Text('今天', style: TextStyle(fontSize: 22, fontWeight: FontWeight.w700, height: 1.1)),
                            ),
                            _TodayActionTile(
                              icon: Icons.camera_alt,
                              onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => ComposeMomentPage(store: store))),
                            ),
                            const SizedBox(width: 6),
                            _TodayActionTile(
                              icon: Icons.lock,
                              onTap: () => Navigator.push(
                                context,
                                MaterialPageRoute(
                                  builder: (_) => ComposeMomentPage(store: store, initialVisibility: MomentVisibility.privateOnly),
                                ),
                              ),
                            ),
                          ],
                        ),
                      ),
                    ),
                  if (list.isEmpty && !_isMe)
                    const SliverToBoxAdapter(child: EmptyHint('还没有动态'))
                  else
                    SliverList(
                      delegate: SliverChildBuilderDelegate(
                        (context, i) {
                          final m = list[i];
                          final prev = i == 0 ? null : list[i - 1];
                          final showYear = prev == null
                              ? m.time.year != DateTime.now().year
                              : m.time.year != prev.time.year;
                          final showDate = prev == null ||
                              m.time.year != prev.time.year ||
                              m.time.month != prev.time.month ||
                              m.time.day != prev.time.day;
                          return _MyMomentRow(
                            store: store,
                            moment: m,
                            showYear: showYear,
                            showDate: showDate,
                          );
                        },
                        childCount: list.length,
                      ),
                    ),
                  const SliverToBoxAdapter(child: SizedBox(height: 48)),
                ],
              ),
              ),
              Positioned(
                top: 0,
                left: 0,
                right: 0,
                child: SizedBox(
                  height: topPad + 44,
                  child: Padding(
                    padding: EdgeInsets.only(top: topPad),
                    child: Row(
                      children: [
                        IconButton(
                          onPressed: () => Navigator.pop(context),
                          icon: const Icon(Icons.arrow_back_ios_new, color: Colors.white, size: 20),
                        ),
                        const Spacer(),
                        if (_isMe) ...[
                          IconButton(
                            onPressed: () => Navigator.push(
                              context,
                              MaterialPageRoute(builder: (_) => MomentsSearchPage(store: store, authorId: store.meId)),
                            ),
                            icon: const Icon(Icons.search, color: Colors.white),
                          ),
                          IconButton(
                            onPressed: () => Navigator.push(context, MaterialPageRoute(builder: (_) => MomentNoticesPage(store: store))),
                            icon: Badge(
                              isLabelVisible: store.noticeUnread > 0,
                              smallSize: 8,
                              child: const Icon(Icons.mail_outline, color: Colors.white),
                            ),
                          ),
                        ],
                      ],
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
}

class _TodayActionTile extends StatelessWidget {
  const _TodayActionTile({required this.icon, required this.onTap});
  final IconData icon;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    // 微信：约 70 方格，仅图标，无「发表」文字
    return GestureDetector(
      onTap: onTap,
      child: Container(
        width: 70,
        height: 70,
        color: const Color(0xFFE7EBF0),
        alignment: Alignment.center,
        child: Icon(icon, color: const Color(0xFF5B7A9D), size: 30),
      ),
    );
  }
}


class _MyMomentRow extends StatelessWidget {
  const _MyMomentRow({
    required this.store,
    required this.moment,
    required this.showYear,
    required this.showDate,
  });
  final AppStore store;
  final Moment moment;
  final bool showYear;
  final bool showDate;

  @override
  Widget build(BuildContext context) {
    final m = moment;
    final day = m.time.day.toString().padLeft(2, '0');
    final month = '${m.time.month}月';
    final hasMedia = m.mediaIds.isNotEmpty || m.images > 0;
    final thumbId = m.mediaIds.isNotEmpty ? m.mediaIds.first : null;

    return Padding(
      padding: const EdgeInsets.fromLTRB(14, 0, 14, 0),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (showYear)
            Padding(
              padding: const EdgeInsets.fromLTRB(0, 20, 0, 8),
              child: Text('${m.time.year}年', style: const TextStyle(fontSize: 22, fontWeight: FontWeight.w700)),
            ),
          Padding(
            padding: const EdgeInsets.only(top: 12, bottom: 4),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                SizedBox(
                  width: 58,
                  child: showDate
                      ? RichText(
                          text: TextSpan(
                            children: [
                              TextSpan(
                                text: day,
                                style: const TextStyle(fontSize: 22, fontWeight: FontWeight.w700, color: Color(0xFF111111), height: 1),
                              ),
                              TextSpan(
                                text: ' $month',
                                style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w600, color: Color(0xFF111111), height: 1),
                              ),
                            ],
                          ),
                        )
                      : const SizedBox.shrink(),
                ),
                Expanded(
                  child: hasMedia
                      ? Row(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            SizedBox(
                              width: 74,
                              height: 74,
                              child: thumbId != null && store.api != null
                                  ? AuthFileImage(api: store.api, fileId: thumbId, fit: BoxFit.cover)
                                  : ColoredBox(color: AppColors.name.withValues(alpha: 0.25)),
                            ),
                            if (m.text.isNotEmpty) ...[
                              const SizedBox(width: 10),
                              Expanded(
                                child: Text(
                                  m.text,
                                  maxLines: 4,
                                  overflow: TextOverflow.ellipsis,
                                  style: const TextStyle(fontSize: 15, height: 1.35, color: Color(0xFF111111)),
                                ),
                              ),
                            ],
                          ],
                        )
                      : Text(
                          m.text.isEmpty ? '（无内容）' : m.text,
                          style: const TextStyle(fontSize: 15, height: 1.35, color: Color(0xFF111111)),
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

class MomentsSearchPage extends StatefulWidget {
  const MomentsSearchPage({super.key, required this.store, required this.authorId});
  final AppStore store;
  final String authorId;

  @override
  State<MomentsSearchPage> createState() => _MomentsSearchPageState();
}

class _MomentsSearchPageState extends State<MomentsSearchPage> {
  final q = TextEditingController();

  @override
  void dispose() {
    q.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final store = widget.store;
    final keyword = q.text.trim();
    final list = store.moments.where((m) {
      if (m.authorId != widget.authorId) return false;
      if (keyword.isEmpty) return true;
      return m.text.contains(keyword);
    }).toList();
    return Scaffold(
      backgroundColor: Colors.white,
      appBar: AppBar(
        title: TextField(
          controller: q,
          autofocus: true,
          decoration: const InputDecoration(hintText: '搜索这一刻…', border: InputBorder.none),
          onChanged: (_) => setState(() {}),
        ),
      ),
      body: list.isEmpty
          ? const EmptyHint('没有匹配的动态')
          : ListView.separated(
              itemCount: list.length,
              separatorBuilder: (_, _) => const Divider(height: 1),
              itemBuilder: (context, i) {
                final m = list[i];
                return ListTile(
                  title: Text(m.text.isEmpty ? '（图片动态）' : m.text, maxLines: 2, overflow: TextOverflow.ellipsis),
                  subtitle: Text(formatMomentTime(m.time), style: const TextStyle(fontSize: 12, color: AppColors.muted)),
                  onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => UserMomentsPage(store: store, authorId: widget.authorId))),
                );
              },
            ),
    );
  }
}
