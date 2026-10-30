import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../api/api_client.dart';
import '../media/media_pick.dart';
import '../models.dart';
import '../store.dart';
import '../theme.dart';
import '../widgets.dart';
import 'chat_history.dart';
import 'fullscreen_images.dart';
import 'persona.dart';

Color conversationBgColor(String name) {
  switch (name) {
    case '浅灰':
      return const Color(0xFFE8E8E8);
    case '墨绿':
      return const Color(0xFF1F3A34);
    case '暖黄':
      return const Color(0xFFF3E6C8);
    default:
      return AppColors.bg;
  }
}

/// 预设色 + 从相册选图，并持久化到服务器。
Future<void> pickConversationBackground(BuildContext context, AppStore store, Conv conv) async {
  final options = ['默认', '浅灰', '墨绿', '暖黄'];
  final sel = await showModalBottomSheet<String>(
    context: context,
    backgroundColor: Colors.white,
    builder: (ctx) => SafeArea(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          const ListTile(title: Text('选择聊天背景', style: TextStyle(fontWeight: FontWeight.w600))),
          ListTile(
            leading: const Icon(Icons.photo_library_outlined, color: AppColors.name),
            title: const Text('从相册选择'),
            onTap: () => Navigator.pop(ctx, '__album__'),
          ),
          const Divider(height: 1),
          for (final o in options)
            ListTile(
              title: Text(o),
              trailing: conv.bgFileId.isEmpty && conv.bgImageBytes == null && conv.bgName == o
                  ? const Icon(Icons.check, color: AppColors.green)
                  : null,
              onTap: () => Navigator.pop(ctx, o),
            ),
        ],
      ),
    ),
  );
  if (sel == null) return;
  try {
    if (sel == '__album__') {
      final shots = await MediaPick.pickImagesFromGallery(already: 0);
      if (shots.isEmpty) return;
      final bytes = shots.first.bytes;
      if (bytes == null || bytes.isEmpty) return;
      var fileId = '';
      if (!store.demo && store.api != null) {
        final up = await store.api!.uploadChatMedia(
          bytes: bytes,
          filename: shots.first.name,
          kind: 'image',
        );
        fileId = up['file_id']?.toString() ?? '';
        if (fileId.isEmpty) throw Exception('上传未返回文件 id');
      }
      await store.saveConversationBackground(
        conv,
        bgName: '相册图片',
        bgFileId: fileId,
        previewBytes: bytes,
      );
    } else {
      await store.saveConversationBackground(conv, bgName: sel, bgFileId: '');
    }
    if (context.mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('背景已保存'), duration: Duration(seconds: 1)),
      );
    }
  } catch (e) {
    if (context.mounted) {
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('保存背景失败：$e')));
    }
  }
}

/// 微信风格「聊天详情 / 群聊信息」。
class ChatInfoPage extends StatelessWidget {
  const ChatInfoPage({super.key, required this.store, required this.conv});
  final AppStore store;
  final Conv conv;

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: store,
      builder: (context, _) {
        if (conv.isGroup) {
          final g = store.group(conv.groupId!);
          if (g == null) {
            return const Scaffold(body: Center(child: Text('群不存在')));
          }
          return _GroupInfoBody(store: store, conv: conv, group: g);
        }
        final p = store.persona(conv.personaId!);
        if (p == null) {
          return const Scaffold(body: Center(child: Text('联系人不存在')));
        }
        return _SingleInfoBody(store: store, conv: conv, persona: p);
      },
    );
  }
}

// ──────────────────────────── 单聊 ────────────────────────────

class _SingleInfoBody extends StatelessWidget {
  const _SingleInfoBody({required this.store, required this.conv, required this.persona});
  final AppStore store;
  final Conv conv;
  final Persona persona;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.bg,
      appBar: AppBar(title: const Text('聊天详情')),
      body: ListView(
        children: [
          // 头像格：单聊仅对方，必须左对齐（不要用会拉满宽的 Wrap/Column）
          Section(
            margin: EdgeInsets.zero,
            children: [
              Padding(
                padding: const EdgeInsets.fromLTRB(16, 16, 16, 12),
                child: Align(
                  alignment: Alignment.centerLeft,
                  child: _MemberCell(
                    name: persona.display,
                    color: persona.color,
                    api: store.api,
                    avatarFileId: persona.avatarFileId,
                    onTap: () {
                      final fid = persona.avatarFileId;
                      if (fid.isNotEmpty) {
                        openAvatarPreview(
                          context,
                          api: store.api,
                          fileId: fid,
                          label: persona.display,
                        );
                      } else {
                        Navigator.push(context, MaterialPageRoute(builder: (_) => PersonaPage(store: store, persona: persona)));
                      }
                    },
                    onLongPress: () => Navigator.push(
                      context,
                      MaterialPageRoute(builder: (_) => PersonaPage(store: store, persona: persona)),
                    ),
                  ),
                ),
              ),
            ],
          ),
          Section(children: [
            ArrowTile(
              title: '定时任务',
              subtitle: '仅此人设的聊天提醒 / 每日任务',
              onTap: () => Navigator.push(
                context,
                MaterialPageRoute(
                  builder: (_) => PersonaSchedulesPage(store: store, persona: persona),
                ),
              ),
            ),
            ArrowTile(
              title: '设置备注和标签',
              subtitle: [
                if (persona.remark.isNotEmpty) '备注：${persona.remark}',
                if (persona.tagNames.isNotEmpty) '标签：${persona.tagNames.join('、')}',
              ].where((e) => e.isNotEmpty).join(' · ').nullIfEmpty,
              onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => FriendRemarkPage(store: store, persona: persona))),
            ),
            ArrowTile(
              title: '朋友权限',
              subtitle: _friendPermSummary(persona),
              onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => FriendPermPage(store: store, persona: persona))),
            ),
          ]),
          Section(children: [
            ArrowTile(
              title: '查找聊天记录',
              onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => ChatHistoryPage(store: store, conv: conv))),
            ),
          ]),
          Section(children: [
            _WxSwitch(title: '消息免打扰', value: conv.muted, onChanged: (v) { conv.muted = v; store.ping(); }),
            _WxSwitch(title: '置顶聊天', value: conv.pinned, onChanged: (v) { conv.pinned = v; store.ping(); }),
            _WxSwitch(title: '强提醒', value: conv.strongRemind, onChanged: (v) { conv.strongRemind = v; store.ping(); }),
          ]),
          Section(children: [
            ArrowTile(
              title: '设置当前聊天背景',
              subtitle: (conv.bgFileId.isNotEmpty || conv.bgImageBytes != null) ? '相册图片' : conv.bgName,
              onTap: () => pickConversationBackground(context, store, conv),
            ),
          ]),
          Section(children: [
            ArrowTile(
              title: '清理格式残渣',
              subtitle: store.demo ? '演示模式不可用' : '把程序壳 / think 等助手消息移出模型上下文',
              onTap: store.demo
                  ? null
                  : () async {
                      try {
                        final n = await store.cleanConversationResidue(conv.id);
                        if (context.mounted) {
                          ScaffoldMessenger.of(context).showSnackBar(
                            SnackBar(content: Text(n == 0 ? '没有发现格式残渣' : '已排除 $n 条，不再喂给模型')),
                          );
                        }
                      } catch (e) {
                        if (context.mounted) {
                          ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('清理失败：$e')));
                        }
                      }
                    },
            ),
            ArrowTile(
              title: '清空聊天记录',
              onTap: () => _clearHistory(context),
            ),
          ]),
          Section(children: [
            ListTile(
              title: const Center(child: Text('删除联系人', style: TextStyle(color: AppColors.badge, fontSize: 16))),
              onTap: () => _deleteContact(context),
            ),
          ]),
          const SizedBox(height: 32),
        ],
      ),
    );
  }

  String _friendPermSummary(Persona p) {
    final parts = <String>[];
    if (p.starred) parts.add('星标朋友');
    if (p.hideHisMoments) parts.add('不看他');
    if (p.hideMyMoments) parts.add('不让他看');
    if (p.blacklisted) parts.add('已拉黑');
    return parts.isEmpty ? '聊天、朋友圈等' : parts.join(' · ');
  }

  Future<void> _clearHistory(BuildContext context) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('清空聊天记录'),
        content: Text('将清空与「${persona.display}」的聊天记录，且无法恢复。'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('取消')),
          TextButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('清空', style: TextStyle(color: AppColors.badge))),
        ],
      ),
    );
    if (ok == true) {
      store.clearConv(conv);
      if (context.mounted) _toast(context, '已清空');
    }
  }

  Future<void> _deleteContact(BuildContext context) async {
    final clearFlag = <bool>[true];
    final ok = await showWxConfirmDialog(
      context,
      title: '删除「${persona.display}」',
      message: '将同时删除与该联系人的关系。',
      confirmText: '删除',
      destructive: true,
      checkboxLabel: '同时清空聊天记录',
      checkboxValue: clearFlag,
    );
    if (ok != true) return;
    store.deleteContact(persona, clearHistory: clearFlag.isEmpty ? true : clearFlag.first);
    if (!context.mounted) return;
    Navigator.popUntil(context, (r) => r.isFirst);
  }
}

// ──────────────────────────── 群聊 ────────────────────────────

class _GroupInfoBody extends StatefulWidget {
  const _GroupInfoBody({required this.store, required this.conv, required this.group});
  final AppStore store;
  final Conv conv;
  final Group group;

  @override
  State<_GroupInfoBody> createState() => _GroupInfoBodyState();
}

class _GroupInfoBodyState extends State<_GroupInfoBody> {
  bool removing = false;

  AppStore get store => widget.store;
  Conv get conv => widget.conv;
  Group get g => widget.group;
  bool get isOwner => g.ownerId == store.meId;

  @override
  Widget build(BuildContext context) {
    final members = [...g.memberIds];
    return Scaffold(
      backgroundColor: AppColors.bg,
      appBar: AppBar(title: Text('聊天信息(${g.memberIds.length})')),
      body: ListView(
        children: [
          Section(
            margin: EdgeInsets.zero,
            children: [
              Padding(
                padding: const EdgeInsets.fromLTRB(12, 16, 12, 8),
                child: GridView.count(
                  shrinkWrap: true,
                  physics: const NeverScrollableScrollPhysics(),
                  crossAxisCount: 5,
                  mainAxisSpacing: 12,
                  crossAxisSpacing: 8,
                  childAspectRatio: 0.72,
                  children: [
                    for (final id in members)
                      _MemberCell(
                        name: store.memberDisplay(id, g),
                        color: id == store.meId ? const Color(0xFF4C8DFF) : (store.persona(id)?.color ?? Colors.grey),
                        api: store.api,
                        avatarFileId: id == store.meId ? store.meAvatarFileId : (store.persona(id)?.avatarFileId ?? ''),
                        onTap: () {
                          final fid = id == store.meId
                              ? store.meAvatarFileId
                              : (store.persona(id)?.avatarFileId ?? '');
                          if (fid.isNotEmpty) {
                            openAvatarPreview(
                              context,
                              api: store.api,
                              fileId: fid,
                              label: store.memberDisplay(id, g),
                            );
                            return;
                          }
                          if (id == store.meId) return;
                          final p = store.persona(id);
                          if (p != null) {
                            Navigator.push(context, MaterialPageRoute(builder: (_) => PersonaPage(store: store, persona: p)));
                          }
                        },
                        onLongPress: () {
                          if (isOwner && id != store.meId && id != g.ownerId) {
                            setState(() => removing = true);
                            HapticFeedback.selectionClick();
                            return;
                          }
                          if (id == store.meId) return;
                          final p = store.persona(id);
                          if (p != null) {
                            Navigator.push(context, MaterialPageRoute(builder: (_) => PersonaPage(store: store, persona: p)));
                          }
                        },
                      ),
                    _MemberCell(
                      name: '添加',
                      color: Colors.transparent,
                      dashed: true,
                      icon: Icons.add,
                      onTap: () => _addMembers(context),
                    ),
                    if (isOwner)
                      _MemberCell(
                        name: '移除',
                        color: Colors.transparent,
                        dashed: true,
                        icon: Icons.remove,
                        onTap: () => setState(() => removing = !removing),
                      ),
                  ],
                ),
              ),
              if (removing)
                Padding(
                  padding: const EdgeInsets.fromLTRB(16, 0, 16, 12),
                  child: Text('点成员头像旁进入资料后，或在下方选择移除。当前为移除模式：再点「移除」退出。', style: TextStyle(fontSize: 12, color: AppColors.sub)),
                ),
              if (removing)
                for (final id in members.where((e) => e != store.meId && e != g.ownerId))
                  ListTile(
                    leading: Avatar(store.memberDisplay(id, g), store.persona(id)?.color ?? Colors.grey, size: 36),
                    title: Text(store.memberDisplay(id, g)),
                    trailing: TextButton(
                      onPressed: () {
                        store.removeGroupMember(g, id);
                        if (g.memberIds.length <= 1) setState(() => removing = false);
                      },
                      child: const Text('移除', style: TextStyle(color: AppColors.badge)),
                    ),
                  ),
            ],
          ),
          Section(children: [
            ArrowTile(
              title: '群聊名称',
              subtitle: g.name,
              onTap: () => _editField(context, title: '群聊名称', initial: g.name, onSave: (v) {
                g.name = v;
                store.ping();
              }),
            ),
            ArrowTile(
              title: '群二维码',
              trailing: const Icon(Icons.qr_code, color: AppColors.muted),
              onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => _GroupQrPage(group: g))),
            ),
            ArrowTile(
              title: '群公告',
              subtitle: g.announcement.isEmpty ? '未设置' : g.announcement,
              onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => _AnnouncePage(store: store, group: g, editable: isOwner))),
            ),
          ]),
          Section(children: [
            ArrowTile(
              title: '备注',
              subtitle: g.remark.isEmpty ? '群聊的备注仅自己可见' : g.remark,
              onTap: () => _editField(context, title: '群备注', initial: g.remark, hint: '仅自己可见', onSave: (v) {
                g.remark = v;
                store.ping();
              }),
            ),
            ArrowTile(
              title: '我在本群的昵称',
              subtitle: g.myNick.isEmpty ? store.meName : g.myNick,
              onTap: () => _editField(context, title: '我在本群的昵称', initial: g.myNick.isEmpty ? store.meName : g.myNick, onSave: (v) {
                g.myNick = v;
                store.ping();
              }),
            ),
            _WxSwitch(
              title: '显示群成员昵称',
              value: g.showNicks,
              onChanged: (v) {
                g.showNicks = v;
                store.ping();
              },
            ),
          ]),
          Section(children: [
            ArrowTile(
              title: '查找聊天内容',
              onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => ChatHistoryPage(store: store, conv: conv))),
            ),
          ]),
          Section(children: [
            _WxSwitch(title: '消息免打扰', value: conv.muted, onChanged: (v) { conv.muted = v; store.ping(); }),
            if (conv.muted)
              ArrowTile(
                title: '以下消息仍通知',
                subtitle: _notifySummary(),
                onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => _NotifyStillPage(store: store, group: g))),
              ),
            _WxSwitch(title: '置顶聊天', value: conv.pinned, onChanged: (v) { conv.pinned = v; store.ping(); }),
            _WxSwitch(title: '折叠该群聊', value: g.fold, onChanged: (v) { g.fold = v; store.ping(); }),
            _WxSwitch(title: '保存到通讯录', value: g.saved, onChanged: (v) { g.saved = v; store.ping(); }),
          ]),
          Section(children: [
            ArrowTile(
              title: '设置当前聊天背景',
              subtitle: (conv.bgFileId.isNotEmpty || conv.bgImageBytes != null) ? '相册图片' : conv.bgName,
              onTap: () => pickConversationBackground(context, store, conv),
            ),
          ]),
          Section(children: [
            ArrowTile(title: '清空聊天记录', onTap: () => _clear(context)),
          ]),
          if (isOwner)
            Section(children: [
              ArrowTile(
                title: '群管理',
                onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => _GroupManagePage(store: store, group: g))),
              ),
            ]),
          Section(children: [
            ListTile(
              title: Center(
                child: Text(isOwner ? '解散群聊' : '删除并退出', style: const TextStyle(color: AppColors.badge, fontSize: 16)),
              ),
              onTap: () => _leave(context),
            ),
          ]),
          const SizedBox(height: 32),
        ],
      ),
    );
  }

  String _notifySummary() {
    final parts = <String>[];
    if (g.notifyAtMe) parts.add('@我');
    if (g.notifyAtAll) parts.add('@所有人');
    if (g.notifyAnnounce) parts.add('群公告');
    if (g.watchIds.isNotEmpty) parts.add('${g.watchIds.length}位成员');
    return parts.isEmpty ? '未选择' : parts.join('、');
  }

  Future<void> _addMembers(BuildContext context) async {
    final candidates = store.friends.where((f) => !g.memberIds.contains(f.id)).toList();
    if (candidates.isEmpty) {
      _toast(context, '没有可添加的联系人');
      return;
    }
    final picked = <String>{};
    final ok = await showModalBottomSheet<bool>(
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
                        TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('取消')),
                        const Expanded(child: Center(child: Text('选择联系人', style: TextStyle(fontWeight: FontWeight.w600)))),
                        TextButton(onPressed: picked.isEmpty ? null : () => Navigator.pop(ctx, true), child: Text('完成(${picked.length})')),
                      ],
                    ),
                  ),
                  const Divider(height: 1),
                  Expanded(
                    child: ListView(
                      children: [
                        for (final f in candidates)
                          CheckboxListTile(
                            value: picked.contains(f.id),
                            secondary: Avatar(f.display, f.color, size: 40),
                            title: Text(f.display),
                            onChanged: (v) => setLocal(() {
                              if (v == true) {
                                picked.add(f.id);
                              } else {
                                picked.remove(f.id);
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
    if (ok == true && picked.isNotEmpty) {
      store.addGroupMembers(g, picked.toList());
    }
  }

  Future<void> _clear(BuildContext context) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('清空聊天记录'),
        content: Text('将清空「${g.name}」的聊天记录，且无法恢复。'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('取消')),
          TextButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('清空', style: TextStyle(color: AppColors.badge))),
        ],
      ),
    );
    if (ok == true) {
      store.clearConv(conv);
      if (context.mounted) _toast(context, '已清空');
    }
  }

  Future<void> _leave(BuildContext context) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(isOwner ? '解散群聊' : '删除并退出'),
        content: Text(isOwner ? '解散后所有成员将被移出，聊天记录清空。' : '退出后将不再接收此群消息。'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('取消')),
          TextButton(onPressed: () => Navigator.pop(ctx, true), child: Text(isOwner ? '解散' : '退出', style: const TextStyle(color: AppColors.badge))),
        ],
      ),
    );
    if (ok != true) return;
    store.leaveGroup(conv);
    if (!context.mounted) return;
    Navigator.popUntil(context, (r) => r.isFirst);
  }
}

// ──────────────────────────── 子页面 ────────────────────────────

class FriendRemarkPage extends StatefulWidget {
  const FriendRemarkPage({super.key, required this.store, required this.persona});
  final AppStore store;
  final Persona persona;
  @override
  State<FriendRemarkPage> createState() => _FriendRemarkPageState();
}

class _FriendRemarkPageState extends State<FriendRemarkPage> {
  late final remark = TextEditingController(text: widget.persona.remark);
  late final phone = TextEditingController(text: widget.persona.phone);
  late final desc = TextEditingController(text: widget.persona.desc);

  @override
  void dispose() {
    remark.dispose();
    phone.dispose();
    desc.dispose();
    super.dispose();
  }

  void _save() {
    widget.persona.remark = remark.text.trim();
    widget.persona.phone = phone.text.trim();
    widget.persona.desc = desc.text.trim();
    widget.store.ping();
    Navigator.pop(context);
  }

  Future<void> _editTags() async {
    final p = widget.persona;
    final store = widget.store;
    final selected = {...p.tagNames};
    final createCtrl = TextEditingController();
    await showDialog<void>(
      context: context,
      builder: (ctx) => StatefulBuilder(builder: (ctx, setLocal) {
        final names = <String>{
          ...store.tags.map((t) => t.name),
          ...selected,
        }.toList()
          ..sort();
        return AlertDialog(
          title: const Text('选择标签'),
          content: SizedBox(
            width: 300,
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                if (names.isEmpty)
                  const Padding(
                    padding: EdgeInsets.only(bottom: 8),
                    child: Text('还没有标签，先新建一个', style: TextStyle(color: AppColors.sub, fontSize: 13)),
                  ),
                ConstrainedBox(
                  constraints: const BoxConstraints(maxHeight: 240),
                  child: SingleChildScrollView(
                    child: Column(
                      children: [
                        for (final name in names)
                          CheckboxListTile(
                            dense: true,
                            contentPadding: EdgeInsets.zero,
                            value: selected.contains(name),
                            title: Text(name),
                            onChanged: (v) => setLocal(() {
                              if (v == true) {
                                selected.add(name);
                              } else {
                                selected.remove(name);
                              }
                            }),
                          ),
                      ],
                    ),
                  ),
                ),
                const Divider(height: 16),
                Row(
                  children: [
                    Expanded(
                      child: TextField(
                        controller: createCtrl,
                        decoration: const InputDecoration(
                          hintText: '新建标签名',
                          isDense: true,
                          border: OutlineInputBorder(),
                        ),
                        onSubmitted: (_) {
                          final n = createCtrl.text.trim();
                          if (n.isEmpty) return;
                          setLocal(() {
                            selected.add(n);
                            createCtrl.clear();
                          });
                        },
                      ),
                    ),
                    const SizedBox(width: 8),
                    TextButton(
                      onPressed: () {
                        final n = createCtrl.text.trim();
                        if (n.isEmpty) return;
                        setLocal(() {
                          selected.add(n);
                          createCtrl.clear();
                        });
                      },
                      child: const Text('添加', style: TextStyle(color: AppColors.green)),
                    ),
                  ],
                ),
              ],
            ),
          ),
          actions: [
            TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('取消')),
            TextButton(
              onPressed: () {
                p.tagNames
                  ..clear()
                  ..addAll(selected);
                for (final name in selected) {
                  final existing = store.tags.where((t) => t.name == name).toList();
                  if (existing.isEmpty) {
                    store.tags.add(TagGroup(name, [p.id]));
                  } else if (!existing.first.memberIds.contains(p.id)) {
                    existing.first.memberIds.add(p.id);
                  }
                }
                for (final g in store.tags) {
                  if (!selected.contains(g.name)) {
                    g.memberIds.remove(p.id);
                  }
                }
                store.ping();
                setState(() {});
                Navigator.pop(ctx);
              },
              child: const Text('确定'),
            ),
          ],
        );
      }),
    );
    createCtrl.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final p = widget.persona;
    return Scaffold(
      backgroundColor: AppColors.bg,
      appBar: AppBar(
        title: const Text('设置备注和标签'),
        actions: [TextButton(onPressed: _save, child: const Text('完成', style: TextStyle(color: AppColors.green)))],
      ),
      body: ListView(
        children: [
          Section(children: [
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 16),
              child: TextField(controller: remark, decoration: const InputDecoration(labelText: '备注名', border: InputBorder.none)),
            ),
            const Divider(height: 1, indent: 16),
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 16),
              child: TextField(controller: phone, decoration: const InputDecoration(labelText: '电话', border: InputBorder.none), keyboardType: TextInputType.phone),
            ),
            const Divider(height: 1, indent: 16),
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 16),
              child: TextField(controller: desc, decoration: const InputDecoration(labelText: '描述', border: InputBorder.none), maxLines: 3),
            ),
          ]),
          Section(children: [
            ArrowTile(
              title: '标签',
              subtitle: p.tagNames.isEmpty ? '未设置' : p.tagNames.join('、'),
              onTap: () => _editTags(),
            ),
          ]),
        ],
      ),
    );
  }
}

class FriendPermPage extends StatelessWidget {
  const FriendPermPage({super.key, required this.store, required this.persona});
  final AppStore store;
  final Persona persona;

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: store,
      builder: (context, _) {
        return Scaffold(
          backgroundColor: AppColors.bg,
          appBar: AppBar(title: const Text('朋友权限')),
          body: ListView(
            children: [
              Section(children: [
                _WxSwitch(title: '星标朋友', value: persona.starred, onChanged: (v) { persona.starred = v; store.ping(); }),
              ]),
              Section(children: [
                _WxSwitch(title: '不看他（她）的朋友圈', value: persona.hideHisMoments, onChanged: (v) { persona.hideHisMoments = v; store.ping(); }),
                _WxSwitch(title: '不让他（她）看我的朋友圈', value: persona.hideMyMoments, onChanged: (v) { persona.hideMyMoments = v; store.ping(); }),
              ]),
              Section(children: [
                _WxSwitch(title: '加入黑名单', value: persona.blacklisted, onChanged: (v) { persona.blacklisted = v; store.ping(); }),
              ]),
            ],
          ),
        );
      },
    );
  }
}

class _AnnouncePage extends StatefulWidget {
  const _AnnouncePage({required this.store, required this.group, required this.editable});
  final AppStore store;
  final Group group;
  final bool editable;
  @override
  State<_AnnouncePage> createState() => _AnnouncePageState();
}

class _AnnouncePageState extends State<_AnnouncePage> {
  late final c = TextEditingController(text: widget.group.announcement);

  @override
  void dispose() {
    c.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.white,
      appBar: AppBar(
        title: const Text('群公告'),
        actions: [
          if (widget.editable)
            TextButton(
              onPressed: () {
                widget.group.announcement = c.text.trim();
                widget.store.ping();
                Navigator.pop(context);
                ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('群公告已更新'), duration: Duration(seconds: 1)));
              },
              child: const Text('完成', style: TextStyle(color: AppColors.green)),
            ),
        ],
      ),
      body: Padding(
        padding: const EdgeInsets.all(16),
        child: TextField(
          controller: c,
          enabled: widget.editable,
          maxLines: null,
          decoration: InputDecoration(
            hintText: widget.editable ? '填写群公告，全体成员可见' : '暂无公告',
            border: InputBorder.none,
          ),
        ),
      ),
    );
  }
}

class _NotifyStillPage extends StatelessWidget {
  const _NotifyStillPage({required this.store, required this.group});
  final AppStore store;
  final Group group;

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: store,
      builder: (context, _) {
        return Scaffold(
          backgroundColor: AppColors.bg,
          appBar: AppBar(title: const Text('以下消息仍通知')),
          body: ListView(
            children: [
              const Padding(
                padding: EdgeInsets.fromLTRB(16, 12, 16, 4),
                child: Text('开启免打扰后，仍可收到这些提醒', style: TextStyle(color: AppColors.sub, fontSize: 13)),
              ),
              Section(children: [
                _WxSwitch(title: '@我', value: group.notifyAtMe, onChanged: (v) { group.notifyAtMe = v; store.ping(); }),
                _WxSwitch(title: '@所有人', value: group.notifyAtAll, onChanged: (v) { group.notifyAtAll = v; store.ping(); }),
                _WxSwitch(title: '群公告', value: group.notifyAnnounce, onChanged: (v) { group.notifyAnnounce = v; store.ping(); }),
              ]),
              Section(children: [
                ArrowTile(
                  title: '添加重要群成员',
                  subtitle: group.watchIds.isEmpty ? '最多 4 人' : group.watchIds.map(store.nameOfSender).join('、'),
                  onTap: () async {
                    final candidates = group.memberIds.where((id) => id != store.meId).toList();
                    final selected = {...group.watchIds};
                    await showDialog<void>(
                      context: context,
                      builder: (ctx) => StatefulBuilder(builder: (ctx, setLocal) {
                        return AlertDialog(
                          title: const Text('重要群成员'),
                          content: SizedBox(
                            width: 280,
                            child: Column(
                              mainAxisSize: MainAxisSize.min,
                              children: [
                                for (final id in candidates)
                                  CheckboxListTile(
                                    value: selected.contains(id),
                                    title: Text(store.nameOfSender(id)),
                                    onChanged: (v) => setLocal(() {
                                      if (v == true) {
                                        if (selected.length >= 4) return;
                                        selected.add(id);
                                      } else {
                                        selected.remove(id);
                                      }
                                    }),
                                  ),
                              ],
                            ),
                          ),
                          actions: [
                            TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('取消')),
                            TextButton(
                              onPressed: () {
                                group.watchIds
                                  ..clear()
                                  ..addAll(selected);
                                store.ping();
                                Navigator.pop(ctx);
                              },
                              child: const Text('确定'),
                            ),
                          ],
                        );
                      }),
                    );
                  },
                ),
              ]),
            ],
          ),
        );
      },
    );
  }
}

class _GroupManagePage extends StatelessWidget {
  const _GroupManagePage({required this.store, required this.group});
  final AppStore store;
  final Group group;

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: store,
      builder: (context, _) {
        return Scaffold(
          backgroundColor: AppColors.bg,
          appBar: AppBar(title: const Text('群管理')),
          body: ListView(
            children: [
              Section(children: [
                ArrowTile(
                  title: '群主管理权转让',
                  subtitle: '当前群主：${store.nameOfSender(group.ownerId)}',
                  onTap: () async {
                    final others = group.memberIds.where((id) => id != store.meId).toList();
                    final id = await showModalBottomSheet<String>(
                      context: context,
                      backgroundColor: Colors.white,
                      builder: (ctx) => SafeArea(
                        child: Column(
                          mainAxisSize: MainAxisSize.min,
                          children: [
                            for (final oid in others)
                              ListTile(
                                leading: Avatar(store.nameOfSender(oid), store.persona(oid)?.color ?? Colors.grey, size: 40),
                                title: Text(store.nameOfSender(oid)),
                                onTap: () => Navigator.pop(ctx, oid),
                              ),
                          ],
                        ),
                      ),
                    );
                    if (id != null) {
                      group.ownerId = id;
                      store.ping();
                      if (context.mounted) {
                        _toast(context, '已转让给 ${store.nameOfSender(id)}');
                        Navigator.pop(context);
                      }
                    }
                  },
                ),
              ]),
              const Padding(
                padding: EdgeInsets.all(16),
                child: Text('演示版：进群确认、管理员等开关可在后续接后端时补齐。', style: TextStyle(color: AppColors.sub, fontSize: 13)),
              ),
            ],
          ),
        );
      },
    );
  }
}

class _GroupQrPage extends StatelessWidget {
  const _GroupQrPage({required this.group});
  final Group group;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: const Color(0xFF2E2E2E),
      appBar: AppBar(backgroundColor: const Color(0xFF2E2E2E), foregroundColor: Colors.white, title: const Text('群二维码')),
      body: Center(
        child: Container(
          width: 280,
          padding: const EdgeInsets.all(24),
          decoration: BoxDecoration(color: Colors.white, borderRadius: BorderRadius.circular(8)),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Text(group.name, style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w600)),
              const SizedBox(height: 8),
              Text('${group.memberIds.length}人', style: const TextStyle(color: AppColors.sub)),
              const SizedBox(height: 20),
              Container(
                width: 180,
                height: 180,
                color: const Color(0xFFF0F0F0),
                alignment: Alignment.center,
                child: const Icon(Icons.qr_code_2, size: 120, color: Color(0xFF333333)),
              ),
              const SizedBox(height: 16),
              const Text('该二维码 7 天内有效（演示）', style: TextStyle(fontSize: 12, color: AppColors.sub)),
            ],
          ),
        ),
      ),
    );
  }
}

// ──────────────────────────── 人设定时任务（提醒 + 主动计划） ────────────────────────────

class PersonaSchedulesPage extends StatefulWidget {
  const PersonaSchedulesPage({super.key, required this.store, required this.persona});
  final AppStore store;
  final Persona persona;

  @override
  State<PersonaSchedulesPage> createState() => _PersonaSchedulesPageState();
}

class _PersonaSchedulesPageState extends State<PersonaSchedulesPage> {
  var loading = true;
  var saving = false;
  String? error;
  List<Map<String, dynamic>> items = const [];

  bool _isUserReminder(String kind) => kind == 'user_once' || kind == 'user_daily';
  bool _isLegacy(String kind) =>
      kind.startsWith('greet_') || kind.startsWith('moment_') || kind == 'moment_daily';

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    final api = widget.store.api;
    if (widget.store.demo || api == null) {
      setState(() {
        loading = false;
        error = '演示模式不可用';
        items = const [];
      });
      return;
    }
    setState(() {
      loading = true;
      error = null;
    });
    try {
      final raw = await api.listSchedules(personaId: widget.persona.id);
      final list = <Map<String, dynamic>>[
        for (final e in raw)
          if (e is Map) Map<String, dynamic>.from(e),
      ];
      list.sort((a, b) {
        final ka = a['kind'] as String? ?? '';
        final kb = b['kind'] as String? ?? '';
        final ra = _isUserReminder(ka) ? 0 : (_isLegacy(ka) ? 2 : 1);
        final rb = _isUserReminder(kb) ? 0 : (_isLegacy(kb) ? 2 : 1);
        if (ra != rb) return ra.compareTo(rb);
        final ha = (a['hour'] as num?)?.toInt() ?? 0;
        final hb = (b['hour'] as num?)?.toInt() ?? 0;
        if (ha != hb) return ha.compareTo(hb);
        return ((a['minute'] as num?)?.toInt() ?? 0).compareTo((b['minute'] as num?)?.toInt() ?? 0);
      });
      if (!mounted) return;
      setState(() {
        items = list;
        loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        loading = false;
        error = '$e';
      });
    }
  }

  String _kindLabel(String kind) {
    return switch (kind) {
      'user_daily' => '每日提醒',
      'user_once' => '一次提醒',
      'custom' => '自定义',
      _ when kind.startsWith('greet_') => '旧问候',
      _ when kind.startsWith('moment_') => '旧朋友圈',
      _ => kind,
    };
  }

  String _formatNext(String? nxt) {
    if (nxt == null || nxt.isEmpty) return '';
    final dt = DateTime.tryParse(nxt);
    if (dt == null) return nxt;
    final local = dt.toLocal();
    return '${local.month.toString().padLeft(2, '0')}-${local.day.toString().padLeft(2, '0')} '
        '${local.hour.toString().padLeft(2, '0')}:${local.minute.toString().padLeft(2, '0')}';
  }

  String _subtitle(Map<String, dynamic> s) {
    final kind = s['kind'] as String? ?? '';
    final hour = (s['hour'] as num?)?.toInt() ?? 0;
    final minute = (s['minute'] as num?)?.toInt() ?? 0;
    final when = _formatNext(s['next_run_at'] as String?);
    final hm = '${hour.toString().padLeft(2, '0')}:${minute.toString().padLeft(2, '0')}';
    if (kind == 'user_daily') {
      return '每天 $hm${when.isEmpty ? '' : ' · 下次 $when'}';
    }
    if (kind == 'user_once') {
      return when.isEmpty ? '一次 · 待触发' : '一次 · $when';
    }
    return '定点 $hm${when.isEmpty ? '' : ' · 下次 $when'}';
  }

  Future<void> _toggle(Map<String, dynamic> s) async {
    final id = s['id'] as String? ?? '';
    if (id.isEmpty || widget.store.api == null || saving) return;
    final enabled = s['enabled'] == true && s['paused'] != true;
    setState(() => saving = true);
    try {
      final next = await widget.store.api!.patchSchedule(
        id,
        enabled: !enabled,
        paused: enabled,
        reschedule: !enabled,
      );
      if (!mounted) return;
      setState(() {
        items = [
          for (final e in items)
            if (e['id'] == id) Map<String, dynamic>.from(next) else e,
        ];
      });
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('更新失败：$e')));
    } finally {
      if (mounted) setState(() => saving = false);
    }
  }

  Future<void> _delete(Map<String, dynamic> s) async {
    final id = s['id'] as String? ?? '';
    if (id.isEmpty || widget.store.api == null) return;
    final ok = await showWxConfirmDialog(
      context,
      title: '删除任务',
      message: '确定删除「${s['title'] ?? '任务'}」？',
      confirmText: '删除',
      destructive: true,
    );
    if (ok != true) return;
    try {
      await widget.store.api!.deleteSchedule(id);
      if (!mounted) return;
      setState(() => items = items.where((e) => e['id'] != id).toList());
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('已删除')));
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('删除失败：$e')));
    }
  }

  Future<void> _addDaily() async {
    final api = widget.store.api;
    if (api == null || widget.store.demo) return;
    final titleCtrl = TextEditingController();
    final timeCtrl = TextEditingController(text: '08:00');
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('添加每日提醒'),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            TextField(
              controller: titleCtrl,
              decoration: const InputDecoration(labelText: '内容', hintText: '如：叫我起床'),
              autofocus: true,
            ),
            const SizedBox(height: 8),
            TextField(
              controller: timeCtrl,
              decoration: const InputDecoration(labelText: '时间', hintText: 'HH:MM'),
              keyboardType: TextInputType.datetime,
            ),
          ],
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('取消')),
          TextButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('添加')),
        ],
      ),
    );
    final title = titleCtrl.text.trim();
    final timeRaw = timeCtrl.text.trim();
    titleCtrl.dispose();
    timeCtrl.dispose();
    if (ok != true || !mounted) return;
    if (title.isEmpty) {
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('请填写内容')));
      return;
    }
    final m = RegExp(r'^(\d{1,2})[:：](\d{1,2})$').firstMatch(timeRaw);
    if (m == null) {
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('时间格式用 08:00')));
      return;
    }
    final hour = int.parse(m.group(1)!).clamp(0, 23);
    final minute = int.parse(m.group(2)!).clamp(0, 59);
    setState(() => saving = true);
    try {
      await api.createSchedule(
        personaId: widget.persona.id,
        kind: 'user_daily',
        title: title,
        hour: hour,
        minute: minute,
        prompt: '到点了，用平常聊天语气提醒对方：$title。一两句话就好，不要说这是定时任务。',
        jitterMinutes: 0,
        minIntervalMinutes: 0,
      );
      await _load();
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('已添加每日提醒')));
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('添加失败：$e')));
    } finally {
      if (mounted) setState(() => saving = false);
    }
  }

  Widget _sectionHeader(String title) {
    return Container(
      width: double.infinity,
      color: AppColors.bg,
      padding: const EdgeInsets.fromLTRB(16, 14, 16, 6),
      child: Text(title, style: const TextStyle(fontSize: 13, color: AppColors.sub)),
    );
  }

  Widget _tile(Map<String, dynamic> s) {
    final kind = s['kind'] as String? ?? '';
    final enabled = s['enabled'] == true && s['paused'] != true;
    final title = (s['title'] as String?)?.trim();
    return ListTile(
      title: Text(title?.isNotEmpty == true ? title! : _kindLabel(kind)),
      subtitle: Text(
        '${_kindLabel(kind)} · ${_subtitle(s)}${enabled ? '' : ' · 已停用'}',
        style: const TextStyle(fontSize: 13, color: AppColors.sub),
      ),
      trailing: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Switch(
            value: enabled,
            activeTrackColor: AppColors.green,
            onChanged: saving ? null : (_) => _toggle(s),
          ),
          IconButton(
            icon: const Icon(Icons.delete_outline, color: AppColors.badge),
            onPressed: saving ? null : () => _delete(s),
          ),
        ],
      ),
      isThreeLine: true,
    );
  }

  @override
  Widget build(BuildContext context) {
    final reminders = items.where((e) => _isUserReminder(e['kind'] as String? ?? '')).toList();
    final others = items.where((e) {
      final k = e['kind'] as String? ?? '';
      return !_isUserReminder(k) && !_isLegacy(k);
    }).toList();
    final legacy = items.where((e) => _isLegacy(e['kind'] as String? ?? '')).toList();

    return Scaffold(
      backgroundColor: AppColors.bg,
      appBar: AppBar(
        title: Text('定时任务 · ${widget.persona.display}'),
        actions: [
          IconButton(onPressed: loading || saving ? null : _load, icon: const Icon(Icons.refresh)),
          TextButton(onPressed: loading || saving ? null : _addDaily, child: const Text('添加')),
        ],
      ),
      body: loading
          ? const Center(child: CircularProgressIndicator())
          : error != null
              ? Center(child: Text(error!, style: const TextStyle(color: AppColors.sub)))
              : ListView(
                  children: [
                    Padding(
                      padding: const EdgeInsets.fromLTRB(16, 12, 16, 4),
                      child: Text(
                        '仅「${widget.persona.display}」此人设。和她/他聊天时说的提醒写在这里，不会混到别人设。',
                        style: const TextStyle(fontSize: 13, color: AppColors.sub, height: 1.35),
                      ),
                    ),
                    _sectionHeader('聊天提醒 / 每日任务（${reminders.length}）'),
                    if (reminders.isEmpty)
                      const Padding(
                        padding: EdgeInsets.fromLTRB(16, 8, 16, 16),
                        child: Text('此人设暂无提醒。可点右上角「添加」，或在与此人设的聊天里口述创建。', style: TextStyle(color: AppColors.muted)),
                      )
                    else
                      Section(
                        margin: EdgeInsets.zero,
                        children: [
                          for (var i = 0; i < reminders.length; i++) ...[
                            if (i > 0) const Divider(height: 1, indent: 16),
                            _tile(reminders[i]),
                          ],
                        ],
                      ),
                    if (others.isNotEmpty) ...[
                      _sectionHeader('其他计划（${others.length}）'),
                      Section(
                        margin: EdgeInsets.zero,
                        children: [
                          for (var i = 0; i < others.length; i++) ...[
                            if (i > 0) const Divider(height: 1, indent: 16),
                            _tile(others[i]),
                          ],
                        ],
                      ),
                    ],
                    if (legacy.isNotEmpty) ...[
                      _sectionHeader('旧钟点配方（可删，${legacy.length}）'),
                      Section(
                        margin: EdgeInsets.zero,
                        children: [
                          for (var i = 0; i < legacy.length; i++) ...[
                            if (i > 0) const Divider(height: 1, indent: 16),
                            _tile(legacy[i]),
                          ],
                        ],
                      ),
                    ],
                    const SizedBox(height: 32),
                  ],
                ),
    );
  }
}

// ──────────────────────────── 共用小组件 ────────────────────────────

class _MemberCell extends StatelessWidget {
  const _MemberCell({
    required this.name,
    required this.color,
    this.onTap,
    this.onLongPress,
    this.dashed = false,
    this.icon,
    this.api,
    this.avatarFileId = '',
  });
  final String name;
  final Color color;
  final VoidCallback? onTap;
  final VoidCallback? onLongPress;
  final bool dashed;
  final IconData? icon;
  final ApiClient? api;
  final String avatarFileId;

  @override
  Widget build(BuildContext context) {
    return Material(
      color: Colors.transparent,
      child: InkWell(
        onTap: onTap,
        onLongPress: onLongPress,
        borderRadius: BorderRadius.circular(8),
        child: SizedBox(
          width: 56,
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.center,
            children: [
              if (dashed)
                CustomPaint(
                  painter: _DashBorderPainter(color: const Color(0xFFC8C8C8)),
                  child: SizedBox(
                    width: 48,
                    height: 48,
                    child: Icon(icon ?? Icons.add, color: const Color(0xFF888888), size: 28),
                  ),
                )
              else
                Avatar(
                  name,
                  color,
                  size: 48,
                  api: api,
                  avatarFileId: avatarFileId.isEmpty ? null : avatarFileId,
                ),
              const SizedBox(height: 6),
              Text(
                name,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                textAlign: TextAlign.center,
                style: const TextStyle(fontSize: 11, color: Color(0xFF666666)),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _DashBorderPainter extends CustomPainter {
  _DashBorderPainter({required this.color});
  final Color color;

  @override
  void paint(Canvas canvas, Size size) {
    final paint = Paint()
      ..color = color
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1;
    const dash = 4.0;
    const gap = 3.0;
    final r = RRect.fromRectAndRadius(Offset.zero & size, const Radius.circular(4));
    final path = Path()..addRRect(r);
    for (final metric in path.computeMetrics()) {
      var d = 0.0;
      while (d < metric.length) {
        final end = (d + dash > metric.length) ? metric.length : d + dash;
        canvas.drawPath(metric.extractPath(d, end), paint);
        d = end + gap;
      }
    }
  }

  @override
  bool shouldRepaint(covariant CustomPainter oldDelegate) => false;
}

class _WxSwitch extends StatelessWidget {
  const _WxSwitch({required this.title, required this.value, required this.onChanged});
  final String title;
  final bool value;
  final ValueChanged<bool> onChanged;

  @override
  Widget build(BuildContext context) {
    return SwitchListTile(
      title: Text(title),
      value: value,
      activeThumbColor: Colors.white,
      activeTrackColor: AppColors.green,
      onChanged: onChanged,
    );
  }
}

Future<void> _editField(
  BuildContext context, {
  required String title,
  required String initial,
  String? hint,
  required ValueChanged<String> onSave,
}) async {
  final c = TextEditingController(text: initial);
  final ok = await showDialog<bool>(
    context: context,
    builder: (ctx) => AlertDialog(
      title: Text(title),
      content: TextField(
        controller: c,
        autofocus: true,
        decoration: InputDecoration(hintText: hint),
      ),
      actions: [
        TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('取消')),
        TextButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('确定')),
      ],
    ),
  );
  if (ok == true) onSave(c.text.trim());
  c.dispose();
}

void _toast(BuildContext context, String text) {
  ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(text), duration: const Duration(seconds: 1)));
}

extension on String {
  String? get nullIfEmpty => isEmpty ? null : this;
}
