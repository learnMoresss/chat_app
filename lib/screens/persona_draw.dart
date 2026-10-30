import 'dart:async';
import 'dart:typed_data';

import 'package:audioplayers/audioplayers.dart';
import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../media/app_audio.dart';
import '../media/auth_image.dart';
import '../media/media_pick.dart';
import '../models.dart';
import '../store.dart';
import '../theme.dart';
import '../widgets.dart';
import 'fullscreen_images.dart';

/// 人设音色 / 形象：每次生成一条，要换就改提示词再生成。
class PersonaDrawPage extends StatefulWidget {
  const PersonaDrawPage({
    super.key,
    required this.store,
    required this.persona,
    this.initialTab = 0,
  });
  final AppStore store;
  final Persona persona;
  /// 0=音色，1=形象
  final int initialTab;

  @override
  State<PersonaDrawPage> createState() => _PersonaDrawPageState();
}

class _PersonaDrawPageState extends State<PersonaDrawPage> with SingleTickerProviderStateMixin {
  late final TabController _tabs;
  final _voicePrompt = TextEditingController(text: '温柔、轻快、略带笑意的年轻女声');
  // 试听文案尽量短：抽卡主要听音色；服务端走常驻 CosyVoice（热态约数秒）
  final _voiceSample = TextEditingController(text: '你好呀，今天过得怎么样？');
  var _voiceWarming = false;
  var _voiceWarmOk = false;
  final _imagePrompt = TextEditingController(
    text: 'solo upper body, anime style, soft natural light, clean background',
  );
  bool _drawingVoice = false;
  bool _drawingImage = false;
  bool _uploadingVoice = false;
  bool _uploadingImage = false;
  String? _err;
  final List<Map<String, dynamic>> _voices = [];
  final List<Map<String, dynamic>> _images = [];
  final _player = AudioPlayer(playerId: 'persona_draw_voice');
  String? _playingId;
  /// 点击后、字节到位前：按钮立刻转圈，避免「点了没反应」。
  String? _loadingPlayId;
  final _voiceListKey = GlobalKey();
  final _imageListKey = GlobalKey();

  ApiClient? get _api => widget.store.api;

  @override
  void initState() {
    super.initState();
    final tab = widget.initialTab.clamp(0, 1);
    _tabs = TabController(length: 2, vsync: this, initialIndex: tab);
    AppAudio.ensureConfigured();
    _load();
    unawaited(_warmVoice());
  }

  Future<void> _warmVoice() async {
    final api = _api;
    if (api == null || _voiceWarming) return;
    setState(() {
      _voiceWarming = true;
    });
    try {
      final info = await api.warmPersonaVoice(widget.persona.id);
      if (!mounted) return;
      setState(() => _voiceWarmOk = info['ok'] == true);
    } catch (_) {
      if (!mounted) return;
      setState(() => _voiceWarmOk = false);
    } finally {
      if (mounted) setState(() => _voiceWarming = false);
    }
  }

  @override
  void dispose() {
    _tabs.dispose();
    _voicePrompt.dispose();
    _voiceSample.dispose();
    _imagePrompt.dispose();
    _player.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    final api = _api;
    if (api == null) return;
    try {
      final all = await api.listPersonaAssets(widget.persona.id);
      final voices = <Map<String, dynamic>>[];
      final images = <Map<String, dynamic>>[];
      for (final raw in all) {
        final m = Map<String, dynamic>.from(raw as Map);
        final k = (m['kind'] as String? ?? '').toLowerCase();
        if (k == 'voice') {
          voices.add(m);
        } else if (k == 'image') {
          images.add(m);
        }
      }
      if (!mounted) return;
      setState(() {
        _voices
          ..clear()
          ..addAll(voices);
        _images
          ..clear()
          ..addAll(images);
        _err = null;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() => _err = '$e');
    }
  }

  Map<String, dynamic> _placeholder({
    required String kind,
    required String prompt,
    required int index,
    required int total,
  }) {
    // index 必须进 id，否则 List.generate 同毫秒会撞 id，后面只更新第一条。
    return {
      'id': 'pending_${kind}_${DateTime.now().microsecondsSinceEpoch}_$index',
      'kind': kind,
      'prompt': prompt,
      'file_id': '',
      'fixed': false,
      '_pending': true,
      '_failed': false,
      '_slot': index + 1,
      '_total': total,
    };
  }

  Future<void> _prefetchVoice(Map<String, dynamic> asset) async {
    final api = _api;
    final fid = asset['file_id'] as String? ?? '';
    if (api == null || fid.isEmpty) return;
    try {
      final cached = await AppAudio.getCache(fid);
      if (cached != null) return;
      final bytes = await AuthImageCache.load(api, fid);
      if (bytes == null || bytes.isEmpty) return;
      await AppAudio.putCache(fid, Uint8List.fromList(bytes));
    } catch (_) {}
  }

  Future<void> _drawVoice() async {
    final api = _api;
    if (api == null || _drawingVoice) return;
    final prompt = _voicePrompt.text.trim();
    if (prompt.isEmpty) {
      setState(() => _err = '请填写音色描述');
      return;
    }
    // 先等预热：冷启在 warm 阶段做完，抽卡请求只付合成时间
    if (!_voiceWarmOk) {
      await _warmVoice();
      if (!mounted) return;
    }
    final sample = _voiceSample.text.trim();
    final batchId = 'v_${DateTime.now().microsecondsSinceEpoch}';
    final slot = _placeholder(kind: 'voice', prompt: prompt, index: 0, total: 1);
    setState(() {
      _drawingVoice = true;
      _err = null;
      _voices.insert(0, slot);
    });
    _tabs.animateTo(0);
    WidgetsBinding.instance.addPostFrameCallback((_) {
      final ctx = _voiceListKey.currentContext;
      if (ctx != null) {
        Scrollable.ensureVisible(ctx, duration: const Duration(milliseconds: 280), alignment: 0.05);
      }
    });

    try {
      // 冷启 Breeze ≈1 分钟 + 合成 ≈20s；勿用短 timeout 把按需启动掐死
      final items = await api.drawPersonaVoice(
            widget.persona.id,
            prompt: prompt,
            count: 1,
            sampleText: sample,
            batchId: batchId,
            slotIndex: 0,
            refPersona: false,
          );
      if (!mounted) return;
      final slotId = slot['id'] as String;
      final idx = _voices.indexWhere((a) => a['id'] == slotId);
      if (items.isNotEmpty) {
        final done = Map<String, dynamic>.from(items.first as Map);
        setState(() {
          if (idx >= 0) {
            _voices[idx] = done;
          } else {
            _voices.insert(0, done);
          }
        });
        unawaited(_prefetchVoice(done));
      } else {
        setState(() {
          if (idx >= 0) {
            _voices[idx] = {..._voices[idx], '_pending': false, '_failed': true};
          }
          _err = '音色生成失败，请稍后重试';
        });
      }
    } catch (e) {
      if (!mounted) return;
      final idx = _voices.indexWhere((a) => a['id'] == slot['id']);
      setState(() {
        if (idx >= 0 && _voices[idx]['_pending'] == true) {
          _voices[idx] = {
            ..._voices[idx],
            '_pending': false,
            '_failed': true,
            '_error': '$e',
          };
        }
        _err = '$e';
      });
    }
    if (mounted) setState(() => _drawingVoice = false);
  }

  Future<void> _drawImage() async {
    final api = _api;
    if (api == null || _drawingImage) return;
    final prompt = _imagePrompt.text.trim();
    if (prompt.isEmpty) {
      setState(() => _err = '请填写形象提示词');
      return;
    }
    final batchId = 'i_${DateTime.now().microsecondsSinceEpoch}';
    final slot = _placeholder(kind: 'image', prompt: prompt, index: 0, total: 1);
    setState(() {
      _drawingImage = true;
      _err = null;
      _images.insert(0, slot);
    });
    _tabs.animateTo(1);
    WidgetsBinding.instance.addPostFrameCallback((_) {
      final ctx = _imageListKey.currentContext;
      if (ctx != null) {
        Scrollable.ensureVisible(ctx, duration: const Duration(milliseconds: 280), alignment: 0.05);
      }
    });

    final slotId = slot['id'] as String;
    try {
      final items = await api
          .drawPersonaImage(
            widget.persona.id,
            prompt: prompt,
            count: 1,
            batchId: batchId,
            slotIndex: 0,
            refPersona: false,
          )
          .timeout(const Duration(seconds: 120));
      if (!mounted) return;
      final idx = _images.indexWhere((a) => a['id'] == slotId);
      if (items.isEmpty) {
        setState(() {
          if (idx >= 0) {
            _images[idx] = {..._images[idx], '_pending': false, '_failed': true};
          }
          _err = '形象生成失败，请稍后重试';
        });
      } else {
        final done = Map<String, dynamic>.from(items.first as Map);
        setState(() {
          if (idx >= 0) {
            _images[idx] = done;
          } else {
            _images.insert(0, done);
          }
        });
        final fid = done['file_id'] as String? ?? '';
        if (fid.isNotEmpty) unawaited(AuthImageCache.load(api, fid));
      }
    } catch (e) {
      if (!mounted) return;
      final idx = _images.indexWhere((a) => a['id'] == slotId);
      setState(() {
        if (idx >= 0) {
          _images[idx] = {
            ..._images[idx],
            '_pending': false,
            '_failed': true,
            '_error': '$e',
          };
        }
        _err = '$e';
      });
    }
    if (mounted) setState(() => _drawingImage = false);
  }

  Future<void> _uploadVoice() async {
    final api = _api;
    if (api == null || _uploadingVoice || _drawingVoice) return;
    StagedMedia? picked;
    try {
      picked = await MediaPick.pickAudio();
    } catch (e) {
      if (!mounted) return;
      setState(() => _err = '$e');
      return;
    }
    if (picked == null || picked.bytes == null || picked.bytes!.isEmpty) return;
    setState(() {
      _uploadingVoice = true;
      _err = null;
    });
    _tabs.animateTo(0);
    try {
      final up = await api.uploadChatMedia(
        bytes: picked.bytes!,
        filename: picked.name,
        kind: 'file',
        contentType: picked.contentType ?? 'audio/wav',
        timeout: const Duration(seconds: 90),
      );
      final fid = (up['file_id'] as String? ?? '').trim();
      if (fid.isEmpty) throw StateError('上传失败');
      final label = _voicePrompt.text.trim();
      final asset = await api.personaAssetFromMedia(
        widget.persona.id,
        kind: 'voice',
        fileId: fid,
        prompt: label.isEmpty ? '上传参考音色' : label,
        sampleText: _voiceSample.text.trim(),
      );
      if (!mounted) return;
      setState(() {
        _voices.insert(0, Map<String, dynamic>.from(asset));
      });
      unawaited(_prefetchVoice(Map<String, dynamic>.from(asset)));
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('已加入音色列表，点「固定」后才会用于聊天')),
      );
    } catch (e) {
      if (!mounted) return;
      setState(() => _err = '$e');
    } finally {
      if (mounted) setState(() => _uploadingVoice = false);
    }
  }

  Future<void> _uploadImage() async {
    final api = _api;
    if (api == null || _uploadingImage || _drawingImage) return;
    final picked = await MediaPick.pickSingleImage();
    if (picked == null || picked.bytes == null || picked.bytes!.isEmpty) return;
    setState(() {
      _uploadingImage = true;
      _err = null;
    });
    _tabs.animateTo(1);
    try {
      final up = await api.uploadChatMedia(
        bytes: picked.bytes!,
        filename: picked.name,
        kind: 'image',
        contentType: picked.contentType ?? 'image/jpeg',
        timeout: const Duration(seconds: 60),
      );
      final fid = (up['file_id'] as String? ?? '').trim();
      if (fid.isEmpty) throw StateError('上传失败');
      final label = _imagePrompt.text.trim();
      final asset = await api.personaAssetFromMedia(
        widget.persona.id,
        kind: 'image',
        fileId: fid,
        prompt: label.isEmpty ? '上传形象' : label,
      );
      if (!mounted) return;
      setState(() {
        _images.insert(0, Map<String, dynamic>.from(asset));
      });
      final outFid = (asset['file_id'] as String? ?? '').trim();
      if (outFid.isNotEmpty) unawaited(AuthImageCache.load(api, outFid));
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('已加入形象列表，点「固定」后才能发自拍/出镜')),
      );
    } catch (e) {
      if (!mounted) return;
      setState(() => _err = '$e');
    } finally {
      if (mounted) setState(() => _uploadingImage = false);
    }
  }

  Future<void> _fix(Map<String, dynamic> asset) async {
    final api = _api;
    if (api == null || asset['_pending'] == true) return;
    final id = asset['id'] as String? ?? '';
    if (id.isEmpty || id.startsWith('pending_')) return;
    try {
      final fixed = await api.fixPersonaAsset(widget.persona.id, id);
      final kind = (fixed['kind'] as String? ?? asset['kind'] as String? ?? '').toLowerCase();
      setState(() {
        if (kind == 'voice') {
          for (var i = 0; i < _voices.length; i++) {
            final a = _voices[i];
            if (a['_pending'] == true) continue;
            _voices[i] = {...a, 'fixed': a['id'] == id};
          }
        } else if (kind == 'image') {
          for (var i = 0; i < _images.length; i++) {
            final a = _images[i];
            if (a['_pending'] == true) continue;
            _images[i] = {...a, 'fixed': a['id'] == id};
          }
          // 形象 ≠ 头像：固定 look 不改 avatarFileId
        }
      });
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(kind == 'voice' ? '已固定音色' : '已固定形象')),
        );
      }
    } catch (e) {
      final msg = '$e';
      if (mounted) {
        if (msg.contains('资产不存在') || msg.contains('404')) {
          // 列表过期：本地摘掉并静默刷新，不吓人
          setState(() {
            _voices.removeWhere((a) => a['id'] == id);
            _images.removeWhere((a) => a['id'] == id);
            _err = null;
          });
          unawaited(_load());
        } else {
          setState(() => _err = msg);
        }
      }
    }
  }

  Future<void> _delete(Map<String, dynamic> asset) async {
    final id = asset['id'] as String? ?? '';
    if (id.isEmpty) return;
    if (asset['_pending'] == true || id.startsWith('pending_')) {
      setState(() {
        _voices.removeWhere((a) => a['id'] == id);
        _images.removeWhere((a) => a['id'] == id);
      });
      return;
    }
    final api = _api;
    if (api == null) return;
    // 先本地移除，避免连点
    setState(() {
      _voices.removeWhere((a) => a['id'] == id);
      _images.removeWhere((a) => a['id'] == id);
    });
    try {
      await api.deletePersonaAsset(widget.persona.id, id);
    } catch (e) {
      final msg = '$e';
      if (mounted && !(msg.contains('资产不存在') || msg.contains('404'))) {
        setState(() => _err = msg);
      }
    }
  }

  Future<void> _playVoice(Map<String, dynamic> asset) async {
    if (asset['_pending'] == true || asset['_failed'] == true) return;
    final api = _api;
    final fid = asset['file_id'] as String? ?? '';
    if (api == null || fid.isEmpty) return;
    final id = asset['id'] as String? ?? fid;
    if (_playingId == id || _loadingPlayId == id) {
      await _player.stop();
      if (mounted) {
        setState(() {
          _playingId = null;
          _loadingPlayId = null;
        });
      }
      return;
    }
    // 立刻反馈，再下文件 / 读缓存
    setState(() {
      _loadingPlayId = id;
      _playingId = null;
      _err = null;
    });
    try {
      await AppAudio.ensureConfigured();
      final cached = await AppAudio.getCache(fid);
      if (cached != null) {
        if (!mounted || _loadingPlayId != id) return;
        setState(() {
          _loadingPlayId = null;
          _playingId = id;
        });
        await AppAudio.playFile(_player, cached);
      } else {
        final bytes = await AuthImageCache.load(api, fid);
        if (!mounted || _loadingPlayId != id) return;
        if (bytes == null || bytes.isEmpty) {
          setState(() {
            _loadingPlayId = null;
            _err = '语音加载失败';
          });
          return;
        }
        final okWav = bytes.length > 12 &&
            bytes[0] == 0x52 &&
            bytes[1] == 0x49 &&
            bytes[2] == 0x46 &&
            bytes[3] == 0x46;
        if (!okWav) {
          setState(() {
            _loadingPlayId = null;
            _err = '语音文件损坏，请重新抽卡';
          });
          return;
        }
        setState(() {
          _loadingPlayId = null;
          _playingId = id;
        });
        await AppAudio.playBytes(
          _player,
          Uint8List.fromList(bytes),
          ext: 'wav',
          mimeType: 'audio/wav',
          cacheFileId: fid,
        );
      }
      _player.onPlayerComplete.first.then((_) {
        if (mounted && _playingId == id) setState(() => _playingId = null);
      });
    } catch (e) {
      if (mounted) {
        setState(() {
          _playingId = null;
          _loadingPlayId = null;
          _err = '播放失败：$e';
        });
      }
    }
  }

  void _openImages(int index) {
    final slides = <ImageSlide>[];
    final mapIndex = <int, int>{}; // list index -> slide index
    for (var i = 0; i < _images.length; i++) {
      final a = _images[i];
      if (a['_pending'] == true || a['_failed'] == true) continue;
      final fid = a['file_id'] as String? ?? '';
      if (fid.isEmpty) continue;
      mapIndex[i] = slides.length;
      slides.add(ImageSlide(fileId: fid, label: a['prompt'] as String? ?? ''));
    }
    if (slides.isEmpty) return;
    final initial = mapIndex[index] ?? 0;
    openFullscreenImages(context, slides: slides, initialIndex: initial, api: _api);
  }

  @override
  Widget build(BuildContext context) {
    final p = widget.persona;
    return Scaffold(
      backgroundColor: AppColors.bg,
      appBar: AppBar(
        title: Text('${p.display} · 抽卡'),
        bottom: TabBar(
          controller: _tabs,
          labelColor: AppColors.green,
          unselectedLabelColor: AppColors.muted,
          indicatorColor: AppColors.green,
          tabs: const [
            Tab(text: '音色'),
            Tab(text: '形象'),
          ],
        ),
      ),
      body: Column(
        children: [
          if (_err != null)
            Material(
              color: const Color(0xFFFFF0F0),
              child: Padding(
                padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
                child: Row(
                  children: [
                    Expanded(child: Text(_err!, style: const TextStyle(color: AppColors.badge, fontSize: 13))),
                    IconButton(
                      icon: const Icon(Icons.close, size: 18),
                      onPressed: () => setState(() => _err = null),
                    ),
                  ],
                ),
              ),
            ),
          Expanded(
            child: TabBarView(
              controller: _tabs,
              children: [
                _VoiceTab(
                  listKey: _voiceListKey,
                  prompt: _voicePrompt,
                  sample: _voiceSample,
                  drawing: _drawingVoice,
                  uploading: _uploadingVoice,
                  assets: _voices,
                  playingId: _playingId,
                  loadingPlayId: _loadingPlayId,
                  warming: _voiceWarming,
                  onDraw: _drawVoice,
                  onUpload: _uploadVoice,
                  onPlay: _playVoice,
                  onFix: _fix,
                  onDelete: _delete,
                ),
                _ImageTab(
                  listKey: _imageListKey,
                  prompt: _imagePrompt,
                  drawing: _drawingImage,
                  uploading: _uploadingImage,
                  assets: _images,
                  api: _api,
                  onDraw: _drawImage,
                  onUpload: _uploadImage,
                  onFix: _fix,
                  onDelete: _delete,
                  onOpen: _openImages,
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _VoiceTab extends StatelessWidget {
  const _VoiceTab({
    required this.listKey,
    required this.prompt,
    required this.sample,
    required this.drawing,
    required this.uploading,
    required this.assets,
    required this.playingId,
    required this.loadingPlayId,
    required this.warming,
    required this.onDraw,
    required this.onUpload,
    required this.onPlay,
    required this.onFix,
    required this.onDelete,
  });

  final Key listKey;
  final TextEditingController prompt;
  final TextEditingController sample;
  final bool drawing;
  final bool uploading;
  final List<Map<String, dynamic>> assets;
  final String? playingId;
  final String? loadingPlayId;
  final bool warming;
  final VoidCallback onDraw;
  final VoidCallback onUpload;
  final Future<void> Function(Map<String, dynamic>) onPlay;
  final Future<void> Function(Map<String, dynamic>) onFix;
  final Future<void> Function(Map<String, dynamic>) onDelete;

  @override
  Widget build(BuildContext context) {
    // 预热不锁按钮；生成中仍保持绿色白字，不用浅灰「禁用」观感
    final locked = drawing || uploading;
    return ListView(
      padding: const EdgeInsets.fromLTRB(0, 4, 0, 24),
      children: [
        Section(
          margin: EdgeInsets.zero,
          children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 10, 16, 6),
            child: TextField(
              controller: prompt,
              maxLines: 2,
              decoration: const InputDecoration(
                labelText: '音色描述',
                border: OutlineInputBorder(),
                isDense: true,
              ),
            ),
          ),
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 0, 16, 6),
            child: TextField(
              controller: sample,
              maxLines: 2,
              decoration: const InputDecoration(
                labelText: '试听文案',
                border: OutlineInputBorder(),
                isDense: true,
              ),
            ),
          ),
          const Padding(
            padding: EdgeInsets.fromLTRB(16, 0, 16, 8),
            child: Text(
              '上传推荐：8～20 秒清晰人声，单人说话、少背景音乐与混响；wav/mp3/m4a 均可。读几句自然口语最好。',
              style: TextStyle(color: AppColors.sub, fontSize: 12, height: 1.35),
            ),
          ),
          if (warming && !drawing)
            const Padding(
              padding: EdgeInsets.fromLTRB(16, 0, 16, 8),
              child: Text(
                '音色引擎预热中，可直接点生成（首次可能稍慢）。',
                style: TextStyle(color: AppColors.sub, fontSize: 12),
              ),
            ),
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 0, 16, 10),
            child: Row(
              children: [
                Expanded(
                  child: SizedBox(
                    height: 44,
                    child: FilledButton(
                      onPressed: locked ? null : onDraw,
                      style: FilledButton.styleFrom(
                        backgroundColor: AppColors.green,
                        foregroundColor: Colors.white,
                        disabledBackgroundColor: AppColors.green,
                        disabledForegroundColor: Colors.white,
                        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
                      ),
                      child: drawing
                          ? const Row(
                              mainAxisAlignment: MainAxisAlignment.center,
                              children: [
                                SizedBox(
                                  width: 16,
                                  height: 16,
                                  child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white),
                                ),
                                SizedBox(width: 8),
                                Text('生成中', style: TextStyle(fontSize: 16, fontWeight: FontWeight.w600, color: Colors.white)),
                              ],
                            )
                          : const Text('生成', style: TextStyle(fontSize: 16, fontWeight: FontWeight.w600, color: Colors.white)),
                    ),
                  ),
                ),
                const SizedBox(width: 10),
                SizedBox(
                  height: 44,
                  child: OutlinedButton(
                    onPressed: locked ? null : onUpload,
                    style: OutlinedButton.styleFrom(
                      foregroundColor: AppColors.green,
                      disabledForegroundColor: AppColors.green,
                      side: const BorderSide(color: AppColors.green),
                      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
                    ),
                    child: uploading
                        ? const SizedBox(
                            width: 18,
                            height: 18,
                            child: CircularProgressIndicator(strokeWidth: 2, color: AppColors.green),
                          )
                        : const Text('上传', style: TextStyle(fontSize: 16, fontWeight: FontWeight.w600)),
                  ),
                ),
              ],
            ),
          ),
        ]),
        const SizedBox(height: 6),
        if (assets.isEmpty)
          const Padding(
            padding: EdgeInsets.fromLTRB(16, 12, 16, 12),
            child: Center(child: Text('还没有音色，可生成或上传一条', style: TextStyle(color: AppColors.muted))),
          )
        else
          KeyedSubtree(
            key: listKey,
            child: Section(
              margin: EdgeInsets.zero,
              children: [
                for (var i = 0; i < assets.length; i++) ...[
                  if (i > 0) const Divider(height: 1, indent: 16),
                  _VoiceTile(
                    asset: assets[i],
                    playing: playingId == (assets[i]['id'] as String?),
                    loading: loadingPlayId == (assets[i]['id'] as String?),
                    onPlay: () => onPlay(assets[i]),
                    onFix: () => onFix(assets[i]),
                    onDelete: () => onDelete(assets[i]),
                  ),
                ],
              ],
            ),
          ),
      ],
    );
  }
}

class _VoiceTile extends StatelessWidget {
  const _VoiceTile({
    required this.asset,
    required this.playing,
    required this.loading,
    required this.onPlay,
    required this.onFix,
    required this.onDelete,
  });
  final Map<String, dynamic> asset;
  final bool playing;
  final bool loading;
  final VoidCallback onPlay;
  final VoidCallback onFix;
  final VoidCallback onDelete;

  /// 标题：用户提示词（不再展示变体抽取文案）。
  String get _featureTitle {
    final prompt = (asset['prompt'] as String?)?.trim() ?? '';
    if (prompt.isNotEmpty) return prompt;
    return '音色';
  }

  @override
  Widget build(BuildContext context) {
    final pending = asset['_pending'] == true;
    final failed = asset['_failed'] == true;
    final fixed = asset['fixed'] == true;
    Widget leading;
    if (pending) {
      leading = const SizedBox(
        width: 44,
        height: 44,
        child: Padding(
          padding: EdgeInsets.all(10),
          child: CircularProgressIndicator(strokeWidth: 2, color: AppColors.green),
        ),
      );
    } else if (failed) {
      leading = const Icon(Icons.error_outline, color: AppColors.badge, size: 28);
    } else if (loading) {
      leading = const SizedBox(
        width: 44,
        height: 44,
        child: Padding(
          padding: EdgeInsets.all(10),
          child: CircularProgressIndicator(strokeWidth: 2.4, color: AppColors.green),
        ),
      );
    } else {
      leading = IconButton(
        iconSize: 36,
        icon: Icon(
          playing ? Icons.stop_circle : Icons.play_circle_filled,
          color: AppColors.green,
        ),
        onPressed: onPlay,
      );
    }
    final statusLine = pending
        ? '生成中…'
        : failed
            ? (asset['_error'] as String? ?? '请重试')
            : loading
                ? '加载中…'
                : playing
                    ? '播放中'
                    : (fixed ? '已固定' : null);
    return ListTile(
      contentPadding: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
      leading: leading,
      title: Text(
        pending
            ? '正在生成…'
            : (failed ? '生成失败' : _featureTitle),
        maxLines: 2,
        overflow: TextOverflow.ellipsis,
        style: const TextStyle(fontSize: 15, height: 1.25),
      ),
      subtitle: statusLine == null
          ? null
          : Text(statusLine, style: const TextStyle(fontSize: 12, color: AppColors.sub), maxLines: 1, overflow: TextOverflow.ellipsis),
      trailing: pending || failed
          ? IconButton(
              tooltip: '移除',
              icon: const Icon(Icons.close, size: 20, color: AppColors.muted),
              onPressed: onDelete,
            )
          : Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                TextButton(
                  onPressed: fixed ? null : onFix,
                  style: TextButton.styleFrom(
                    minimumSize: const Size(52, 36),
                    padding: const EdgeInsets.symmetric(horizontal: 10),
                    tapTargetSize: MaterialTapTargetSize.shrinkWrap,
                  ),
                  child: Text(
                    fixed ? '已固定' : '固定',
                    style: TextStyle(
                      fontSize: 14,
                      fontWeight: FontWeight.w600,
                      color: fixed ? AppColors.muted : AppColors.green,
                    ),
                  ),
                ),
                IconButton(
                  tooltip: '删除',
                  icon: const Icon(Icons.delete_outline, size: 20, color: Color(0xFF888888)),
                  onPressed: onDelete,
                ),
              ],
            ),
    );
  }
}

class _ImageTab extends StatelessWidget {
  const _ImageTab({
    required this.listKey,
    required this.prompt,
    required this.drawing,
    required this.uploading,
    required this.assets,
    required this.api,
    required this.onDraw,
    required this.onUpload,
    required this.onFix,
    required this.onDelete,
    required this.onOpen,
  });

  final Key listKey;
  final TextEditingController prompt;
  final bool drawing;
  final bool uploading;
  final List<Map<String, dynamic>> assets;
  final ApiClient? api;
  final VoidCallback onDraw;
  final VoidCallback onUpload;
  final Future<void> Function(Map<String, dynamic>) onFix;
  final Future<void> Function(Map<String, dynamic>) onDelete;
  final ValueChanged<int> onOpen;

  @override
  Widget build(BuildContext context) {
    final busy = drawing || uploading;
    return ListView(
      padding: const EdgeInsets.fromLTRB(0, 4, 0, 24),
      children: [
        Section(
          margin: EdgeInsets.zero,
          children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 10, 16, 6),
            child: TextField(
              controller: prompt,
              maxLines: 3,
              decoration: const InputDecoration(
                labelText: '形象提示词',
                border: OutlineInputBorder(),
                isDense: true,
              ),
            ),
          ),
          const Padding(
            padding: EdgeInsets.fromLTRB(16, 0, 16, 8),
            child: Text(
              '上传推荐：单人正面或半身、脸清晰、背景干净；竖图约 3:4 更好。不要九宫格/拼贴/多人合影，画风尽量和人设一致。',
              style: TextStyle(color: AppColors.sub, fontSize: 12, height: 1.35),
            ),
          ),
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 0, 16, 10),
            child: Row(
              children: [
                Expanded(
                  child: SizedBox(
                    height: 44,
                    child: FilledButton(
                      onPressed: busy ? null : onDraw,
                      style: FilledButton.styleFrom(
                        backgroundColor: AppColors.green,
                        foregroundColor: Colors.white,
                        disabledBackgroundColor: AppColors.green,
                        disabledForegroundColor: Colors.white,
                        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
                      ),
                      child: drawing
                          ? const Row(
                              mainAxisAlignment: MainAxisAlignment.center,
                              children: [
                                SizedBox(
                                  width: 16,
                                  height: 16,
                                  child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white),
                                ),
                                SizedBox(width: 8),
                                Text('生成中', style: TextStyle(fontSize: 16, fontWeight: FontWeight.w600, color: Colors.white)),
                              ],
                            )
                          : const Text('生成', style: TextStyle(fontSize: 16, fontWeight: FontWeight.w600, color: Colors.white)),
                    ),
                  ),
                ),
                const SizedBox(width: 10),
                SizedBox(
                  height: 44,
                  child: OutlinedButton(
                    onPressed: busy ? null : onUpload,
                    style: OutlinedButton.styleFrom(
                      foregroundColor: AppColors.green,
                      disabledForegroundColor: AppColors.green,
                      side: const BorderSide(color: AppColors.green),
                      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
                    ),
                    child: uploading
                        ? const SizedBox(
                            width: 18,
                            height: 18,
                            child: CircularProgressIndicator(strokeWidth: 2, color: AppColors.green),
                          )
                        : const Text('上传', style: TextStyle(fontSize: 16, fontWeight: FontWeight.w600)),
                  ),
                ),
              ],
            ),
          ),
        ]),
        const SizedBox(height: 6),
        if (assets.isEmpty)
          const Padding(
            padding: EdgeInsets.fromLTRB(16, 12, 16, 12),
            child: Center(
              child: Text(
                '还没有形象。生成或上传后点「固定」，聊天里才能发自拍/出镜。',
                textAlign: TextAlign.center,
                style: TextStyle(color: AppColors.muted, height: 1.35),
              ),
            ),
          )
        else
          KeyedSubtree(
            key: listKey,
            child: Padding(
              padding: const EdgeInsets.fromLTRB(12, 0, 12, 8),
              child: LayoutBuilder(
                builder: (context, c) {
                  const gap = 10.0;
                  final cell = (c.maxWidth - gap) / 2;
                  return Wrap(
                    spacing: gap,
                    runSpacing: gap + 4,
                    children: [
                      for (var i = 0; i < assets.length; i++)
                        SizedBox(
                          width: cell,
                          child: _ImageCard(
                            asset: assets[i],
                            api: api,
                            onFix: () => onFix(assets[i]),
                            onDelete: () => onDelete(assets[i]),
                            onTap: () => onOpen(i),
                          ),
                        ),
                    ],
                  );
                },
              ),
            ),
          ),
      ],
    );
  }
}

class _ImageCard extends StatelessWidget {
  const _ImageCard({
    required this.asset,
    required this.api,
    required this.onFix,
    required this.onDelete,
    required this.onTap,
  });
  final Map<String, dynamic> asset;
  final ApiClient? api;
  final VoidCallback onFix;
  final VoidCallback onDelete;
  final VoidCallback onTap;

  String get _variant {
    final meta = asset['meta'];
    if (meta is Map) return meta['variant'] as String? ?? '';
    return '';
  }

  @override
  Widget build(BuildContext context) {
    final fid = asset['file_id'] as String? ?? '';
    final fixed = asset['fixed'] == true;
    final pending = asset['_pending'] == true;
    final failed = asset['_failed'] == true;
    final slot = asset['_slot'];
    final total = asset['_total'];
    final variant = _variant;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        AspectRatio(
          aspectRatio: 3 / 4,
          child: Material(
            color: const Color(0xFFE8E8E8),
            borderRadius: BorderRadius.circular(10),
            clipBehavior: Clip.antiAlias,
            child: InkWell(
              onTap: pending || failed || fid.isEmpty ? null : onTap,
              child: Stack(
                fit: StackFit.expand,
                children: [
                  if (pending)
                    ColoredBox(
                      color: const Color(0xFFF2F2F2),
                      child: Column(
                        mainAxisAlignment: MainAxisAlignment.center,
                        children: [
                          const SizedBox(
                            width: 28,
                            height: 28,
                            child: CircularProgressIndicator(color: AppColors.green, strokeWidth: 2.4),
                          ),
                          const SizedBox(height: 10),
                          Text(
                            slot is int && total is int ? '生成中 $slot/$total' : '生成中…',
                            style: const TextStyle(color: AppColors.muted, fontSize: 12),
                          ),
                        ],
                      ),
                    )
                  else if (failed)
                    const ColoredBox(
                      color: Color(0xFFF7F0F0),
                      child: Center(
                        child: Column(
                          mainAxisSize: MainAxisSize.min,
                          children: [
                            Icon(Icons.broken_image_outlined, color: AppColors.badge, size: 36),
                            SizedBox(height: 6),
                            Text('生成失败', style: TextStyle(color: AppColors.badge, fontSize: 12)),
                          ],
                        ),
                      ),
                    )
                  else if (api != null && fid.isNotEmpty)
                    AuthFileImage(api: api, fileId: fid)
                  else
                    const SizedBox.shrink(),
                  if (variant.isNotEmpty)
                    Positioned(
                      left: 6,
                      bottom: 6,
                      child: Container(
                        padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                        decoration: BoxDecoration(
                          color: Colors.black.withValues(alpha: 0.55),
                          borderRadius: BorderRadius.circular(4),
                        ),
                        child: Text(variant, style: const TextStyle(color: Colors.white, fontSize: 11)),
                      ),
                    ),
                  if (fixed)
                    Positioned(
                      top: 6,
                      left: 6,
                      child: Container(
                        padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                        decoration: BoxDecoration(color: AppColors.green, borderRadius: BorderRadius.circular(4)),
                        child: const Text('已固定', style: TextStyle(color: Colors.white, fontSize: 11)),
                      ),
                    ),
                ],
              ),
            ),
          ),
        ),
        const SizedBox(height: 6),
        if (pending)
          const SizedBox(height: 36)
        else
          Row(
            children: [
              Expanded(
                child: SizedBox(
                  height: 34,
                  child: OutlinedButton(
                    onPressed: failed || fixed ? null : onFix,
                    style: OutlinedButton.styleFrom(
                      foregroundColor: AppColors.green,
                      disabledForegroundColor: AppColors.muted,
                      side: BorderSide(color: fixed || failed ? AppColors.line : AppColors.green),
                      padding: EdgeInsets.zero,
                      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(6)),
                    ),
                    child: Text(
                      fixed ? '已固定' : '固定',
                      style: const TextStyle(fontSize: 13, fontWeight: FontWeight.w600),
                    ),
                  ),
                ),
              ),
              const SizedBox(width: 8),
              Expanded(
                child: SizedBox(
                  height: 34,
                  child: OutlinedButton(
                    onPressed: onDelete,
                    style: OutlinedButton.styleFrom(
                      foregroundColor: const Color(0xFF666666),
                      side: const BorderSide(color: AppColors.line),
                      padding: EdgeInsets.zero,
                      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(6)),
                    ),
                    child: const Text('删除', style: TextStyle(fontSize: 13, fontWeight: FontWeight.w500)),
                  ),
                ),
              ),
            ],
          ),
      ],
    );
  }
}
