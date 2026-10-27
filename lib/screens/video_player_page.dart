import 'dart:io';

import 'package:flutter/material.dart';
import 'package:video_player/video_player.dart';

import '../api/api_client.dart';
import '../media/auth_image.dart';
import '../media/media_cache.dart';

/// 全屏播放会话里的视频：优先磁盘缓存，避免每次重新下网。
class VideoPlayerPage extends StatefulWidget {
  const VideoPlayerPage({
    super.key,
    required this.api,
    required this.fileId,
    this.title = '视频',
  });

  final ApiClient api;
  final String fileId;
  final String title;

  @override
  State<VideoPlayerPage> createState() => _VideoPlayerPageState();
}

class _VideoPlayerPageState extends State<VideoPlayerPage> {
  VideoPlayerController? _ctrl;
  String? _error;
  var _loading = true;
  var _fromCache = false;

  @override
  void initState() {
    super.initState();
    _open();
  }

  Future<void> _open() async {
    try {
      File? file = await MediaDiskCache.getOrig(widget.fileId);
      if (file != null) {
        _fromCache = true;
      } else {
        final bytes = await AuthImageCache.loadOriginal(widget.api, widget.fileId);
        if (bytes == null || bytes.isEmpty) {
          if (mounted) {
            setState(() {
              _error = '视频加载失败';
              _loading = false;
            });
          }
          return;
        }
        file = await MediaDiskCache.getOrig(widget.fileId);
        file ??= await MediaDiskCache.putOrig(
          widget.fileId,
          bytes,
          contentType: 'video/mp4',
          makeThumb: false,
        );
        _fromCache = false;
      }
      final ctrl = VideoPlayerController.file(file);
      await ctrl.initialize();
      await ctrl.play();
      if (!mounted) {
        await ctrl.dispose();
        return;
      }
      setState(() {
        _ctrl = ctrl;
        _loading = false;
      });
    } catch (e) {
      if (mounted) {
        setState(() {
          _error = '$e';
          _loading = false;
        });
      }
    }
  }

  @override
  void dispose() {
    _ctrl?.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final ctrl = _ctrl;
    return Scaffold(
      backgroundColor: Colors.black,
      appBar: AppBar(
        backgroundColor: Colors.black,
        foregroundColor: Colors.white,
        title: Text(widget.title),
        actions: [
          if (_fromCache && !_loading && _error == null)
            const Padding(
              padding: EdgeInsets.only(right: 12),
              child: Center(
                child: Text('已缓存', style: TextStyle(color: Colors.white54, fontSize: 12)),
              ),
            ),
        ],
      ),
      body: Center(
        child: _loading
            ? const CircularProgressIndicator(color: Colors.white)
            : _error != null
                ? Text(_error!, style: const TextStyle(color: Colors.white70))
                : ctrl == null || !ctrl.value.isInitialized
                    ? const SizedBox.shrink()
                    : AspectRatio(
                        aspectRatio: ctrl.value.aspectRatio == 0 ? 16 / 9 : ctrl.value.aspectRatio,
                        child: Stack(
                          alignment: Alignment.center,
                          children: [
                            VideoPlayer(ctrl),
                            IconButton(
                              iconSize: 56,
                              color: Colors.white70,
                              onPressed: () {
                                setState(() {
                                  if (ctrl.value.isPlaying) {
                                    ctrl.pause();
                                  } else {
                                    ctrl.play();
                                  }
                                });
                              },
                              icon: Icon(ctrl.value.isPlaying ? Icons.pause_circle : Icons.play_circle),
                            ),
                          ],
                        ),
                      ),
      ),
    );
  }
}
