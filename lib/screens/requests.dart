import 'package:flutter/material.dart';

import '../models.dart';
import '../store.dart';
import '../theme.dart';
import '../widgets.dart';

class RequestsPage extends StatelessWidget {
  const RequestsPage({super.key, required this.store});
  final AppStore store;

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: store,
      builder: (context, _) {
        final list = store.requests;
        return Scaffold(
          appBar: AppBar(title: const Text('新的朋友')),
          backgroundColor: Colors.white,
          body: list.isEmpty
              ? const EmptyHint('没有新的朋友')
              : ListView(
                  children: [
                    for (final p in list)
                      ListTile(
                        leading: Avatar(p.name, p.color),
                        title: Text(p.name),
                        subtitle: Text(p.verify.isEmpty ? (p.state == FriendState.pendingOut ? '等待对方通过' : '请求加你为朋友') : p.verify),
                        trailing: p.state == FriendState.pendingOut
                            ? const Text('等待验证', style: TextStyle(color: AppColors.muted))
                            : Row(mainAxisSize: MainAxisSize.min, children: [
                                TextButton(onPressed: () => store.ignore(p), child: const Text('忽略')),
                                FilledButton(onPressed: () => store.accept(p), child: const Text('通过')),
                              ]),
                      ),
                  ],
                ),
        );
      },
    );
  }
}
