import 'dart:async';

import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../store.dart';
import '../theme.dart';
import 'listen_together.dart';

/// 曲库多选 → 确定创建一起听。
class ListenSongPickerPage extends StatefulWidget {
  const ListenSongPickerPage({
    super.key,
    required this.store,
    required this.convId,
  });

  final AppStore store;
  final String convId;

  @override
  State<ListenSongPickerPage> createState() => _ListenSongPickerPageState();
}

class _ListenSongPickerPageState extends State<ListenSongPickerPage> {
  final _search = TextEditingController();
  final _selected = <String>{};
  List<Map<String, dynamic>> _items = const [];
  int _total = 0;
  bool _loading = true;
  bool _creating = false;
  String? _error;
  Timer? _debounce;

  ApiClient? get _api => widget.store.api;

  @override
  void initState() {
    super.initState();
    unawaited(_load());
    _search.addListener(_onSearchChanged);
  }

  @override
  void dispose() {
    _debounce?.cancel();
    _search.removeListener(_onSearchChanged);
    _search.dispose();
    super.dispose();
  }

  void _onSearchChanged() {
    _debounce?.cancel();
    _debounce = Timer(const Duration(milliseconds: 280), () {
      unawaited(_load());
    });
  }

  Future<void> _load() async {
    final api = _api;
    if (api == null) {
      setState(() {
        _loading = false;
        _error = '未连接服务器';
      });
      return;
    }
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final j = await api.listMusicTracks(q: _search.text.trim(), limit: 300, offset: 0);
      final raw = j['items'];
      final list = <Map<String, dynamic>>[];
      if (raw is List) {
        for (final e in raw) {
          if (e is Map) list.add(Map<String, dynamic>.from(e));
        }
      }
      if (!mounted) return;
      setState(() {
        _items = list;
        _total = (j['total'] as num?)?.toInt() ?? list.length;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _error = '$e';
      });
    }
  }

  Future<void> _confirm() async {
    if (_selected.isEmpty || _creating) return;
    final api = _api;
    if (api == null) return;
    setState(() => _creating = true);
    try {
      final ids = _selected.toList();
      final inv = await api.inviteListen(
        convId: widget.convId,
        mood: '自选歌单',
        reason: '已选 ${ids.length} 首',
        trackIds: ids,
      );
      if (!mounted) return;
      final sid = (inv['session'] as Map?)?['id']?.toString() ?? '';
      if (sid.isEmpty) {
        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('创建失败：无会话 id')));
        setState(() => _creating = false);
        return;
      }
      final conv = widget.store.conv(widget.convId);
      final name = conv?.personaId != null ? widget.store.persona(conv!.personaId!)?.display : null;
      // 替换选歌页为一起听全屏
      Navigator.of(context).pushReplacement(
        MaterialPageRoute(
          builder: (_) => ListenTogetherPage(
            store: widget.store,
            sessionId: sid,
            convId: widget.convId,
            personaName: name,
          ),
        ),
      );
    } catch (e) {
      if (!mounted) return;
      setState(() => _creating = false);
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('创建失败：$e')));
    }
  }

  String _subtitle(Map<String, dynamic> t) {
    final artist = (t['artist'] ?? '').toString().trim();
    final sec = (t['duration_sec'] as num?)?.toInt() ?? 0;
    final mm = sec ~/ 60;
    final ss = (sec % 60).toString().padLeft(2, '0');
    final dur = sec > 0 ? '$mm:$ss' : '';
    if (artist.isNotEmpty && dur.isNotEmpty) return '$artist · $dur';
    if (artist.isNotEmpty) return artist;
    return dur;
  }

  @override
  Widget build(BuildContext context) {
    final n = _selected.length;
    return Scaffold(
      backgroundColor: const Color(0xFFF7F7F7),
      appBar: AppBar(
        title: const Text('选择歌曲'),
        actions: [
          TextButton(
            onPressed: _loading ? null : () => unawaited(_load()),
            child: const Text('刷新'),
          ),
        ],
      ),
      body: Column(
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(12, 8, 12, 4),
            child: TextField(
              controller: _search,
              decoration: InputDecoration(
                hintText: '搜索歌名 / 歌手',
                prefixIcon: const Icon(Icons.search, size: 20),
                filled: true,
                fillColor: Colors.white,
                contentPadding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
                border: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(8),
                  borderSide: BorderSide.none,
                ),
              ),
            ),
          ),
          if (_total > 0)
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 4, 16, 4),
              child: Align(
                alignment: Alignment.centerLeft,
                child: Text(
                  '曲库 $_total 首 · 已选 $n',
                  style: const TextStyle(fontSize: 12, color: AppColors.sub),
                ),
              ),
            ),
          Expanded(
            child: _loading
                ? const Center(child: CircularProgressIndicator())
                : _error != null
                    ? Center(
                        child: Padding(
                          padding: const EdgeInsets.all(24),
                          child: Column(
                            mainAxisSize: MainAxisSize.min,
                            children: [
                              Text(_error!, textAlign: TextAlign.center),
                              const SizedBox(height: 12),
                              FilledButton(onPressed: () => unawaited(_load()), child: const Text('重试')),
                            ],
                          ),
                        ),
                      )
                    : _items.isEmpty
                        ? const Center(
                            child: Padding(
                              padding: EdgeInsets.all(24),
                              child: Text(
                                '曲库为空，请先在运维扫描曲库后再选歌',
                                textAlign: TextAlign.center,
                                style: TextStyle(color: AppColors.sub),
                              ),
                            ),
                          )
                        : ListView.separated(
                            itemCount: _items.length,
                            separatorBuilder: (_, _) => const Divider(height: 1, indent: 56),
                            itemBuilder: (context, i) {
                              final t = _items[i];
                              final id = t['id']?.toString() ?? '';
                              if (id.isEmpty) return const SizedBox.shrink();
                              final checked = _selected.contains(id);
                              final title = (t['title'] ?? '未命名').toString();
                              return CheckboxListTile(
                                value: checked,
                                onChanged: (v) {
                                  setState(() {
                                    if (v == true) {
                                      _selected.add(id);
                                    } else {
                                      _selected.remove(id);
                                    }
                                  });
                                },
                                title: Text(title, maxLines: 1, overflow: TextOverflow.ellipsis),
                                subtitle: Text(
                                  _subtitle(t),
                                  maxLines: 1,
                                  overflow: TextOverflow.ellipsis,
                                  style: const TextStyle(fontSize: 12, color: AppColors.sub),
                                ),
                                controlAffinity: ListTileControlAffinity.leading,
                                dense: true,
                              );
                            },
                          ),
          ),
          SafeArea(
            top: false,
            child: Padding(
              padding: const EdgeInsets.fromLTRB(16, 8, 16, 12),
              child: SizedBox(
                width: double.infinity,
                height: 44,
                child: FilledButton(
                  onPressed: n > 0 && !_creating ? () => unawaited(_confirm()) : null,
                  style: FilledButton.styleFrom(
                    backgroundColor: AppColors.green,
                    disabledBackgroundColor: const Color(0xFFCCCCCC),
                  ),
                  child: _creating
                      ? const SizedBox(
                          width: 22,
                          height: 22,
                          child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white),
                        )
                      : Text(n > 0 ? '确定创建 ($n)' : '请先勾选歌曲', style: const TextStyle(fontSize: 16)),
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }
}
