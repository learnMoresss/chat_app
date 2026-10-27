import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../store.dart';
import '../theme.dart';
import '../widgets.dart';

/// 对照 Open WebUI「Settings → Usage」：看自己的调用与 token。
class UsagePage extends StatefulWidget {
  const UsagePage({super.key, required this.store});
  final AppStore store;

  @override
  State<UsagePage> createState() => _UsagePageState();
}

class _UsagePageState extends State<UsagePage> {
  bool loading = true;
  String? error;
  Map<String, dynamic>? summary;
  List<dynamic> logs = [];

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    if (widget.store.demo || widget.store.api == null) {
      setState(() {
        loading = false;
        error = '未连接服务器，无法查看用量。';
      });
      return;
    }
    setState(() {
      loading = true;
      error = null;
    });
    try {
      final s = await widget.store.api!.usageSummary(days: 30);
      final l = await widget.store.api!.usageLogs(limit: 40);
      if (!mounted) return;
      setState(() {
        summary = s;
        logs = l;
        loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        loading = false;
        error = e is ApiException ? e.message : '$e';
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.bg,
      appBar: AppBar(
        title: const Text('用量'),
        actions: [
          IconButton(onPressed: loading ? null : _load, icon: const Icon(Icons.refresh)),
        ],
      ),
      body: loading
          ? const Center(child: CircularProgressIndicator(color: AppColors.green))
          : error != null && summary == null
              ? Center(child: Padding(padding: const EdgeInsets.all(24), child: Text(error!, textAlign: TextAlign.center)))
              : ListView(
                  children: [
                    if (error != null)
                      Padding(
                        padding: const EdgeInsets.all(12),
                        child: Text(error!, style: const TextStyle(color: AppColors.badge, fontSize: 13)),
                      ),
                    Section(
                      children: [
                        const ListTile(title: Text('近 30 天', style: TextStyle(fontWeight: FontWeight.w600))),
                        Padding(
                          padding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
                          child: Row(
                            children: [
                              _Metric('调用', '${summary?['calls'] ?? 0}'),
                              _Metric('成功', '${summary?['ok_calls'] ?? 0}'),
                              _Metric('Token', '${summary?['total_tokens'] ?? 0}'),
                            ],
                          ),
                        ),
                      ],
                    ),
                    if ((summary?['by_model'] as List?)?.isNotEmpty == true)
                      Section(
                        children: [
                          const ListTile(title: Text('按模型', style: TextStyle(fontWeight: FontWeight.w600))),
                          for (final m in (summary!['by_model'] as List))
                            ListTile(
                              title: Text('${m['model']}'),
                              subtitle: Text('调用 ${m['calls']}'),
                              trailing: Text('${m['tokens']} tok', style: const TextStyle(color: AppColors.sub)),
                            ),
                        ],
                      ),
                    Section(
                      children: [
                        const ListTile(title: Text('明细', style: TextStyle(fontWeight: FontWeight.w600))),
                        if (logs.isEmpty)
                          const Padding(
                            padding: EdgeInsets.fromLTRB(16, 0, 16, 16),
                            child: Text('还没有调用记录', style: TextStyle(color: AppColors.muted)),
                          )
                        else
                          for (final raw in logs)
                            ListTile(
                              title: Text('${raw['model'] ?? '-'} · ${raw['client'] ?? 'app'}'),
                              subtitle: Text(
                                '${(raw['created_at'] as String? ?? '').replaceFirst('T', ' ').split('.').first}'
                                '${raw['status'] == 'ok' ? '' : ' · ${raw['error'] ?? '失败'}'}',
                                maxLines: 2,
                                overflow: TextOverflow.ellipsis,
                              ),
                              trailing: Text('${raw['total_tokens'] ?? 0}', style: const TextStyle(color: AppColors.sub)),
                            ),
                      ],
                    ),
                  ],
                ),
    );
  }
}

class _Metric extends StatelessWidget {
  const _Metric(this.label, this.value);
  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    return Expanded(
      child: Container(
        margin: const EdgeInsets.only(right: 8),
        padding: const EdgeInsets.all(12),
        decoration: BoxDecoration(color: const Color(0xFFF5F5F5), borderRadius: BorderRadius.circular(10)),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(label, style: const TextStyle(fontSize: 12, color: AppColors.sub)),
            const SizedBox(height: 4),
            Text(value, style: const TextStyle(fontSize: 20, fontWeight: FontWeight.w600)),
          ],
        ),
      ),
    );
  }
}
