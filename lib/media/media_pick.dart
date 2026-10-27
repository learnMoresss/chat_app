import 'dart:io';
import 'dart:typed_data';

import 'package:file_picker/file_picker.dart';
import 'package:image_picker/image_picker.dart';
import 'package:path/path.dart' as p;

/// 暂存待发媒体（微信：先选图，点发送再上传）。
class StagedMedia {
  StagedMedia({
    required this.path,
    required this.name,
    required this.kind, // image | video | file
    this.bytes,
    this.width,
    this.height,
  });

  final String path;
  final String name;
  final String kind;
  final Uint8List? bytes;
  final int? width;
  final int? height;

  int get size => bytes?.length ?? File(path).lengthSync();
}

class MediaPick {
  MediaPick._();
  static final _picker = ImagePicker();

  static const maxImages = 9;

  /// 相册多选图片（最多 9）。
  static Future<List<StagedMedia>> pickImagesFromGallery({int already = 0}) async {
    final remain = maxImages - already;
    if (remain <= 0) return [];
    final list = await _picker.pickMultiImage(
      imageQuality: 85,
      maxWidth: 2048,
      maxHeight: 2048,
      limit: remain,
    );
    final out = <StagedMedia>[];
    for (final x in list) {
      final bytes = await x.readAsBytes();
      if (bytes.isEmpty) continue;
      out.add(StagedMedia(
        path: x.path,
        name: x.name.isNotEmpty ? x.name : p.basename(x.path),
        kind: 'image',
        bytes: bytes,
      ));
      if (out.length >= remain) break;
    }
    return out;
  }

  static Future<StagedMedia?> takePhoto() async {
    final x = await _picker.pickImage(
      source: ImageSource.camera,
      imageQuality: 85,
      maxWidth: 2048,
      maxHeight: 2048,
    );
    if (x == null) return null;
    final bytes = await x.readAsBytes();
    if (bytes.isEmpty) return null;
    return StagedMedia(
      path: x.path,
      name: x.name.isNotEmpty ? x.name : 'camera.jpg',
      kind: 'image',
      bytes: bytes,
    );
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
}
