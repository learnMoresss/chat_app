import 'package:flutter/material.dart';

import '../models.dart';
import '../store.dart';
import '../theme.dart';
import '../widgets.dart';
import 'fullscreen_images.dart';

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
                        subtitle: Text(p.verify.isEmpty ? (p.state == FriendState.pendingOut ? '等待对方通过' : '请求加你为朋友') : p.verify),
                        trailing: p.state == FriendState.pendingOut
                            ? const Text('等待验证', style: TextStyle(color: AppColors.muted))
                            : Row(mainAxisSize: MainAxisSize.min, children: [
                                TextButton(
                                  onPressed: () async {
                                    try {
                                      await store.ignore(p);
                                    } catch (e) {
                                      if (context.mounted) {
                                        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$e')));
                                      }
                                    }
                                  },
                                  child: const Text('忽略'),
                                ),
                                FilledButton(
                                  onPressed: () async {
                                    try {
                                      await store.accept(p);
                                      if (context.mounted) {
                                        ScaffoldMessenger.of(context).showSnackBar(
                                          SnackBar(content: Text('已通过「${p.display}」')),
                                        );
                                      }
                                    } catch (e) {
                                      if (context.mounted) {
                                        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$e')));
                                      }
                                    }
                                  },
                                  child: const Text('通过'),
                                ),
                              ]),
                      ),
                  ],
                ),
        );
      },
    );
  }
}
