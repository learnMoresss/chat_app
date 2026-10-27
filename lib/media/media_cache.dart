import 'dart:io';
import 'dart:typed_data';
import 'dart:ui' as ui;

import 'package:path_provider/path_provider.dart';

/// 聊天媒体磁盘缓存（图片 / 视频 / 通用文件）。
///
/// 分层：
/// - `orig`：服务端原文件（下次直接读盘，不再下网）
/// - `thumb`：本地生成的预览图（气泡/列表优先，省解码内存）
///
/// 与 [AuthImageCache] 配合：内存热缓存 + 本目录持久化。
class MediaDiskCache {
  MediaDiskCache._();

  static Directory? _root;
  static const _thumbMaxEdge = 720;

  static Future<Directory> root() async {
    if (_root != null) return _root!;
    final base = await getApplicationSupportDirectory();
    final d = Directory('${base.path}/chat_media_cache');
    if (!await d.exists()) await d.create(recursive: true);
    _root = d;
    return d;
  }

  static String _safe(String fileId) => fileId.replaceAll(RegExp(r'[^a-zA-Z0-9_-]'), '_');

  static Future<File> origFile(String fileId, {String ext = 'bin'}) async {
    final dir = await root();
    return File('${dir.path}/o_${_safe(fileId)}.$ext');
  }

  static Future<File> thumbFile(String fileId) async {
    final dir = await root();
    return File('${dir.path}/t_${_safe(fileId)}.png');
  }

  /// 已缓存的原文件路径（存在且非空）。
  static Future<File?> getOrig(String fileId) async {
    for (final ext in const ['bin', 'jpg', 'jpeg', 'png', 'webp', 'gif', 'mp4', 'mov', 'wav', 'm4a']) {
      final f = await origFile(fileId, ext: ext);
      if (await f.exists() && await f.length() > 32) return f;
    }
    // 兼容无扩展名探测：扫目录前缀
    final dir = await root();
    final prefix = 'o_${_safe(fileId)}.';
    await for (final e in dir.list()) {
      if (e is File && e.path.split(Platform.pathSeparator).last.startsWith(prefix)) {
        if (await e.length() > 32) return e;
      }
    }
    return null;
  }

  static Future<File?> getThumb(String fileId) async {
    final f = await thumbFile(fileId);
    if (await f.exists() && await f.length() > 32) return f;
    return null;
  }

  static Future<Uint8List?> readOrigBytes(String fileId) async {
    final f = await getOrig(fileId);
    if (f == null) return null;
    return f.readAsBytes();
  }

  static Future<Uint8List?> readThumbBytes(String fileId) async {
    final f = await getThumb(fileId);
    if (f == null) return null;
    return f.readAsBytes();
  }

  static String _guessExt(Uint8List bytes, {String? hint}) {
    if (hint != null && hint.isNotEmpty) {
      final h = hint.toLowerCase();
      if (h.contains('mp4') || h.contains('video')) return 'mp4';
      if (h.contains('png')) return 'png';
      if (h.contains('webp')) return 'webp';
      if (h.contains('gif')) return 'gif';
      if (h.contains('wav')) return 'wav';
      if (h.contains('jpeg') || h.contains('jpg') || h.contains('image')) return 'jpg';
    }
    if (bytes.length >= 3 && bytes[0] == 0xFF && bytes[1] == 0xD8 && bytes[2] == 0xFF) return 'jpg';
    if (bytes.length >= 8 && bytes[0] == 0x89 && bytes[1] == 0x50) return 'png';
    if (bytes.length >= 4 && bytes[0] == 0x47 && bytes[1] == 0x49) return 'gif';
    if (bytes.length >= 12 && bytes[4] == 0x66 && bytes[5] == 0x74 && bytes[6] == 0x79 && bytes[7] == 0x70) {
      return 'mp4';
    }
    return 'bin';
  }

  /// 写入原图/原文件；图片会异步补一份 thumb。
  static Future<File> putOrig(
    String fileId,
    Uint8List bytes, {
    String? contentType,
    bool makeThumb = true,
  }) async {
    final ext = _guessExt(bytes, hint: contentType);
    final f = await origFile(fileId, ext: ext);
    await f.writeAsBytes(bytes, flush: true);
    if (makeThumb && _looksLikeImage(ext, contentType)) {
      // 不阻塞主路径
      // ignore: unawaited_futures
      _ensureThumb(fileId, bytes);
    }
    return f;
  }

  static bool _looksLikeImage(String ext, String? contentType) {
    if (contentType != null && contentType.startsWith('image/')) return true;
    return const {'jpg', 'jpeg', 'png', 'webp', 'gif'}.contains(ext);
  }

  static Future<void> _ensureThumb(String fileId, Uint8List bytes) async {
    try {
      final existing = await getThumb(fileId);
      if (existing != null) return;
      final thumb = await encodeThumbPng(bytes, maxEdge: _thumbMaxEdge);
      if (thumb == null || thumb.isEmpty) return;
      final f = await thumbFile(fileId);
      await f.writeAsBytes(thumb, flush: true);
    } catch (_) {}
  }

  /// 气泡优先：有 thumb 用 thumb，否则退回 orig 字节（再触发补 thumb）。
  static Future<Uint8List?> loadPreviewBytes(String fileId) async {
    final t = await readThumbBytes(fileId);
    if (t != null && t.isNotEmpty) return t;
    final o = await readOrigBytes(fileId);
    if (o == null || o.isEmpty) return null;
    // ignore: unawaited_futures
    _ensureThumb(fileId, o);
    return o;
  }

  /// 全屏 / 保存：只要原文件。
  static Future<Uint8List?> loadOrigBytes(String fileId) => readOrigBytes(fileId);

  static Future<bool> hasOrig(String fileId) async => (await getOrig(fileId)) != null;

  static Future<bool> hasThumbOnly(String fileId) async {
    final t = await getThumb(fileId);
    if (t == null) return false;
    return !(await hasOrig(fileId));
  }

  static Future<int> totalBytes() async {
    final dir = await root();
    var sum = 0;
    await for (final e in dir.list(recursive: true, followLinks: false)) {
      if (e is File) {
        try {
          sum += await e.length();
        } catch (_) {}
      }
    }
    return sum;
  }

  static Future<({int files, int bytes})> stats() async {
    final dir = await root();
    var files = 0;
    var bytes = 0;
    await for (final e in dir.list(recursive: true, followLinks: false)) {
      if (e is File) {
        files++;
        try {
          bytes += await e.length();
        } catch (_) {}
      }
    }
    return (files: files, bytes: bytes);
  }

  /// 清聊天媒体缓存（不含音频目录 / sync.json）。
  static Future<void> clear() async {
    final dir = await root();
    if (await dir.exists()) {
      await dir.delete(recursive: true);
    }
    _root = null;
    await root();
  }

  /// 顺带清音频临时缓存目录（与 [AppAudio] 共用策略入口）。
  static Future<int> clearAudioCache() async {
    final tmp = await getTemporaryDirectory();
    final d = Directory('${tmp.path}/chat_audio_cache');
    var freed = 0;
    if (await d.exists()) {
      await for (final e in d.list(followLinks: false)) {
        if (e is File) {
          try {
            freed += await e.length();
          } catch (_) {}
        }
      }
      await d.delete(recursive: true);
    }
    return freed;
  }

  static Future<int> audioCacheBytes() async {
    final tmp = await getTemporaryDirectory();
    final d = Directory('${tmp.path}/chat_audio_cache');
    if (!await d.exists()) return 0;
    var sum = 0;
    await for (final e in d.list(followLinks: false)) {
      if (e is File) {
        try {
          sum += await e.length();
        } catch (_) {}
      }
    }
    return sum;
  }

  static String formatSize(int bytes) {
    if (bytes < 1024) return '$bytes B';
    if (bytes < 1024 * 1024) return '${(bytes / 1024).toStringAsFixed(1)} KB';
    if (bytes < 1024 * 1024 * 1024) return '${(bytes / (1024 * 1024)).toStringAsFixed(1)} MB';
    return '${(bytes / (1024 * 1024 * 1024)).toStringAsFixed(2)} GB';
  }
}

/// 本地缩略图：最长边 [maxEdge]，PNG 输出（无额外依赖）。
Future<Uint8List?> encodeThumbPng(Uint8List bytes, {int maxEdge = 720}) async {
  try {
    final codec = await ui.instantiateImageCodec(bytes);
    final frame = await codec.getNextFrame();
    final src = frame.image;
    final w = src.width;
    final h = src.height;
    if (w <= 0 || h <= 0) {
      src.dispose();
      return null;
    }
    final long = w > h ? w : h;
    if (long <= maxEdge) {
      final bd = await src.toByteData(format: ui.ImageByteFormat.png);
      src.dispose();
      return bd?.buffer.asUint8List();
    }
    final scale = maxEdge / long;
    final tw = (w * scale).round().clamp(1, maxEdge);
    final th = (h * scale).round().clamp(1, maxEdge);
    final recorder = ui.PictureRecorder();
    final canvas = ui.Canvas(recorder);
    canvas.drawImageRect(
      src,
      ui.Rect.fromLTWH(0, 0, w.toDouble(), h.toDouble()),
      ui.Rect.fromLTWH(0, 0, tw.toDouble(), th.toDouble()),
      ui.Paint()..filterQuality = ui.FilterQuality.medium,
    );
    final picture = recorder.endRecording();
    final out = await picture.toImage(tw, th);
    picture.dispose();
    src.dispose();
    final bd = await out.toByteData(format: ui.ImageByteFormat.png);
    out.dispose();
    return bd?.buffer.asUint8List();
  } catch (_) {
    return null;
  }
}
