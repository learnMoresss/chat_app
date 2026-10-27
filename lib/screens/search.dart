import 'package:flutter/material.dart';

import '../store.dart';
import '../theme.dart';
import '../widgets.dart';
import 'chat.dart';
import 'persona.dart';

/// 微信主界面全局搜索：圆角搜索框 + 取消；空态为「搜索指定内容」快捷入口。
class SearchPage extends StatefulWidget {
  const SearchPage({super.key, required this.store});
  final AppStore store;
  @override
  State<SearchPage> createState() => _SearchPageState();
}

class _SearchPageState extends State<SearchPage> {
  final focus = FocusNode();
  final controller = TextEditingController();
  String q = '';

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => focus.requestFocus());
  }

  @override
  void dispose() {
    focus.dispose();
    controller.dispose();
    super.dispose();
  }

  void _cancel() {
    if (q.isNotEmpty) {
      controller.clear();
      setState(() => q = '');
      focus.unfocus();
      return;
    }
    Navigator.pop(context);
  }

  @override
  Widget build(BuildContext context) {
    final store = widget.store;
    final query = q.trim();
    final people = query.isEmpty ? <dynamic>[] : store.friends.where((p) => p.display.contains(query) || p.name.contains(query)).toList();
    final convs = query.isEmpty ? <dynamic>[] : store.conversations.where((c) => store.titleOf(c).contains(query)).toList();
    final hits = <(String, String, VoidCallback)>[];
    if (query.isNotEmpty) {
      for (final c in store.conversations) {
        for (final m in store.msgs(c.id)) {
          if (m.kind.name == 'text' && m.text.contains(query)) {
            hits.add((store.titleOf(c), m.text, () => Navigator.push(context, MaterialPageRoute(builder: (_) => ChatPage(store: store, conv: c)))));
          }
        }
      }
    }

    final shortcuts = [
      ('朋友圈', () => _tip('跳转朋友圈（演示）')),
      ('文章', () => _tip('暂无文章')),
      ('公众号', () => _tip('暂无公众号')),
      ('小程序', () => _tip('暂无小程序')),
      ('音乐', () => _tip('暂无音乐')),
      ('表情', () => _tip('暂无表情')),
    ];

    return Scaffold(
      backgroundColor: AppColors.bg,
      body: GestureDetector(
        onTap: () => focus.unfocus(),
        behavior: HitTestBehavior.opaque,
        child: SafeArea(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Padding(
                padding: const EdgeInsets.fromLTRB(12, 8, 8, 8),
                child: Row(
                  children: [
                    Expanded(
                      child: Container(
                        height: 36,
                        padding: const EdgeInsets.symmetric(horizontal: 10),
                        decoration: BoxDecoration(color: Colors.white, borderRadius: BorderRadius.circular(4)),
                        child: Row(
                          children: [
                            const Icon(Icons.search, size: 18, color: Color(0xFFB2B2B2)),
                            const SizedBox(width: 6),
                            Expanded(
                              child: TextField(
                                controller: controller,
                                focusNode: focus,
                                style: const TextStyle(fontSize: 16),
                                decoration: const InputDecoration(
                                  hintText: '搜索',
                                  hintStyle: TextStyle(color: Color(0xFFB2B2B2), fontSize: 16),
                                  border: InputBorder.none,
                                  isDense: true,
                                  contentPadding: EdgeInsets.symmetric(vertical: 8),
                                ),
                                onChanged: (v) => setState(() => q = v),
                              ),
                            ),
                            if (q.isNotEmpty)
                              GestureDetector(
                                onTap: () {
                                  controller.clear();
                                  setState(() => q = '');
                                },
                                child: const Icon(Icons.cancel, size: 18, color: Color(0xFFB2B2B2)),
                              ),
                          ],
                        ),
                      ),
                    ),
                    TextButton(
                      onPressed: _cancel,
                      child: const Text('取消', style: TextStyle(color: Color(0xFF181818), fontSize: 16)),
                    ),
                  ],
                ),
              ),
              Expanded(
                child: query.isEmpty
                    ? ListView(
                        keyboardDismissBehavior: ScrollViewKeyboardDismissBehavior.onDrag,
                        children: [
                          const Padding(
                            padding: EdgeInsets.fromLTRB(16, 20, 16, 16),
                            child: Text('搜索指定内容', style: TextStyle(fontSize: 14, color: Color(0xFF888888))),
                          ),
                          Padding(
                            padding: const EdgeInsets.symmetric(horizontal: 8),
                            child: GridView.builder(
                              shrinkWrap: true,
                              physics: const NeverScrollableScrollPhysics(),
                              gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(
                                crossAxisCount: 3,
                                mainAxisExtent: 44,
                              ),
                              itemCount: shortcuts.length,
                              itemBuilder: (context, i) {
                                final (label, onTap) = shortcuts[i];
                                return InkWell(
                                  onTap: () {
                                    focus.unfocus();
                                    onTap();
                                  },
                                  child: Center(
                                    child: Text(label, style: const TextStyle(fontSize: 15, color: Color(0xFF576B95))),
                                  ),
                                );
                              },
                            ),
                          ),
                        ],
                      )
                    : ListView(
                        keyboardDismissBehavior: ScrollViewKeyboardDismissBehavior.onDrag,
                        children: [
                          if (people.isNotEmpty) ...[
                            const _SectionLabel('联系人'),
                            for (final p in people)
                              Material(
                                color: Colors.white,
                                child: ListTile(
                                  leading: Avatar(p.display, p.color),
                                  title: Text(p.display),
                                  onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => PersonaPage(store: store, persona: p))),
                                ),
                              ),
                          ],
                          if (convs.isNotEmpty) ...[
                            const _SectionLabel('聊天'),
                            for (final c in convs)
                              Material(
                                color: Colors.white,
                                child: ListTile(
                                  leading: Avatar(store.titleOf(c), store.colorOf(c), group: c.isGroup),
                                  title: Text(store.titleOf(c)),
                                  subtitle: Text(c.preview, maxLines: 1, overflow: TextOverflow.ellipsis),
                                  onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => ChatPage(store: store, conv: c))),
                                ),
                              ),
                          ],
                          if (hits.isNotEmpty) ...[
                            const _SectionLabel('聊天记录'),
                            for (final h in hits)
                              Material(
                                color: Colors.white,
                                child: ListTile(
                                  title: Text(h.$1),
                                  subtitle: Text(h.$2, maxLines: 2, overflow: TextOverflow.ellipsis),
                                  onTap: h.$3,
                                ),
                              ),
                          ],
                          if (people.isEmpty && convs.isEmpty && hits.isEmpty) const EmptyHint('无结果'),
                        ],
                      ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  void _tip(String text) {
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(text), duration: const Duration(seconds: 1)));
  }
}

class _SectionLabel extends StatelessWidget {
  const _SectionLabel(this.text);
  final String text;
  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 12, 16, 6),
      child: Text(text, style: const TextStyle(color: AppColors.sub, fontSize: 13)),
    );
  }
}
