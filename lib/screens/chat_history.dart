import 'package:flutter/material.dart';

import '../format.dart';
import '../models.dart';
import '../store.dart';
import '../theme.dart';
import '../widgets.dart';
import 'chat.dart';

/// 微信「查找聊天内容」：顶栏搜索 +「搜索指定内容」三列蓝字入口。
class ChatHistoryPage extends StatefulWidget {
  const ChatHistoryPage({super.key, required this.store, required this.conv, this.memberId});
  final AppStore store;
  final Conv conv;
  final String? memberId;

  @override
  State<ChatHistoryPage> createState() => _ChatHistoryPageState();
}

class _ChatHistoryPageState extends State<ChatHistoryPage> {
  final focus = FocusNode();
  final controller = TextEditingController();
  String q = '';

  AppStore get store => widget.store;
  Conv get conv => widget.conv;

  @override
  void initState() {
    super.initState();
    // 微信进入后会聚焦搜索框；点空白可收起
    WidgetsBinding.instance.addPostFrameCallback((_) => focus.requestFocus());
  }

  @override
  void dispose() {
    focus.dispose();
    controller.dispose();
    super.dispose();
  }

  List<Msg> get _all {
    var list = store.msgs(conv.id).where((m) => m.kind != MsgKind.system).toList();
    if (widget.memberId != null) {
      list = list.where((m) => m.senderId == widget.memberId).toList();
    }
    return list;
  }

  void _dismissKb() => focus.unfocus();

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
    final query = q.trim();
    final hits = query.isEmpty ? <Msg>[] : _all.where((m) => m.text.contains(query)).toList().reversed.toList();
    final hint = widget.memberId == null ? '搜索' : '搜索 ${store.nameOfSender(widget.memberId!)}';

    final cats = <(String, VoidCallback)>[
      if (conv.isGroup) ('群成员', () => Navigator.push(context, MaterialPageRoute(builder: (_) => _MemberFilterPage(store: store, conv: conv)))),
      ('日期', () => Navigator.push(context, MaterialPageRoute(builder: (_) => _DateBrowsePage(store: store, conv: conv)))),
      ('图片与视频', () => Navigator.push(context, MaterialPageRoute(builder: (_) => _KindBrowsePage(store: store, conv: conv, kind: MsgKind.image, title: '图片与视频')))),
      ('文件', () => Navigator.push(context, MaterialPageRoute(builder: (_) => _KindBrowsePage(store: store, conv: conv, kind: MsgKind.file, title: '文件')))),
      ('链接', () => _emptyTip(context, '本会话暂无链接')),
      ('音乐与音频', () => Navigator.push(context, MaterialPageRoute(builder: (_) => _KindBrowsePage(store: store, conv: conv, kind: MsgKind.voice, title: '音乐与音频')))),
      ('交易', () => _emptyTip(context, '本会话暂无交易记录')),
      ('小程序', () => _emptyTip(context, '本会话暂无小程序')),
      ('视频号', () => _emptyTip(context, '本会话暂无视频号')),
    ];

    return Scaffold(
      backgroundColor: AppColors.bg,
      body: GestureDetector(
        onTap: _dismissKb,
        behavior: HitTestBehavior.opaque,
        child: SafeArea(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              // 顶栏：返回 + 圆角搜索框 + 取消
              Padding(
                padding: const EdgeInsets.fromLTRB(4, 4, 8, 8),
                child: Row(
                  children: [
                    IconButton(
                      onPressed: () => Navigator.pop(context),
                      icon: const Icon(Icons.arrow_back_ios_new, size: 18),
                      color: const Color(0xFF181818),
                    ),
                    Expanded(
                      child: Container(
                        height: 36,
                        padding: const EdgeInsets.symmetric(horizontal: 10),
                        decoration: BoxDecoration(
                          color: Colors.white,
                          borderRadius: BorderRadius.circular(4),
                        ),
                        child: Row(
                          children: [
                            const Icon(Icons.search, size: 18, color: Color(0xFFB2B2B2)),
                            const SizedBox(width: 6),
                            Expanded(
                              child: TextField(
                                controller: controller,
                                focusNode: focus,
                                style: const TextStyle(fontSize: 16),
                                textInputAction: TextInputAction.search,
                                decoration: InputDecoration(
                                  hintText: hint,
                                  hintStyle: const TextStyle(color: Color(0xFFB2B2B2), fontSize: 16),
                                  border: InputBorder.none,
                                  isDense: true,
                                  contentPadding: const EdgeInsets.symmetric(vertical: 8),
                                ),
                                onChanged: (v) => setState(() => q = v),
                                onTap: () => setState(() {}),
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
                child: query.isNotEmpty
                    ? (hits.isEmpty
                        ? const EmptyHint('无结果')
                        : ListView.builder(
                            itemCount: hits.length,
                            itemBuilder: (context, i) {
                              final m = hits[i];
                              return Material(
                                color: Colors.white,
                                child: ListTile(
                                  leading: Avatar(
                                    store.nameOfSender(m.senderId),
                                    m.mine ? const Color(0xFF4C8DFF) : (store.persona(m.senderId)?.color ?? Colors.grey),
                                    size: 40,
                                  ),
                                  title: Text(store.nameOfSender(m.senderId), style: const TextStyle(fontSize: 14)),
                                  subtitle: Text(m.text, maxLines: 2, overflow: TextOverflow.ellipsis),
                                  trailing: Text(formatListTime(m.time), style: const TextStyle(fontSize: 12, color: AppColors.muted)),
                                  onTap: () {
                                    Navigator.pop(context);
                                    Navigator.pushReplacement(context, MaterialPageRoute(builder: (_) => ChatPage(store: store, conv: conv)));
                                  },
                                ),
                              );
                            },
                          ))
                    : ListView(
                        // 点空白收起键盘
                        keyboardDismissBehavior: ScrollViewKeyboardDismissBehavior.onDrag,
                        children: [
                          const Padding(
                            padding: EdgeInsets.fromLTRB(16, 20, 16, 16),
                            child: Text('搜索指定内容', style: TextStyle(fontSize: 14, color: Color(0xFF888888))),
                          ),
                          // 微信：三列蓝字，无箭头、无白卡片列表
                          Padding(
                            padding: const EdgeInsets.symmetric(horizontal: 8),
                            child: GridView.builder(
                              shrinkWrap: true,
                              physics: const NeverScrollableScrollPhysics(),
                              gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(
                                crossAxisCount: 3,
                                mainAxisExtent: 44,
                              ),
                              itemCount: cats.length,
                              itemBuilder: (context, i) {
                                final (label, onTap) = cats[i];
                                return InkWell(
                                  onTap: () {
                                    _dismissKb();
                                    onTap();
                                  },
                                  child: Center(
                                    child: Text(
                                      label,
                                      style: const TextStyle(fontSize: 15, color: Color(0xFF576B95)),
                                    ),
                                  ),
                                );
                              },
                            ),
                          ),
                        ],
                      ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  void _emptyTip(BuildContext context, String text) {
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(text), duration: const Duration(seconds: 1)));
  }
}

class _MemberFilterPage extends StatelessWidget {
  const _MemberFilterPage({required this.store, required this.conv});
  final AppStore store;
  final Conv conv;

  @override
  Widget build(BuildContext context) {
    final g = store.group(conv.groupId!);
    final ids = g?.memberIds ?? [];
    return Scaffold(
      backgroundColor: AppColors.bg,
      appBar: AppBar(title: const Text('按群成员查找')),
      body: ListView(
        children: [
          for (final id in ids)
            ListTile(
              tileColor: Colors.white,
              leading: Avatar(store.memberDisplay(id, g!), store.persona(id)?.color ?? const Color(0xFF4C8DFF)),
              title: Text(store.memberDisplay(id, g)),
              onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => ChatHistoryPage(store: store, conv: conv, memberId: id))),
            ),
        ],
      ),
    );
  }
}

/// 按日期：有记录的日子黑色，无记录灰色（仿微信日历感列表）。
class _DateBrowsePage extends StatefulWidget {
  const _DateBrowsePage({required this.store, required this.conv});
  final AppStore store;
  final Conv conv;
  @override
  State<_DateBrowsePage> createState() => _DateBrowsePageState();
}

class _DateBrowsePageState extends State<_DateBrowsePage> {
  late DateTime month;

  @override
  void initState() {
    super.initState();
    final msgs = widget.store.msgs(widget.conv.id);
    month = msgs.isEmpty ? DateTime.now() : DateTime(msgs.last.time.year, msgs.last.time.month);
  }

  Set<String> get _activeDays {
    final set = <String>{};
    for (final m in widget.store.msgs(widget.conv.id)) {
      if (m.kind == MsgKind.system) continue;
      set.add('${m.time.year}-${m.time.month}-${m.time.day}');
    }
    return set;
  }

  @override
  Widget build(BuildContext context) {
    final y = month.year;
    final m = month.month;
    final first = DateTime(y, m, 1);
    final daysInMonth = DateTime(y, m + 1, 0).day;
    final startWeekday = first.weekday % 7; // 周日=0
    final active = _activeDays;

    return Scaffold(
      backgroundColor: Colors.white,
      appBar: AppBar(
        title: const Text('按日期查找'),
        actions: [
          IconButton(
            onPressed: () => setState(() => month = DateTime(y, m - 1)),
            icon: const Icon(Icons.chevron_left),
          ),
          Center(child: Text('$y年$m月', style: const TextStyle(fontSize: 15))),
          IconButton(
            onPressed: () => setState(() => month = DateTime(y, m + 1)),
            icon: const Icon(Icons.chevron_right),
          ),
        ],
      ),
      body: Column(
        children: [
          Padding(
            padding: const EdgeInsets.symmetric(vertical: 8),
            child: Row(
              children: [
                for (final w in ['日', '一', '二', '三', '四', '五', '六'])
                  Expanded(
                    child: Center(child: Text(w, style: const TextStyle(color: AppColors.sub, fontSize: 13))),
                  ),
              ],
            ),
          ),
          const Divider(height: 1),
          Expanded(
            child: GridView.builder(
              padding: const EdgeInsets.all(8),
              gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(crossAxisCount: 7),
              itemCount: startWeekday + daysInMonth,
              itemBuilder: (context, i) {
                if (i < startWeekday) return const SizedBox.shrink();
                final day = i - startWeekday + 1;
                final key = '$y-$m-$day';
                final has = active.contains(key);
                return InkWell(
                  onTap: has
                      ? () {
                          final list = widget.store.msgs(widget.conv.id).where((msg) {
                            return msg.kind != MsgKind.system && msg.time.year == y && msg.time.month == m && msg.time.day == day;
                          }).toList();
                          showModalBottomSheet<void>(
                            context: context,
                            backgroundColor: Colors.white,
                            builder: (ctx) => SafeArea(
                              child: ListView(
                                shrinkWrap: true,
                                children: [
                                  ListTile(title: Text('$y年$m月$day日', style: const TextStyle(fontWeight: FontWeight.w600))),
                                  for (final msg in list)
                                    ListTile(
                                      dense: true,
                                      title: Text('${widget.store.nameOfSender(msg.senderId)}: ${msg.text}', maxLines: 2, overflow: TextOverflow.ellipsis),
                                    ),
                                ],
                              ),
                            ),
                          );
                        }
                      : null,
                  child: Center(
                    child: Text(
                      '$day',
                      style: TextStyle(
                        fontSize: 16,
                        fontWeight: has ? FontWeight.w600 : FontWeight.w400,
                        color: has ? const Color(0xFF181818) : const Color(0xFFCCCCCC),
                      ),
                    ),
                  ),
                );
              },
            ),
          ),
          const Padding(
            padding: EdgeInsets.only(bottom: 16),
            child: Text('黑色日期表示当天有聊天记录', style: TextStyle(fontSize: 12, color: AppColors.sub)),
          ),
        ],
      ),
    );
  }
}

class _KindBrowsePage extends StatelessWidget {
  const _KindBrowsePage({required this.store, required this.conv, required this.kind, required this.title});
  final AppStore store;
  final Conv conv;
  final MsgKind kind;
  final String title;

  @override
  Widget build(BuildContext context) {
    final list = store.msgs(conv.id).where((m) => m.kind == kind).toList().reversed.toList();
    return Scaffold(
      backgroundColor: AppColors.bg,
      appBar: AppBar(title: Text(title)),
      body: list.isEmpty
          ? EmptyHint('暂无$title')
          : kind == MsgKind.image
              ? GridView.builder(
                  padding: const EdgeInsets.all(4),
                  gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(crossAxisCount: 4, mainAxisSpacing: 2, crossAxisSpacing: 2),
                  itemCount: list.length,
                  itemBuilder: (context, i) {
                    final m = list[i];
                    return ColoredBox(
                      color: const Color(0xFFD0D0D0),
                      child: Center(child: Text(m.text, style: const TextStyle(fontSize: 10, color: Colors.white), textAlign: TextAlign.center)),
                    );
                  },
                )
              : ListView.separated(
                  itemCount: list.length,
                  separatorBuilder: (context, index) => const Divider(height: 1),
                  itemBuilder: (context, i) {
                    final m = list[i];
                    return ListTile(
                      tileColor: Colors.white,
                      leading: Icon(kind == MsgKind.file ? Icons.insert_drive_file : Icons.graphic_eq, color: AppColors.name),
                      title: Text(m.kind == MsgKind.voice ? '语音 ${m.seconds}"' : m.text),
                      subtitle: Text('${store.nameOfSender(m.senderId)} · ${formatListTime(m.time)}', style: const TextStyle(fontSize: 12)),
                    );
                  },
                ),
    );
  }
}
