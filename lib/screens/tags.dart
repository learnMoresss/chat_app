import 'package:flutter/material.dart';

import '../store.dart';
import '../widgets.dart';

class TagsPage extends StatelessWidget {
  const TagsPage({super.key, required this.store});
  final AppStore store;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('标签')),
      backgroundColor: Colors.white,
      body: ListView(
        children: [
          for (final t in store.tags)
            ExpansionTile(
              title: Text('${t.name}（${t.memberIds.length}）'),
              children: [
                for (final id in t.memberIds)
                  ListTile(
                    leading: Avatar(store.persona(id)?.display ?? id, store.persona(id)?.color ?? Colors.grey, size: 36),
                    title: Text(store.persona(id)?.display ?? id),
                  ),
              ],
            ),
          if (store.tags.isEmpty) const EmptyHint('还没有标签'),
        ],
      ),
    );
  }
}
