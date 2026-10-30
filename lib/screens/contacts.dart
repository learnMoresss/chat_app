import 'package:flutter/material.dart';

import '../format.dart';
import '../models.dart';
import '../store.dart';
import '../theme.dart';
import '../widgets.dart';
import 'chat.dart';
import 'create_persona.dart';
import 'fullscreen_images.dart';
import 'persona.dart';
import 'requests.dart';
import 'tags.dart';

class ContactsPage extends StatelessWidget {
  const ContactsPage({super.key, required this.store});
  final AppStore store;

  @override
  Widget build(BuildContext context) {
    final friends = [...store.friends]..sort((a, b) => letterOf(a.display).compareTo(letterOf(b.display)));
    final grouped = <String, List<Persona>>{};
    for (final p in friends) {
      grouped.putIfAbsent(letterOf(p.display), () => []).add(p);
    }
    final letters = grouped.keys.toList()..sort();

    return Scaffold(
      backgroundColor: Colors.white,
      appBar: AppBar(
        title: const Text('通讯录'),
        actions: [
          IconButton(
            tooltip: '新建人设',
            onPressed: () {
              Navigator.push(context, MaterialPageRoute(builder: (_) => CreatePersonaPage(store: store)));
            },
            icon: const Icon(Icons.person_add_alt),
          ),
        ],
      ),
      body: Stack(
        children: [
          ListView(
            children: [
              ArrowTile(
                leading: Container(width: 40, height: 40, decoration: BoxDecoration(color: const Color(0xFFFA9D3B), borderRadius: BorderRadius.circular(4)), child: const Icon(Icons.person_add_alt_1, color: Colors.white)),
                title: '新的朋友',
                trailing: RedBadge(count: store.requestCount, child: const Icon(Icons.chevron_right, color: Color(0xFFC8C8C8))),
                onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => RequestsPage(store: store))),
              ),
              ArrowTile(
                leading: Container(width: 40, height: 40, decoration: BoxDecoration(color: AppColors.green, borderRadius: BorderRadius.circular(4)), child: const Icon(Icons.groups, color: Colors.white)),
                title: '群聊',
                subtitle: '${store.groups.length} 个',
                onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => GroupsPage(store: store))),
              ),
              ArrowTile(
                leading: Container(width: 40, height: 40, decoration: BoxDecoration(color: const Color(0xFF1485EE), borderRadius: BorderRadius.circular(4)), child: const Icon(Icons.label, color: Colors.white)),
                title: '标签',
                onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => TagsPage(store: store))),
              ),
              const Divider(height: 8, thickness: 8, color: AppColors.bg),
              if (friends.isEmpty) const EmptyHint('通过好友申请后，才会出现在这里。'),
              for (final letter in letters) ...[
                Container(width: double.infinity, color: AppColors.bg, padding: const EdgeInsets.fromLTRB(16, 4, 16, 4), child: Text(letter, style: const TextStyle(color: AppColors.sub, fontSize: 13))),
                for (final p in grouped[letter]!)
                  ListTile(
                    leading: SizedBox(
                      width: 48,
                      height: 48,
                      child: GestureDetector(
                        onTap: () {
                          final fid = p.avatarFileId;
                          if (fid.isEmpty) return;
                          openAvatarPreview(
                            context,
                            api: store.api,
                            fileId: fid,
                            label: p.display,
                          );
                        },
                        child: Avatar(
                          p.display,
                          p.color,
                          size: 48,
                          api: store.api,
                          avatarFileId: p.avatarFileId.isEmpty ? null : p.avatarFileId,
                        ),
                      ),
                    ),
                    title: Text(p.display),
                    subtitle: p.isBot ? const Text('人设 · 查看配置') : null,
                    onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => PersonaPage(store: store, persona: p))),
                  ),
              ],
              Padding(padding: const EdgeInsets.all(16), child: Center(child: Text('${friends.length} 位联系人', style: const TextStyle(color: AppColors.muted)))),
            ],
          ),
          if (letters.isNotEmpty)
            Align(
              alignment: Alignment.centerRight,
              child: Padding(
                padding: const EdgeInsets.only(right: 2),
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    for (final l in letters) Text(l, style: const TextStyle(fontSize: 10, color: AppColors.sub)),
                  ],
                ),
              ),
            ),
        ],
      ),
    );
  }
}

class GroupsPage extends StatelessWidget {
  const GroupsPage({super.key, required this.store});
  final AppStore store;
  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('群聊')),
      backgroundColor: Colors.white,
      body: ListView(
        children: [
          for (final g in store.groups)
            ListTile(
              leading: Avatar(g.name, AppColors.green, group: true),
              title: Text(g.name),
              subtitle: Text('${g.memberIds.length} 人'),
              onTap: () {
                final c = store.conversations.where((e) => e.groupId == g.id).firstOrNull;
                if (c == null) return;
                Navigator.pop(context);
                Navigator.push(context, MaterialPageRoute(builder: (_) => ChatPage(store: store, conv: c)));
              },
            ),
        ],
      ),
    );
  }
}
