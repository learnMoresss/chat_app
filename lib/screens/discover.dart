import 'package:flutter/material.dart';

import '../store.dart';
import '../theme.dart';
import '../widgets.dart';
import 'moments.dart';

/// 微信「发现」：分组白条 + 彩色图标，无「入口已留」灰字。
class DiscoverPage extends StatelessWidget {
  const DiscoverPage({super.key, required this.store});
  final AppStore store;

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: store,
      builder: (context, _) {
        return Scaffold(
          backgroundColor: AppColors.bg,
          appBar: AppBar(title: const Text('发现'), backgroundColor: AppColors.bg),
          body: ListView(
            children: [
              Section(
                margin: EdgeInsets.zero,
                children: [
                  _DiscoverTile(
                    icon: Icons.camera_alt,
                    color: const Color(0xFF2782D7),
                    title: '朋友圈',
                    badge: store.noticeUnread > 0,
                    onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => MomentsPage(store: store))),
                  ),
                ],
              ),
              Section(children: [
                _DiscoverTile(
                  icon: Icons.play_circle_filled,
                  color: const Color(0xFFFA9D3B),
                  title: '视频号',
                  onTap: () => _soon(context, '视频号'),
                ),
              ]),
              Section(children: [
                _DiscoverTile(
                  icon: Icons.qr_code_scanner,
                  color: const Color(0xFF1485EE),
                  title: '扫一扫',
                  onTap: () => _soon(context, '扫一扫'),
                ),
                const Divider(height: 1, indent: 56),
                _DiscoverTile(
                  icon: Icons.vibration,
                  color: const Color(0xFF1485EE),
                  title: '摇一摇',
                  onTap: () => _soon(context, '摇一摇'),
                ),
              ]),
              Section(children: [
                _DiscoverTile(
                  icon: Icons.visibility_outlined,
                  color: const Color(0xFF618FB8),
                  title: '看一看',
                  onTap: () => _soon(context, '看一看'),
                ),
                const Divider(height: 1, indent: 56),
                _DiscoverTile(
                  icon: Icons.search,
                  color: const Color(0xFFE75E58),
                  title: '搜一搜',
                  onTap: () => _soon(context, '搜一搜'),
                ),
              ]),
              Section(children: [
                _DiscoverTile(
                  icon: Icons.shopping_bag_outlined,
                  color: const Color(0xFFE75E58),
                  title: '购物',
                  onTap: () => _soon(context, '购物'),
                ),
                const Divider(height: 1, indent: 56),
                _DiscoverTile(
                  icon: Icons.sports_esports_outlined,
                  color: const Color(0xFF35B0C6),
                  title: '游戏',
                  onTap: () => _soon(context, '游戏'),
                ),
              ]),
              Section(children: [
                _DiscoverTile(
                  icon: Icons.apps,
                  color: const Color(0xFF6467F0),
                  title: '小程序',
                  onTap: () => _soon(context, '小程序'),
                ),
              ]),
              const SizedBox(height: 24),
            ],
          ),
        );
      },
    );
  }

  void _soon(BuildContext context, String name) {
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(content: Text('$name（演示占位）'), duration: const Duration(seconds: 1)),
    );
  }
}

class _DiscoverTile extends StatelessWidget {
  const _DiscoverTile({
    required this.icon,
    required this.color,
    required this.title,
    required this.onTap,
    this.badge = false,
  });
  final IconData icon;
  final Color color;
  final String title;
  final VoidCallback onTap;
  final bool badge;

  @override
  Widget build(BuildContext context) {
    return ListTile(
      contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 2),
      leading: Icon(icon, color: color, size: 26),
      title: Text(title, style: const TextStyle(fontSize: 17)),
      trailing: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          if (badge)
            Container(
              width: 8,
              height: 8,
              margin: const EdgeInsets.only(right: 6),
              decoration: const BoxDecoration(color: AppColors.badge, shape: BoxShape.circle),
            ),
          const Icon(Icons.chevron_right, color: Color(0xFFC8C8C8)),
        ],
      ),
      onTap: onTap,
    );
  }
}
