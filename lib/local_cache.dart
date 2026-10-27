import 'dart:convert';
import 'dart:io';

import 'package:path_provider/path_provider.dart';

/// 本地同步缓存（drift 完整方案前的过渡层）。
/// 按「服务器 + 用户」分文件，启动时可先读本地再拉网。
class LocalSyncCache {
  LocalSyncCache(this.serverUrl, this.userId);

  final String serverUrl;
  final String userId;

  Future<File> _file() async {
    final dir = await getApplicationDocumentsDirectory();
    final safeHost = Uri.tryParse(serverUrl)?.host.replaceAll(RegExp(r'[^a-zA-Z0-9._-]'), '_') ?? 'local';
    final folder = Directory('${dir.path}/chat_cache/$safeHost/$userId');
    if (!await folder.exists()) await folder.create(recursive: true);
    return File('${folder.path}/sync.json');
  }

  Future<void> save(Map<String, dynamic> syncPayload, {String? cursor}) async {
    final f = await _file();
    await f.writeAsString(jsonEncode({
      'saved_at': DateTime.now().toIso8601String(),
      'cursor': cursor ?? DateTime.now().toUtc().toIso8601String(),
      'data': syncPayload,
    }));
  }

  Future<({Map<String, dynamic> data, String? cursor})?> loadWithCursor() async {
    try {
      final f = await _file();
      if (!await f.exists()) return null;
      final raw = jsonDecode(await f.readAsString()) as Map<String, dynamic>;
      final data = raw['data'];
      Map<String, dynamic>? map;
      if (data is Map<String, dynamic>) {
        map = data;
      } else if (data is Map) {
        map = Map<String, dynamic>.from(data);
      }
      if (map == null) return null;
      return (data: map, cursor: raw['cursor'] as String? ?? raw['saved_at'] as String?);
    } catch (_) {}
    return null;
  }

  Future<Map<String, dynamic>?> load() async {
    final hit = await loadWithCursor();
    return hit?.data;
  }

  Future<File> _deletedFile() async {
    final dir = await getApplicationDocumentsDirectory();
    final safeHost = Uri.tryParse(serverUrl)?.host.replaceAll(RegExp(r'[^a-zA-Z0-9._-]'), '_') ?? 'local';
    final folder = Directory('${dir.path}/chat_cache/$safeHost/$userId');
    if (!await folder.exists()) await folder.create(recursive: true);
    return File('${folder.path}/deleted_convs.json');
  }

  Future<void> saveDeletedConvIds(Set<String> ids) async {
    final f = await _deletedFile();
    await f.writeAsString(jsonEncode({'ids': ids.toList()}));
  }

  Future<Set<String>> loadDeletedConvIds() async {
    try {
      final f = await _deletedFile();
      if (!await f.exists()) return {};
      final raw = jsonDecode(await f.readAsString()) as Map<String, dynamic>;
      final list = raw['ids'] as List? ?? const [];
      return {for (final e in list) e.toString()}.where((e) => e.isNotEmpty).toSet();
    } catch (_) {
      return {};
    }
  }
}
