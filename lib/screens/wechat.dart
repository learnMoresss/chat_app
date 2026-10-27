import 'dart:async';

import 'package:flutter/material.dart';

import '../format.dart';
import '../models.dart';
import '../store.dart';
import '../theme.dart';
import '../widgets.dart';
import 'chat.dart';
import 'create_persona.dart';
import 'search.dart';

class WechatPage extends StatelessWidget {
  const WechatPage({super.key, required this.store});
  final AppStore store;

  @override
  Widget build(BuildContext context) {
    final all = store.conversations.where((c) => !c.hidden).toList()
      ..sort((a, b) {
        if (a.pinned != b.pinned) return a.pinned ? -1 : 1;
        return b.time.compareTo(a.time);
      });
    final folded = all.where((c) => c.isGroup && (store.group(c.groupId!)?.fold ?? false)).toList();
    final rows = all.where((c) => !(c.isGroup && (store.group(c.groupId!)?.fold ?? false))).toList();

    return Scaffold(
      appBar: AppBar(
        title: const Text('未读'),
        actions: [
          IconButton(
            onPressed: () => _showPlusMenu(context),
            icon: Container(
              width: 24,
              height: 24,
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                border: Border.all(color: const Color(0xFF111111), width: 1.6),
              ),
              child: const Icon(Icons.add, size: 18, color: Color(0xFF111111)),
            ),
          ),
        ],
      ),
      body: Container(
        color: Colors.white,
        child: rows.isEmpty && folded.isEmpty
            ? const EmptyHint('还没有会话')
            : ListView.builder(
                itemCount: rows.length + (folded.isEmpty ? 0 : 1),
                itemBuilder: (context, i) {
                  if (i < rows.length) {
                    final c = rows[i];
                    return _ConvTile(store: store, conv: c, first: i == 0);
                  }
                  final unread = folded.fold<int>(0, (s, c) => s + c.unread);
                  return ListTile(
                    leading: Avatar('折', AppColors.muted, size: 50, group: true),
                    title: Text('折叠的群聊(${folded.length})'),
                    subtitle: Text(unread > 0 ? '$unread 条消息' : '暂无新消息', style: const TextStyle(color: AppColors.muted, fontSize: 14)),
                    onTap: () => Navigator.push(
                      context,
                      MaterialPageRoute(builder: (_) => _FoldedGroupsPage(store: store, convs: folded)),
                    ),
                  );
                },
              ),
      ),
    );
  }

  Future<void> _showPlusMenu(BuildContext context) async {
    final value = await showGeneralDialog<String>(
      context: context,
      barrierDismissible: true,
      barrierLabel: '关闭',
      barrierColor: Colors.transparent,
      transitionDuration: const Duration(milliseconds: 120),
      pageBuilder: (context, anim, secondary) {
        return Stack(
          children: [
            Positioned.fill(
              child: GestureDetector(onTap: () => Navigator.pop(context), behavior: HitTestBehavior.opaque),
            ),
            Positioned(
              top: MediaQuery.paddingOf(context).top + 48,
              right: 10,
              child: Material(
                color: Colors.transparent,
                child: Container(
                  width: 168,
                  decoration: BoxDecoration(
                    color: const Color(0xFF4C4C4C),
                    borderRadius: BorderRadius.circular(6),
                    boxShadow: const [BoxShadow(color: Color(0x33000000), blurRadius: 12, offset: Offset(0, 4))],
                  ),
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      _PlusItem(icon: Icons.chat_bubble_outline, label: '发起群聊', value: 'group'),
                      _PlusItem(icon: Icons.person_add_alt_1_outlined, label: '新建人设', value: 'persona'),
                      _PlusItem(icon: Icons.qr_code_scanner, label: '扫一扫', value: 'scan'),
                      _PlusItem(icon: Icons.search, label: '搜一搜', value: 'search', last: true),
                    ],
                  ),
                ),
              ),
            ),
          ],
        );
      },
    );
    if (value == null || !context.mounted) return;
    switch (value) {
      case 'group':
        showDialog<void>(
          context: context,
          builder: (context) => _CreateGroupDialog(store: store, friends: store.friends),
        );
      case 'persona':
        Navigator.push(context, MaterialPageRoute(builder: (_) => CreatePersonaPage(store: store)));
      case 'scan':
        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('扫一扫入口已留')));
      case 'search':
        Navigator.push(context, MaterialPageRoute(builder: (_) => SearchPage(store: store)));
    }
  }
}

class _PlusItem extends StatelessWidget {
  const _PlusItem({required this.icon, required this.label, required this.value, this.last = false});
  final IconData icon;
  final String label;
  final String value;
  final bool last;

  @override
  Widget build(BuildContext context) {
    return InkWell(
      onTap: () => Navigator.pop(context, value),
      child: Container(
        height: 50,
        padding: const EdgeInsets.symmetric(horizontal: 16),
        decoration: BoxDecoration(
          border: last ? null : const Border(bottom: BorderSide(color: Color(0x33FFFFFF), width: 0.5)),
        ),
        child: Row(
          children: [
            Icon(icon, color: Colors.white, size: 22),
            const SizedBox(width: 14),
            Text(label, style: const TextStyle(color: Colors.white, fontSize: 16)),
          ],
        ),
      ),
    );
  }
}

class _ConvTile extends StatelessWidget {
  const _ConvTile({required this.store, required this.conv, required this.first});
  final AppStore store;
  final Conv conv;
  final bool first;

  @override
  Widget build(BuildContext context) {
    final title = store.titleOf(conv);
    final color = store.colorOf(conv);
    final preview = conv.draft.isNotEmpty ? '[草稿] ${conv.draft}' : readableConvPreview(conv.preview);
    return InkWell(
      onTap: () {
        unawaited(store.markConversationRead(conv.id));
        Navigator.push(context, MaterialPageRoute(builder: (_) => ChatPage(store: store, conv: conv)));
      },
      onLongPress: () => _menu(context),
      child: Container(
        color: conv.pinned ? AppColors.pinned : Colors.white,
        padding: const EdgeInsets.only(left: 16),
        child: Row(
          children: [
            RedBadge(
              count: conv.unread,
              dot: conv.muted,
              child: Avatar(
                title,
                color,
                size: 50,
                group: conv.isGroup,
                api: store.api,
                avatarFileId: () {
                  if (conv.isGroup) return null;
                  final id = store.persona(conv.personaId!)?.avatarFileId ?? '';
                  return id.isEmpty ? null : id;
                }(),
              ),
            ),
            const SizedBox(width: 12),
            Expanded(
              child: Container(
                padding: const EdgeInsets.fromLTRB(0, 12, 16, 12),
                decoration: BoxDecoration(border: first ? null : const Border(top: BorderSide(color: AppColors.line, width: 0.6))),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(children: [
                      Expanded(child: Text(title, style: const TextStyle(fontSize: 17))),
                      Text(formatListTime(conv.time), style: const TextStyle(fontSize: 12, color: AppColors.muted)),
                    ]),
                    const SizedBox(height: 4),
                    Row(children: [
                      Expanded(
                        child: Text.rich(
                          TextSpan(children: [
                            if (conv.draft.isNotEmpty) const TextSpan(text: '[草稿] ', style: TextStyle(color: AppColors.badge, fontSize: 14)),
                            TextSpan(text: conv.draft.isNotEmpty ? conv.draft : preview, style: const TextStyle(color: AppColors.muted, fontSize: 14)),
                          ]),
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                        ),
                      ),
                      if (conv.muted) const Icon(Icons.notifications_off, size: 14, color: AppColors.muted),
                    ]),
                  ],
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }

  void _menu(BuildContext context) {
    showModalBottomSheet<void>(
      context: context,
      builder: (context) => SafeArea(
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          ListTile(
            title: Text(conv.unread > 0 ? '标为已读' : '标为未读'),
            onTap: () {
              if (conv.unread > 0) {
                unawaited(store.markConversationRead(conv.id));
              } else {
                conv.unread = 1;
                store.ping();
              }
              Navigator.pop(context);
            },
          ),
          ListTile(title: Text(conv.pinned ? '取消置顶' : '置顶该聊天'), onTap: () { conv.pinned = !conv.pinned; store.ping(); Navigator.pop(context); }),
          ListTile(title: Text(conv.muted ? '取消免打扰' : '消息免打扰'), onTap: () { conv.muted = !conv.muted; store.ping(); Navigator.pop(context); }),
          ListTile(
            title: const Text('不显示该聊天'),
            onTap: () {
              unawaited(store.hideConversation(conv));
              Navigator.pop(context);
            },
          ),
          ListTile(
            title: const Text('删除该聊天', style: TextStyle(color: AppColors.badge)),
            onTap: () async {
              Navigator.pop(context);
              try {
                await store.deleteConversation(conv);
              } catch (e) {
                if (context.mounted) {
                  ScaffoldMessenger.of(context).showSnackBar(
                    SnackBar(content: Text('已从本机删除；云端同步失败：$e')),
                  );
                }
              }
            },
          ),
        ]),
      ),
    );
  }
}

class _CreateGroupDialog extends StatefulWidget {
  const _CreateGroupDialog({required this.store, required this.friends});
  final AppStore store;
  final List<Persona> friends;
  @override
  State<_CreateGroupDialog> createState() => _CreateGroupDialogState();
}

class _CreateGroupDialogState extends State<_CreateGroupDialog> {
  final name = TextEditingController(text: '新的群聊');
  final picked = <String>{};

  @override
  void dispose() {
    name.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: const Text('发起群聊'),
      content: SizedBox(
        width: 320,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            TextField(controller: name, decoration: const InputDecoration(labelText: '群名')),
            const SizedBox(height: 8),
            Flexible(
              child: ListView(
                shrinkWrap: true,
                children: [
                  for (final p in widget.friends)
                    CheckboxListTile(
                      value: picked.contains(p.id),
                      onChanged: (v) => setState(() => v == true ? picked.add(p.id) : picked.remove(p.id)),
                      secondary: Avatar(p.display, p.color, size: 36),
                      title: Text(p.display),
                    ),
                ],
              ),
            ),
          ],
        ),
      ),
      actions: [
        TextButton(onPressed: () => Navigator.pop(context), child: const Text('取消')),
        TextButton(
          onPressed: picked.isEmpty
              ? null
              : () {
                  widget.store.createGroup(name.text.trim().isEmpty ? '群聊' : name.text.trim(), picked.toList());
                  Navigator.pop(context);
                },
          child: const Text('创建'),
        ),
      ],
    );
  }
}

class _FoldedGroupsPage extends StatelessWidget {
  const _FoldedGroupsPage({required this.store, required this.convs});
  final AppStore store;
  final List<Conv> convs;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('折叠的群聊')),
      backgroundColor: Colors.white,
      body: ListView.builder(
        itemCount: convs.length,
        itemBuilder: (context, i) => _ConvTile(store: store, conv: convs[i], first: i == 0),
      ),
    );
  }
}
