import 'dart:io';
import 'dart:typed_data';

import 'package:file_picker/file_picker.dart';
import 'package:image_picker/image_picker.dart';
import 'package:path/path.dart' as p;

import 'image_compress.dart';

/// 暂存待发媒体（微信：先选图，点发送再上传）。
class StagedMedia {
  StagedMedia({
    required this.path,
    required this.name,
    required this.kind, // image | video | file
    this.bytes,
    this.width,
    this.height,
    this.contentType,
  });

  final String path;
  final String name;
  final String kind;
  final Uint8List? bytes;
  final int? width;
  final int? height;
  final String? contentType;

  int get size => bytes?.length ?? File(path).lengthSync();
}

class MediaPick {
  MediaPick._();
  static final _picker = ImagePicker();

  static const maxImages = 9;

  /// 选图后立刻压成上传友好体积（公网链路默认压缩）。
  static Future<StagedMedia> _stageImage(XFile x) async {
    final raw = await x.readAsBytes();
    if (raw.isEmpty) {
      throw StateError('空图片');
    }
    final name = x.name.isNotEmpty ? x.name : p.basename(x.path);
    final compressed = await compressImageForUpload(raw, filename: name);
    return StagedMedia(
      path: x.path,
      name: compressed.filename,
      kind: 'image',
      bytes: compressed.bytes,
      contentType: compressed.contentType,
    );
  }

  /// 相册多选图片（最多 9）。
  static Future<List<StagedMedia>> pickImagesFromGallery({int already = 0}) async {
    final remain = maxImages - already;
    if (remain <= 0) return [];
    final list = await _picker.pickMultiImage(
      // 系统侧先缩到接近上传目标，减轻二次压缩与隧道压力
      imageQuality: 65,
      maxWidth: 1280,
      maxHeight: 1280,
      limit: remain,
    );
    final out = <StagedMedia>[];
    for (final x in list) {
      try {
        out.add(await _stageImage(x));
      } catch (_) {
        continue;
      }
      if (out.length >= remain) break;
    }
    return out;
  }

  static Future<StagedMedia?> takePhoto() async {
    final x = await _picker.pickImage(
      source: ImageSource.camera,
      imageQuality: 65,
      maxWidth: 1280,
      maxHeight: 1280,
    );
    if (x == null) return null;
    try {
      return await _stageImage(x);
    } catch (_) {
      return null;
    }
  }

  static Future<StagedMedia?> pickVideo({bool fromCamera = false}) async {
    final x = await _picker.pickVideo(
      source: fromCamera ? ImageSource.camera : ImageSource.gallery,
      maxDuration: const Duration(minutes: 5),
    );
    if (x == null) return null;
    final bytes = await x.readAsBytes();
    if (bytes.isEmpty) return null;
    return StagedMedia(
      path: x.path,
      name: x.name.isNotEmpty ? x.name : 'video.mp4',
      kind: 'video',
      bytes: bytes,
    );
  }

  static Future<StagedMedia?> pickFile() async {
    final list = await FilePicker.pickFiles();
    if (list.isEmpty) return null;
    final f = list.first;
    final bytes = await f.readAsBytes();
    if (bytes.isEmpty) return null;
    if (bytes.length > 80 * 1024 * 1024) {
      throw StateError('文件不能超过 80MB');
    }
    return StagedMedia(
      path: f.path ?? f.name,
      name: f.name,
      kind: 'file',
      bytes: bytes,
    );
  }

  /// 音色参考：wav / mp3 / m4a / aac / flac。
  static Future<StagedMedia?> pickAudio() async {
    final list = await FilePicker.pickFiles(
      type: FileType.custom,
      allowedExtensions: const ['wav', 'mp3', 'm4a', 'aac', 'flac'],
    );
    if (list.isEmpty) return null;
    final f = list.first;
    final bytes = await f.readAsBytes();
    if (bytes.isEmpty) return null;
    if (bytes.length > 30 * 1024 * 1024) {
      throw StateError('音频不能超过 30MB');
    }
    final name = f.name.toLowerCase();
    var ctype = 'application/octet-stream';
    if (name.endsWith('.wav')) {
      ctype = 'audio/wav';
    } else if (name.endsWith('.mp3')) {
      ctype = 'audio/mpeg';
    } else if (name.endsWith('.m4a') || name.endsWith('.aac')) {
      ctype = 'audio/mp4';
    } else if (name.endsWith('.flac')) {
      ctype = 'audio/flac';
    }
    return StagedMedia(
      path: f.path ?? f.name,
      name: f.name,
      kind: 'audio',
      bytes: bytes,
      contentType: ctype,
    );
  }

  /// 形象参考：单张相册图。
  static Future<StagedMedia?> pickSingleImage() async {
    final x = await _picker.pickImage(
      source: ImageSource.gallery,
      imageQuality: 85,
      maxWidth: 1536,
      maxHeight: 2048,
    );
    if (x == null) return null;
    try {
      return await _stageImage(x);
    } catch (_) {
      return null;
    }
  }
}
