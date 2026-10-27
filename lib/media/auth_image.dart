import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;

import '../api/api_client.dart';
import 'media_cache.dart';

/// 带 Bearer 拉媒体：内存热缓存 + 磁盘持久化（微信式「下次进缓存」）。
///
/// - [load] / [loadOriginal]：原图/原文件
/// - [loadPreview]：气泡用缩略图（有则用 thumb，否则 orig 并异步补 thumb）
class AuthImageCache {
  AuthImageCache._();
  static final map = <String, Uint8List>{};
  static final _previewMap = <String, Uint8List>{};
  static final _inflight = <String, Future<Uint8List?>>{};
  static const _memCap = 48;

  static void _touchMem(String key, Uint8List bytes, Map<String, Uint8List> bucket) {
    bucket.remove(key);
    bucket[key] = bytes;
    while (bucket.length > _memCap) {
      bucket.remove(bucket.keys.first);
    }
  }

  /// 原文件：内存 → 磁盘 → 网络。
  static Future<Uint8List?> load(ApiClient api, String fileId) => loadOriginal(api, fileId);

  static Future<Uint8List?> loadOriginal(ApiClient api, String fileId) {
    final hit = map[fileId];
    if (hit != null && hit.isNotEmpty) return Future.value(hit);
    final pending = _inflight['o:$fileId'];
    if (pending != null) return pending;
    final fut = _loadOriginalOnce(api, fileId);
    _inflight['o:$fileId'] = fut;
    return fut.whenComplete(() => _inflight.remove('o:$fileId'));
  }

  static Future<Uint8List?> loadPreview(ApiClient api, String fileId) {
    final hit = _previewMap[fileId];
    if (hit != null && hit.isNotEmpty) return Future.value(hit);
    final pending = _inflight['p:$fileId'];
    if (pending != null) return pending;
    final fut = _loadPreviewOnce(api, fileId);
    _inflight['p:$fileId'] = fut;
    return fut.whenComplete(() => _inflight.remove('p:$fileId'));
  }

  static Future<Uint8List?> _loadPreviewOnce(ApiClient api, String fileId) async {
    final diskPreview = await MediaDiskCache.loadPreviewBytes(fileId);
    if (diskPreview != null && diskPreview.isNotEmpty) {
      _touchMem(fileId, diskPreview, _previewMap);
      return diskPreview;
    }
    final orig = await _loadOriginalOnce(api, fileId);
    if (orig == null || orig.isEmpty) return null;
    final thumb = await MediaDiskCache.readThumbBytes(fileId);
    final use = (thumb != null && thumb.isNotEmpty) ? thumb : orig;
    _touchMem(fileId, use, _previewMap);
    return use;
  }

  static Future<Uint8List?> _loadOriginalOnce(ApiClient api, String fileId) async {
    final disk = await MediaDiskCache.readOrigBytes(fileId);
    if (disk != null && disk.isNotEmpty) {
      _touchMem(fileId, disk, map);
      return disk;
    }
    if (api.accessToken == null || api.accessToken!.isEmpty) return null;
    final uri = Uri.parse(api.mediaAuthUrl(fileId));
    for (var attempt = 0; attempt < 3; attempt++) {
      final token = api.accessToken;
      if (token == null || token.isEmpty) return null;
      try {
        final res = await http
            .get(uri, headers: {'Authorization': 'Bearer $token'})
            .timeout(const Duration(seconds: 90));
        if (res.statusCode == 401 && attempt < 2) {
          final ok = await api.trySilentRefresh();
          if (!ok) {
            await Future<void>.delayed(Duration(milliseconds: 300 * (attempt + 1)));
          }
          continue;
        }
        if (res.statusCode >= 400 || res.bodyBytes.isEmpty) {
          if (attempt < 2) {
            await Future<void>.delayed(Duration(milliseconds: 350 * (attempt + 1)));
            continue;
          }
          return null;
        }
        if (res.bodyBytes.length < 32) return null;
        final bytes = res.bodyBytes;
        final ctype = res.headers['content-type'];
        _touchMem(fileId, bytes, map);
        await MediaDiskCache.putOrig(fileId, bytes, contentType: ctype);
        return bytes;
      } catch (_) {
        if (attempt < 2) {
          await Future<void>.delayed(Duration(milliseconds: 350 * (attempt + 1)));
          continue;
        }
        return null;
      }
    }
    return null;
  }

  /// 上传后立刻写入内存 + 磁盘，气泡秒开。
  static void put(String fileId, Uint8List bytes, {String? contentType}) {
    _touchMem(fileId, bytes, map);
    // ignore: unawaited_futures
    MediaDiskCache.putOrig(fileId, bytes, contentType: contentType);
  }

  static void invalidate(String fileId) {
    map.remove(fileId);
    _previewMap.remove(fileId);
    _inflight.remove('o:$fileId');
    _inflight.remove('p:$fileId');
  }

  /// 设置页清缓存时一并清内存。
  static void clearMemory() {
    map.clear();
    _previewMap.clear();
    _inflight.clear();
  }
}

/// [preview]=true（默认）走缩略图缓存，适合气泡/头像网格；
/// 全屏看原图传 [preview]=false。
class AuthFileImage extends StatefulWidget {
  const AuthFileImage({
    super.key,
    required this.api,
    required this.fileId,
    this.fit = BoxFit.cover,
    this.placeholder,
    this.preview = true,
    this.decodeWidth,
  });

  final ApiClient? api;
  final String fileId;
  final BoxFit fit;
  final Widget? placeholder;
  final bool preview;
  /// 额外限制解码宽度（逻辑像素×dpr 由调用方换算），气泡建议 360。
  final int? decodeWidth;

  @override
  State<AuthFileImage> createState() => _AuthFileImageState();
}

class _AuthFileImageState extends State<AuthFileImage> {
  Uint8List? _bytes;
  var _loading = true;
  var _failed = false;
  var _gen = 0;
  var _tokenWaitTried = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void didUpdateWidget(covariant AuthFileImage oldWidget) {
    super.didUpdateWidget(oldWidget);
    final tokenChanged = oldWidget.api?.accessToken != widget.api?.accessToken;
    if (oldWidget.fileId != widget.fileId ||
        oldWidget.api != widget.api ||
        oldWidget.preview != widget.preview ||
        tokenChanged) {
      _tokenWaitTried = false;
      _load();
    } else if (_bytes == null && !_loading && widget.api?.accessToken != null) {
      _load();
    }
  }

  Future<void> _load({bool force = false}) async {
    final api = widget.api;
    final fid = widget.fileId;
    if (api == null || fid.isEmpty) {
      if (mounted) {
        setState(() {
          _bytes = null;
          _loading = false;
          _failed = true;
        });
      }
      return;
    }
    if (api.accessToken == null || api.accessToken!.isEmpty) {
      if (mounted) {
        setState(() {
          _bytes = null;
          _loading = false;
          _failed = true;
        });
      }
      if (!_tokenWaitTried) {
        _tokenWaitTried = true;
        Future<void>.delayed(const Duration(milliseconds: 600), () {
          if (!mounted) return;
          if (widget.api?.accessToken != null && widget.api!.accessToken!.isNotEmpty) {
            _load();
          }
        });
      }
      return;
    }
    if (force) AuthImageCache.invalidate(fid);
    final gen = ++_gen;
    if (mounted) {
      setState(() {
        _loading = true;
        _failed = false;
      });
    }
    final b = widget.preview
        ? await AuthImageCache.loadPreview(api, fid)
        : await AuthImageCache.loadOriginal(api, fid);
    if (!mounted || gen != _gen) return;
    setState(() {
      _bytes = b;
      _loading = false;
      _failed = b == null || b.isEmpty;
    });
  }

  @override
  Widget build(BuildContext context) {
    if (_bytes != null && _bytes!.isNotEmpty) {
      final dpr = MediaQuery.devicePixelRatioOf(context);
      final cacheW = widget.decodeWidth != null ? (widget.decodeWidth! * dpr).round() : null;
      return Image.memory(
        _bytes!,
        fit: widget.fit,
        width: double.infinity,
        height: double.infinity,
        gaplessPlayback: true,
        filterQuality: FilterQuality.medium,
        cacheWidth: cacheW,
        errorBuilder: (_, __, ___) {
          WidgetsBinding.instance.addPostFrameCallback((_) {
            AuthImageCache.invalidate(widget.fileId);
            if (mounted) {
              setState(() {
                _bytes = null;
                _failed = true;
                _loading = false;
              });
            }
          });
          return _failedBox(onTap: () => _load(force: true));
        },
      );
    }
    if (_loading) {
      return widget.placeholder ??
          const ColoredBox(
            color: Color(0xFFD8D8D8),
            child: Center(child: SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2))),
          );
    }
    assert(_failed || _bytes == null);
    return widget.placeholder ?? _failedBox(onTap: () => _load(force: true));
  }

  Widget _failedBox({VoidCallback? onTap}) {
    return GestureDetector(
      onTap: onTap,
      behavior: HitTestBehavior.opaque,
      child: const ColoredBox(
        color: Color(0xFFD0D0D0),
        child: Center(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(Icons.broken_image, color: Colors.white70),
              SizedBox(height: 4),
              Text('点击重试', style: TextStyle(color: Colors.white70, fontSize: 11)),
            ],
          ),
        ),
      ),
    );
  }
}
