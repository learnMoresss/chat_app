import 'package:flutter/foundation.dart';
import 'package:image/image.dart' as img;
import 'package:path/path.dart' as p;

/// 聊天上传用图片压缩结果。
class CompressedImage {
  CompressedImage({
    required this.bytes,
    required this.filename,
    required this.contentType,
    required this.originalBytes,
  });

  final Uint8List bytes;
  final String filename;
  final String contentType;
  final int originalBytes;

  int get size => bytes.length;
  bool get shrunk => size < originalBytes;
}

class _CompressArgs {
  _CompressArgs({
    required this.raw,
    required this.filename,
    required this.maxSide,
    required this.quality,
    required this.targetBytes,
    required this.hardMaxBytes,
  });

  final Uint8List raw;
  final String filename;
  final int maxSide;
  final int quality;
  final int targetBytes;
  final int hardMaxBytes;
}

/// 公网反向隧道很窄：默认压到约 250KB、长边 1080。
Future<CompressedImage> compressImageForUpload(
  Uint8List raw, {
  required String filename,
  int maxSide = 1080,
  int quality = 62,
  int targetBytes = 250 * 1024,
  int hardMaxBytes = 400 * 1024,
}) async {
  // 小 JPEG 直接过，避免无意义解码
  if (raw.isNotEmpty && raw.length <= targetBytes && _looksLikeJpeg(raw)) {
    return CompressedImage(
      bytes: raw,
      filename: _ensureJpgName(filename),
      contentType: 'image/jpeg',
      originalBytes: raw.length,
    );
  }
  // 丢到后台 isolate，避免主线程卡死导致「一直转圈→超时」
  return compute(
    _compressInIsolate,
    _CompressArgs(
      raw: raw,
      filename: filename,
      maxSide: maxSide,
      quality: quality,
      targetBytes: targetBytes,
      hardMaxBytes: hardMaxBytes,
    ),
  );
}

CompressedImage _compressInIsolate(_CompressArgs a) {
  return compressImageForUploadSync(
    a.raw,
    filename: a.filename,
    maxSide: a.maxSide,
    quality: a.quality,
    targetBytes: a.targetBytes,
    hardMaxBytes: a.hardMaxBytes,
  );
}

/// 同步压缩（供 isolate / 单测）。
CompressedImage compressImageForUploadSync(
  Uint8List raw, {
  required String filename,
  int maxSide = 1080,
  int quality = 62,
  int targetBytes = 250 * 1024,
  int hardMaxBytes = 400 * 1024,
}) {
  final original = raw.length;
  if (raw.isEmpty) {
    return CompressedImage(
      bytes: raw,
      filename: filename,
      contentType: _guessContentType(filename),
      originalBytes: 0,
    );
  }
  if (original <= targetBytes && _looksLikeJpeg(raw)) {
    return CompressedImage(
      bytes: raw,
      filename: _ensureJpgName(filename),
      contentType: 'image/jpeg',
      originalBytes: original,
    );
  }

  try {
    final decoded = img.decodeImage(raw);
    if (decoded == null) {
      return CompressedImage(
        bytes: raw,
        filename: filename,
        contentType: _guessContentType(filename),
        originalBytes: original,
      );
    }
    var image = decoded;
    final w = image.width;
    final h = image.height;
    if (w > maxSide || h > maxSide) {
      image = w >= h
          ? img.copyResize(image, width: maxSide, interpolation: img.Interpolation.linear)
          : img.copyResize(image, height: maxSide, interpolation: img.Interpolation.linear);
    }

    var q = quality;
    var out = Uint8List.fromList(img.encodeJpg(image, quality: q));
    if (out.length > hardMaxBytes) {
      q = 52;
      out = Uint8List.fromList(img.encodeJpg(image, quality: q));
    }
    if (out.length > hardMaxBytes) {
      final side2 = (maxSide * 0.7).round().clamp(640, maxSide);
      image = image.width >= image.height
          ? img.copyResize(image, width: side2, interpolation: img.Interpolation.linear)
          : img.copyResize(image, height: side2, interpolation: img.Interpolation.linear);
      q = 48;
      out = Uint8List.fromList(img.encodeJpg(image, quality: q));
    }
    if (out.length > original && original <= hardMaxBytes) {
      return CompressedImage(
        bytes: raw,
        filename: filename,
        contentType: _guessContentType(filename),
        originalBytes: original,
      );
    }
    return CompressedImage(
      bytes: out,
      filename: _ensureJpgName(filename),
      contentType: 'image/jpeg',
      originalBytes: original,
    );
  } catch (_) {
    return CompressedImage(
      bytes: raw,
      filename: filename,
      contentType: _guessContentType(filename),
      originalBytes: original,
    );
  }
}

bool _looksLikeJpeg(Uint8List b) =>
    b.length >= 3 && b[0] == 0xFF && b[1] == 0xD8 && b[2] == 0xFF;

String _ensureJpgName(String name) {
  final base = p.basenameWithoutExtension(name.isEmpty ? 'image' : name);
  return '$base.jpg';
}

String _guessContentType(String filename) {
  final ext = p.extension(filename).toLowerCase();
  return switch (ext) {
    '.png' => 'image/png',
    '.webp' => 'image/webp',
    '.gif' => 'image/gif',
    '.heic' || '.heif' => 'image/heic',
    _ => 'image/jpeg',
  };
}
