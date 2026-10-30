import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:gal/gal.dart';

import '../api/api_client.dart';
import '../media/auth_image.dart';
import '../media/media_cache.dart';
import '../models.dart';
import '../store.dart';

class ImageSlide {
  ImageSlide({this.fileId, this.bytes, this.label = '', this.byteSize = 0});
  final String? fileId;
  final Uint8List? bytes;
  final String label;
  final int byteSize;
}

/// 从单条图片消息抽出幻灯片（本地预览优先）。
List<ImageSlide> slidesFromImageMsg(Msg msg) {
  final meta = msg.meta ?? const <String, dynamic>{};
  final previews = meta['local_previews'];
  var files = meta['files'];
  final sizes = meta['sizes'];
  final out = <ImageSlide>[];
  if (previews is List && previews.isNotEmpty) {
    for (final raw in previews) {
      if (raw is Map && raw['bytes'] is List) {
        out.add(ImageSlide(bytes: Uint8List.fromList(List<int>.from(raw['bytes'] as List))));
      }
    }
  }
  // 兜底：meta 丢失时从正文 JSON 再解一次（sync/旧缓存偶发）
  if ((files is! List || files.isEmpty) && msg.text.trim().startsWith('{')) {
    try {
      final decoded = jsonDecode(msg.text);
      if (decoded is Map && decoded['files'] is List) {
        files = decoded['files'];
      }
    } catch (_) {}
  }
  if (out.isEmpty && files is List) {
    for (var i = 0; i < files.length; i++) {
      final id = files[i].toString().trim();
      if (id.isEmpty) continue;
      var sz = 0;
      if (sizes is List && i < sizes.length && sizes[i] is num) {
        sz = (sizes[i] as num).round();
      } else if (msg.bytes > 0 && files.length == 1) {
        sz = msg.bytes;
      }
      out.add(ImageSlide(fileId: id, byteSize: sz));
    }
  }
  if (out.isEmpty) out.add(ImageSlide(label: msg.text));
  return out;
}

/// 会话内全部图片消息按时间序聚合；返回 (幻灯片列表, 当前全局下标)。
({List<ImageSlide> slides, int index}) sessionImageGallery({
  required AppStore store,
  required String convId,
  required Msg current,
  required int localIndex,
}) {
  final all = <ImageSlide>[];
  var global = 0;
  var found = false;
  for (final m in store.msgs(convId)) {
    if (m.kind != MsgKind.image) continue;
    final slides = slidesFromImageMsg(m);
    if (m.id == current.id) {
      global = all.length + localIndex.clamp(0, slides.isEmpty ? 0 : slides.length - 1);
      found = true;
    }
    all.addAll(slides);
  }
  if (all.isEmpty) {
    final fallback = slidesFromImageMsg(current);
    return (slides: fallback, index: localIndex.clamp(0, fallback.isEmpty ? 0 : fallback.length - 1));
  }
  if (!found) global = localIndex.clamp(0, all.length - 1);
  return (slides: all, index: global.clamp(0, all.length - 1));
}

/// 全屏看图：会话级左右滑，双指缩放，存相册；优先磁盘原图。
Future<void> openFullscreenImages(
  BuildContext context, {
  required List<ImageSlide> slides,
  int initialIndex = 0,
  ApiClient? api,
}) {
  if (slides.isEmpty) return Future.value();
  return Navigator.of(context).push(
    PageRouteBuilder(
      opaque: false,
      barrierColor: Colors.black,
      pageBuilder: (_, animation, secondaryAnimation) => _FullscreenImagesPage(
        slides: slides,
        initialIndex: initialIndex.clamp(0, slides.length - 1),
        api: api,
      ),
    ),
  );
}

/// 头像 / 单图预览（有 fileId 或本地 bytes 才打开）。
Future<void> openAvatarPreview(
  BuildContext context, {
  ApiClient? api,
  String? fileId,
  Uint8List? bytes,
  String label = '',
}) {
  final slides = <ImageSlide>[];
  if (bytes != null && bytes.isNotEmpty) {
    slides.add(ImageSlide(bytes: bytes, label: label));
  } else if (fileId != null && fileId.isNotEmpty) {
    slides.add(ImageSlide(fileId: fileId, label: label));
  }
  if (slides.isEmpty) return Future.value();
  return openFullscreenImages(context, slides: slides, api: api);
}

class _FullscreenImagesPage extends StatefulWidget {
  const _FullscreenImagesPage({
    required this.slides,
    required this.initialIndex,
    this.api,
  });
  final List<ImageSlide> slides;
  final int initialIndex;
  final ApiClient? api;

  @override
  State<_FullscreenImagesPage> createState() => _FullscreenImagesPageState();
}

class _FullscreenImagesPageState extends State<_FullscreenImagesPage> {
  late final page = PageController(initialPage: widget.initialIndex);
  late int index = widget.initialIndex;
  var _saving = false;
  /// fileId → 是否已加载原图（相对缩略预览）
  final _origReady = <String, bool>{};
  final _loadingOrig = <String>{};
  final _probing = <String>{};

  @override
  void dispose() {
    page.dispose();
    super.dispose();
  }

  ImageSlide get _cur => widget.slides[index];

  Future<void> _probeCache(String? fileId) async {
    if (fileId == null || fileId.isEmpty) return;
    if (_origReady.containsKey(fileId) || _probing.contains(fileId)) return;
    _probing.add(fileId);
    final has = await MediaDiskCache.hasOrig(fileId);
    if (!mounted) return;
    setState(() {
      _origReady[fileId] = has;
      _probing.remove(fileId);
    });
  }

  Future<void> _loadOriginal(String fileId) async {
    if (widget.api == null || _loadingOrig.contains(fileId)) return;
    setState(() => _loadingOrig.add(fileId));
    try {
      final b = await AuthImageCache.loadOriginal(widget.api!, fileId);
      if (!mounted) return;
      setState(() {
        _origReady[fileId] = b != null && b.isNotEmpty;
        _loadingOrig.remove(fileId);
      });
    } catch (_) {
      if (mounted) setState(() => _loadingOrig.remove(fileId));
    }
  }

  Future<Uint8List?> _bytesOf(ImageSlide s) async {
    if (s.bytes != null) return s.bytes;
    if (s.fileId != null && widget.api != null) {
      return AuthImageCache.loadOriginal(widget.api!, s.fileId!);
    }
    return null;
  }

  Future<void> _save() async {
    if (_saving) return;
    setState(() => _saving = true);
    try {
      final hasAccess = await Gal.hasAccess(toAlbum: true);
      if (!hasAccess) {
        final ok = await Gal.requestAccess(toAlbum: true);
        if (!ok) {
          if (mounted) {
            ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('需要相册写入权限')));
          }
          return;
        }
      }
      final bytes = await _bytesOf(widget.slides[index]);
      if (bytes == null || bytes.isEmpty) {
        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('图片还没加载好')));
        }
        return;
      }
      await Gal.putImageBytes(bytes, name: 'chat_${DateTime.now().millisecondsSinceEpoch}');
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('已保存到相册')));
      }
    } on GalException catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('保存失败：${e.type}')));
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('保存失败：$e')));
      }
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  @override
  void initState() {
    super.initState();
    _probeCache(_cur.fileId);
    // 打开即升原图，避免一直看糊的缩略图
    final fid = _cur.fileId;
    if (fid != null && fid.isNotEmpty) {
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (mounted) unawaited(_loadOriginal(fid));
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final cur = _cur;
    final fid = cur.fileId;
    final origReady = fid == null ? true : (_origReady[fid] ?? false);
    final loadingOrig = fid != null && _loadingOrig.contains(fid);
    final sizeHint = cur.byteSize > 0 ? MediaDiskCache.formatSize(cur.byteSize) : '';

    return AnnotatedRegion<SystemUiOverlayStyle>(
      value: SystemUiOverlayStyle.light,
      child: Scaffold(
        backgroundColor: Colors.black,
        body: Stack(
          children: [
            PageView.builder(
              controller: page,
              itemCount: widget.slides.length,
              onPageChanged: (i) {
                setState(() => index = i);
                _probeCache(widget.slides[i].fileId);
                final nextFid = widget.slides[i].fileId;
                if (nextFid != null && nextFid.isNotEmpty) {
                  unawaited(_loadOriginal(nextFid));
                }
              },
              itemBuilder: (_, i) {
                final s = widget.slides[i];
                Widget child;
                if (s.bytes != null) {
                  child = InteractiveViewer(
                    minScale: 1,
                    maxScale: 4,
                    child: Center(child: Image.memory(s.bytes!, fit: BoxFit.contain)),
                  );
                } else if (s.fileId != null && widget.api != null) {
                  final ready = _origReady[s.fileId!] ?? false;
                  child = InteractiveViewer(
                    minScale: 1,
                    maxScale: 4,
                    child: Center(
                      child: AuthFileImage(
                        api: widget.api,
                        fileId: s.fileId!,
                        fit: BoxFit.contain,
                        // 原图就绪前先出预览，点「查看原图」后再切
                        preview: !ready,
                      ),
                    ),
                  );
                } else {
                  child = const Center(child: Icon(Icons.broken_image, color: Colors.white54, size: 48));
                }
                return child;
              },
            ),
            SafeArea(
              child: Padding(
                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                child: Row(
                  children: [
                    IconButton(
                      onPressed: () => Navigator.pop(context),
                      icon: const Icon(Icons.close, color: Colors.white),
                    ),
                    const Spacer(),
                    Text(
                      '${index + 1} / ${widget.slides.length}',
                      style: const TextStyle(color: Colors.white70, fontSize: 14),
                    ),
                    IconButton(
                      onPressed: _saving ? null : _save,
                      tooltip: '保存到相册',
                      icon: _saving
                          ? const SizedBox(
                              width: 20,
                              height: 20,
                              child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white),
                            )
                          : const Icon(Icons.download, color: Colors.white),
                    ),
                  ],
                ),
              ),
            ),
            // 微信式：预览未升原图时底部「查看原图」（探测中不闪）
            if (fid != null && !origReady && !_probing.contains(fid))
              Positioned(
                left: 0,
                right: 0,
                bottom: 36 + MediaQuery.paddingOf(context).bottom,
                child: Center(
                  child: Material(
                    color: Colors.black54,
                    borderRadius: BorderRadius.circular(4),
                    child: InkWell(
                      onTap: loadingOrig ? null : () => _loadOriginal(fid),
                      borderRadius: BorderRadius.circular(4),
                      child: Padding(
                        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
                        child: loadingOrig
                            ? const SizedBox(
                                width: 18,
                                height: 18,
                                child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white),
                              )
                            : Text(
                                sizeHint.isEmpty ? '查看原图' : '查看原图 ($sizeHint)',
                                style: const TextStyle(color: Colors.white, fontSize: 14),
                              ),
                      ),
                    ),
                  ),
                ),
              ),
            if (fid != null && origReady)
              Positioned(
                left: 0,
                right: 0,
                bottom: 28 + MediaQuery.paddingOf(context).bottom,
                child: const Center(
                  child: Text('原图已缓存', style: TextStyle(color: Colors.white38, fontSize: 11)),
                ),
              ),
          ],
        ),
      ),
    );
  }
}
