import 'package:flutter/material.dart';

import '../store.dart';
import '../theme.dart';
import '../widgets.dart';
import '../widgets/top_toast_banner.dart';
import 'chat.dart';
import 'contacts.dart';
import 'discover.dart';
import 'listen_together.dart';
import 'me.dart';
import 'wechat.dart';

class Shell extends StatefulWidget {
  const Shell({super.key, required this.store});
  final AppStore store;
  @override
  State<Shell> createState() => _ShellState();
}

class _ShellState extends State<Shell> {
  int tab = 0;

  @override
  void initState() {
    super.initState();
    widget.store.onTopToastNavigate = _handleTopToast;
  }

  @override
  void didUpdateWidget(covariant Shell oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.store != widget.store) {
      oldWidget.store.onTopToastNavigate = null;
      widget.store.onTopToastNavigate = _handleTopToast;
    }
  }

  @override
  void dispose() {
    widget.store.onTopToastNavigate = null;
    super.dispose();
  }

  void _handleTopToast(TopToast toast) {
    final store = widget.store;
    if (toast.kind == TopToastKind.listenInvite && (toast.sessionId ?? '').isNotEmpty) {
      final name = toast.personaId != null ? store.persona(toast.personaId!)?.display : null;
      Navigator.of(context).push(
        MaterialPageRoute(
          builder: (_) => ListenTogetherPage(
            store: store,
            sessionId: toast.sessionId!,
            convId: toast.convId,
            personaName: name,
          ),
        ),
      );
      return;
    }
    final convId = toast.convId;
    if (convId == null) return;
    final c = store.conv(convId);
    if (c == null) return;
    setState(() => tab = 0);
    Navigator.of(context).push(MaterialPageRoute(builder: (_) => ChatPage(store: store, conv: c)));
  }

  @override
  Widget build(BuildContext context) {
    final store = widget.store;
    // 听歌浮窗已挂到 MaterialApp.builder（盖住 Chat/Listen 全屏页）
    return TopToastBannerHost(
      store: store,
      child: ListenableBuilder(
          listenable: store,
          builder: (context, _) {
            final pages = [
              WechatPage(store: store),
              ContactsPage(store: store),
              DiscoverPage(store: store),
              MePage(store: store),
            ];
            return Scaffold(
              body: Column(
                children: [
                  if (!store.serverOnline)
                    Material(
                      color: const Color(0xFFE6A23C),
                      child: SafeArea(
                        bottom: false,
                        child: InkWell(
                          onTap: () async {
                            await store.probeServer();
                            if (!context.mounted) return;
                            final ok = store.serverOnline;
                            ScaffoldMessenger.of(context).showSnackBar(
                              SnackBar(
                                content: Text(ok ? '已重新连上服务' : '仍连不上：${store.lastOfflineReason ?? "请检查本地 API"}'),
                                duration: const Duration(seconds: 2),
                              ),
                            );
                          },
                          child: Padding(
                            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                            child: Row(
                              children: [
                                const Icon(Icons.wifi_off, color: Colors.white, size: 18),
                                const SizedBox(width: 8),
                                Expanded(
                                  child: Text(
                                    '后端不可用（点此重试）。可到「我 → 设置」重新配置服务器地址'
                                    '${store.lastOfflineReason == null ? '' : '\n${store.lastOfflineReason}'}',
                                    style: const TextStyle(color: Colors.white, fontSize: 13, height: 1.25),
                                  ),
                                ),
                              ],
                            ),
                          ),
                        ),
                      ),
                    ),
                  Expanded(child: pages[tab]),
                ],
              ),
              bottomNavigationBar: Container(
                decoration: const BoxDecoration(border: Border(top: BorderSide(color: AppColors.line, width: 0.5))),
                child: BottomNavigationBar(
                  currentIndex: tab,
                  onTap: (v) => setState(() => tab = v),
                  type: BottomNavigationBarType.fixed,
                  backgroundColor: const Color(0xFFF7F7F7),
                  selectedItemColor: AppColors.green,
                  unselectedItemColor: const Color(0xFF181818),
                  selectedFontSize: 11,
                  unselectedFontSize: 11,
                  elevation: 0,
                  items: [
                    BottomNavigationBarItem(icon: RedBadge(count: store.unreadTotal, child: const Icon(Icons.chat_bubble)), label: '微信'),
                    BottomNavigationBarItem(icon: RedBadge(count: store.requestCount, child: const Icon(Icons.contacts)), label: '通讯录'),
                    BottomNavigationBarItem(icon: RedBadge(count: store.noticeUnread, dot: true, child: const Icon(Icons.explore)), label: '发现'),
                    const BottomNavigationBarItem(icon: Icon(Icons.person), label: '我'),
                  ],
                ),
              ),
            );
          },
      ),
    );
  }
}
