import 'dart:convert';

String two(int n) => n.toString().padLeft(2, '0');

/// 会话列表 preview：旧缓存可能存了媒体 JSON，展示前洗成可读文案。
String readableConvPreview(String raw) {
  final s = raw.trim();
  if (!s.startsWith('{')) return s;
  try {
    final m = jsonDecode(s);
    if (m is! Map) return s;
    final files = m['files'];
    final cap = (m['caption']?.toString() ?? '').trim();
    final n = files is List ? files.length : 0;
    final kindExtra = (m['kind_extra']?.toString() ?? '').toLowerCase();
    final hasAsr = (m['asr']?.toString() ?? '').trim().isNotEmpty;
    final sec = int.tryParse('${m['seconds'] ?? ''}') ?? 0;
    // 语音 / 唱歌 JSON 也带 files，不能一律当成图片
    final card = (m['card']?.toString() ?? '').toLowerCase();
    final looksVoice = kindExtra == 'sing' || card == 'sing_track' || hasAsr || m.containsKey('seconds');
    if (looksVoice && (n > 0 || m.containsKey('seconds'))) {
      if (kindExtra == 'sing' || card == 'sing_track') {
        final title = (m['title']?.toString() ?? m['caption']?.toString() ?? '').trim();
        final base = title.isNotEmpty ? '[音乐] $title' : '[音乐]';
        return sec > 0 ? '$base $sec"' : base;
      }
      return sec > 0 ? '[语音] $sec"' : '[语音]';
    }
    if (n > 0) {
      final base = n > 1 ? '[图片×$n]' : '[图片]';
      return cap.isEmpty ? base : '$base $cap';
    }
    if (m['job_id'] != null || m['url'] != null) {
      final p = (m['preview']?.toString() ?? '').trim();
      return p.isEmpty ? '[链接]' : p;
    }
  } catch (_) {}
  return s;
}

String hm(DateTime t) => '${two(t.hour)}:${two(t.minute)}';

DateTime dateOnly(DateTime d) => DateTime(d.year, d.month, d.day);

DateTime startOfWeek(DateTime d) {
  final day = dateOnly(d);
  return day.subtract(Duration(days: day.weekday - 1));
}

const _weeks = ['星期一', '星期二', '星期三', '星期四', '星期五', '星期六', '星期日'];

/// 聊天里两条消息之间的时间条。
String formatChatTime(DateTime t, [DateTime? now]) {
  final n = now ?? DateTime.now();
  final local = t.toLocal();
  if (dateOnly(local) == dateOnly(n)) return hm(local);
  if (dateOnly(local) == dateOnly(n).subtract(const Duration(days: 1))) return '昨天 ${hm(local)}';
  if (startOfWeek(local) == startOfWeek(n)) return '${_weeks[local.weekday - 1]} ${hm(local)}';
  if (local.year == n.year) return '${local.month}月${local.day}日 ${hm(local)}';
  return '${local.year}年${local.month}月${local.day}日 ${hm(local)}';
}

/// 会话列表右上角，不带钟点（今天除外）。
String formatListTime(DateTime t, [DateTime? now]) {
  final n = now ?? DateTime.now();
  final local = t.toLocal();
  if (dateOnly(local) == dateOnly(n)) return hm(local);
  if (dateOnly(local) == dateOnly(n).subtract(const Duration(days: 1))) return '昨天';
  if (startOfWeek(local) == startOfWeek(n)) return _weeks[local.weekday - 1];
  if (local.year == n.year) return '${local.month}月${local.day}日';
  return '${local.year}/${local.month}/${local.day}';
}

/// 朋友圈：刚刚、几分钟前、几小时前、昨天、日期。
String formatMomentTime(DateTime t, [DateTime? now]) {
  final n = now ?? DateTime.now();
  final diff = n.difference(t);
  if (diff.inMinutes < 1) return '刚刚';
  if (diff.inMinutes < 60) return '${diff.inMinutes}分钟前';
  if (diff.inHours < 24 && dateOnly(t) == dateOnly(n)) return '${diff.inHours}小时前';
  if (dateOnly(t) == dateOnly(n).subtract(const Duration(days: 1))) return '昨天 ${hm(t)}';
  if (t.year == n.year) return '${t.month}月${t.day}日';
  return '${t.year}年${t.month}月${t.day}日';
}

bool gapNeedsTime(DateTime older, DateTime newer) => newer.difference(older).inMinutes >= 5;

const _letterOf = {'林': 'L', '苏': 'S', '深': 'S', '阿': 'A', '陈': 'C', '周': 'Z', '安': 'A'};

String letterOf(String name) {
  if (name.isEmpty) return '#';
  final c = name.substring(0, 1);
  if (RegExp(r'[A-Za-z]').hasMatch(c)) return c.toUpperCase();
  return _letterOf[c] ?? '#';
}

String fileSize(int bytes) {
  if (bytes < 1024) return '$bytes B';
  if (bytes < 1024 * 1024) return '${(bytes / 1024).toStringAsFixed(0)} KB';
  return '${(bytes / 1024 / 1024).toStringAsFixed(1)} MB';
}
