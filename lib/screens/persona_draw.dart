import 'dart:async';
import 'dart:typed_data';

import 'package:audioplayers/audioplayers.dart';
import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../media/app_audio.dart';
import '../media/auth_image.dart';
import '../models.dart';
import '../store.dart';
import '../theme.dart';
import '../widgets.dart';
import 'fullscreen_images.dart';

/// 人设抽卡：音色 / 形象。多抽时先出 N 条 loading，出一条就能听/看。
class PersonaDrawPage extends StatefulWidget {
  const PersonaDrawPage({super.key, required this.store, required this.persona});
  final AppStore store;
  final Persona persona;

  @override
  State<PersonaDrawPage> createState() => _PersonaDrawPageState();
}

class _PersonaDrawPageState extends State<PersonaDrawPage> with SingleTickerProviderStateMixin {
  late final TabController _tabs;
  final _voicePrompt = TextEditingController(text: '温柔、轻快、略带笑意的年轻女声');
  final _voiceSample = TextEditingController(text: '你好呀，我是你的专属陪伴，今天想听我说点什么？');
  final _imagePrompt = TextEditingController(
    text: '单人半身肖像，只有一个人，禁止拼贴多人，自然光，干净背景，动漫风格',
  );
  int _voiceCount = 3;
  int _imageCount = 3;
  /// 默认开启：抽卡锚定人设（性别/简介），形象抽「长什么样」。
  bool _refPersona = true;
  bool _drawingVoice = false;
  bool _drawingImage = false;
  String? _err;
  final List<Map<String, dynamic>> _voices = [];
  final List<Map<String, dynamic>> _images = [];
  final _player = AudioPlayer(playerId: 'persona_draw_voice');
  String? _playingId;
  /// 点击后、字节到位前：按钮立刻转圈，避免「点了没反应」。
  String? _loadingPlayId;

  ApiClient? get _api => widget.store.api;

  @override
  void initState() {
    super.initState();
    _tabs = TabController(length: 2, vsync: this);
    AppAudio.ensureConfigured();
    _load();
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
    final n = _voiceCount;
    final sample = _voiceSample.text.trim();
    final batchId = 'v_${DateTime.now().microsecondsSinceEpoch}';
    final slots = List.generate(
      n,
      (i) => _placeholder(kind: 'voice', prompt: prompt, index: i, total: n),
    );
    setState(() {
      _drawingVoice = true;
      _err = null;
      _voices.insertAll(0, slots);
    });
    _tabs.animateTo(0);

    for (var i = 0; i < n; i++) {
      final slotId = slots[i]['id'] as String;
      try {
        final items = await api.drawPersonaVoice(
          widget.persona.id,
          prompt: prompt,
          count: 1,
          sampleText: sample,
          batchId: batchId,
          slotIndex: i,
          refPersona: _refPersona,
        );
        if (!mounted) return;
        if (items.isEmpty) {
          setState(() {
            final idx = _voices.indexWhere((a) => a['id'] == slotId);
            if (idx >= 0) {
              _voices[idx] = {..._voices[idx], '_pending': false, '_failed': true};
            }
          });
          continue;
        }
        final done = Map<String, dynamic>.from(items.first as Map);
        setState(() {
          final idx = _voices.indexWhere((a) => a['id'] == slotId);
          if (idx >= 0) {
            _voices[idx] = done;
          } else {
            _voices.insert(0, done);
          }
        });
        unawaited(_prefetchVoice(done));
      } catch (e) {
        if (!mounted) return;
        setState(() {
          final idx = _voices.indexWhere((a) => a['id'] == slotId);
          if (idx >= 0) {
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
    final n = _imageCount;
    final batchId = 'i_${DateTime.now().microsecondsSinceEpoch}';
    final slots = List.generate(
      n,
      (i) => _placeholder(kind: 'image', prompt: prompt, index: i, total: n),
    );
    setState(() {
      _drawingImage = true;
      _err = null;
      _images.insertAll(0, slots);
    });
    _tabs.animateTo(1);

    for (var i = 0; i < n; i++) {
      final slotId = slots[i]['id'] as String;
      try {
        final items = await api.drawPersonaImage(
          widget.persona.id,
          prompt: prompt,
          count: 1,
          batchId: batchId,
          slotIndex: i,
          refPersona: _refPersona,
        );
        if (!mounted) return;
        if (items.isEmpty) {
          setState(() {
            final idx = _images.indexWhere((a) => a['id'] == slotId);
            if (idx >= 0) {
              _images[idx] = {..._images[idx], '_pending': false, '_failed': true};
            }
          });
          continue;
        }
        final done = Map<String, dynamic>.from(items.first as Map);
        setState(() {
          final idx = _images.indexWhere((a) => a['id'] == slotId);
          if (idx >= 0) {
            _images[idx] = done;
          } else {
            _images.insert(0, done);
          }
        });
        // 预热图缓存，格子里尽快出图
        final fid = done['file_id'] as String? ?? '';
        if (fid.isNotEmpty) unawaited(AuthImageCache.load(api, fid));
      } catch (e) {
        if (!mounted) return;
        setState(() {
          final idx = _images.indexWhere((a) => a['id'] == slotId);
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
    }
    if (mounted) setState(() => _drawingImage = false);
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
          final fid = fixed['file_id'] as String? ?? '';
          if (fid.isNotEmpty) {
            widget.persona.avatarFileId = fid;
            widget.store.ping();
          }
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
    final drawing = _drawingVoice || _drawingImage;
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
          if (drawing) const LinearProgressIndicator(minHeight: 2, color: AppColors.green),
          Expanded(
            child: TabBarView(
              controller: _tabs,
              children: [
                _VoiceTab(
                  prompt: _voicePrompt,
                  sample: _voiceSample,
                  count: _voiceCount,
                  drawing: _drawingVoice,
                  assets: _voices,
                  playingId: _playingId,
                  loadingPlayId: _loadingPlayId,
                  onCount: (v) => setState(() => _voiceCount = v),
                  onDraw: _drawVoice,
                  onPlay: _playVoice,
                  onFix: _fix,
                  onDelete: _delete,
                ),
                _ImageTab(
                  prompt: _imagePrompt,
                  count: _imageCount,
                  drawing: _drawingImage,
                  assets: _images,
                  api: _api,
                  refPersona: _refPersona,
                  personaName: widget.persona.display,
                  onCount: (v) => setState(() => _imageCount = v),
                  onRefPersona: (v) => setState(() => _refPersona = v),
                  onDraw: _drawImage,
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
    required this.prompt,
    required this.sample,
    required this.count,
    required this.drawing,
    required this.assets,
    required this.playingId,
    required this.loadingPlayId,
    required this.onCount,
    required this.onDraw,
    required this.onPlay,
    required this.onFix,
    required this.onDelete,
  });

  final TextEditingController prompt;
  final TextEditingController sample;
  final int count;
  final bool drawing;
  final List<Map<String, dynamic>> assets;
  final String? playingId;
  final String? loadingPlayId;
  final ValueChanged<int> onCount;
  final VoidCallback onDraw;
  final Future<void> Function(Map<String, dynamic>) onPlay;
  final Future<void> Function(Map<String, dynamic>) onFix;
  final Future<void> Function(Map<String, dynamic>) onDelete;

  @override
  Widget build(BuildContext context) {
    return ListView(
      padding: const EdgeInsets.fromLTRB(0, 12, 0, 32),
      children: [
        Section(children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 12, 16, 8),
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
            padding: const EdgeInsets.fromLTRB(16, 0, 16, 8),
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
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 0, 16, 12),
            child: Row(
              children: [
                const Text('条数', style: TextStyle(color: AppColors.muted)),
                const SizedBox(width: 12),
                for (final n in [1, 2, 3, 4])
                  Padding(
                    padding: const EdgeInsets.only(right: 8),
                    child: ChoiceChip(
                      label: Text('$n'),
                      selected: count == n,
                      onSelected: drawing ? null : (_) => onCount(n),
                    ),
                  ),
                const Spacer(),
                FilledButton(
                  onPressed: drawing ? null : onDraw,
                  style: FilledButton.styleFrom(backgroundColor: AppColors.green),
                  child: Text(drawing ? '生成中…' : '抽音色'),
                ),
              ],
            ),
          ),
        ]),
        const SizedBox(height: 12),
        if (assets.isEmpty)
          const Padding(
            padding: EdgeInsets.all(24),
            child: Center(child: Text('还没有音色候选', style: TextStyle(color: AppColors.muted))),
          )
        else
          Section(
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

  Map<String, dynamic> get _meta {
    final m = asset['meta'];
    if (m is Map) return Map<String, dynamic>.from(m);
    return const {};
  }

  /// 标题：本条音色特色，不再重复用户输入的总描述。
  String get _featureTitle {
    final meta = _meta;
    final desc = (meta['variant_desc'] as String?)?.trim() ?? '';
    if (desc.isNotEmpty) return desc;
    final label = (meta['variant'] as String?)?.trim() ?? '';
    final extra = (meta['variant_extra'] as String?)?.trim() ?? '';
    if (label.isNotEmpty && extra.isNotEmpty) return '$label：$extra';
    if (label.isNotEmpty) return label;
    final slot = asset['_slot'];
    if (slot is int) return '音色候选 $slot';
    return '音色候选';
  }

  @override
  Widget build(BuildContext context) {
    final pending = asset['_pending'] == true;
    final failed = asset['_failed'] == true;
    final fixed = asset['fixed'] == true;
    final slot = asset['_slot'];
    final total = asset['_total'];
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
    final subtitle = pending
        ? '出炉后可立即试听'
        : failed
            ? (asset['_error'] as String? ?? '请重试')
            : loading
                ? '加载音频中…'
                : playing
                    ? '播放中 · 再点停止'
                    : (fixed ? '已固定 · 通话将用此音色' : '点播放试听 · 点固定用于通话');
    return ListTile(
      contentPadding: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
      leading: leading,
      title: Text(
        pending
            ? (slot is int && total is int ? '正在生成 $slot/$total…' : '正在生成…')
            : (failed ? '生成失败' : _featureTitle),
        maxLines: 2,
        overflow: TextOverflow.ellipsis,
        style: const TextStyle(fontSize: 15, height: 1.25),
      ),
      subtitle: Text(subtitle, style: const TextStyle(fontSize: 12)),
      trailing: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          if (!pending && !failed)
            TextButton(
              onPressed: fixed ? null : onFix,
              child: Text(fixed ? '已固定' : '固定', style: TextStyle(color: fixed ? AppColors.muted : AppColors.green)),
            ),
          IconButton(icon: const Icon(Icons.delete_outline, size: 20), onPressed: onDelete),
        ],
      ),
    );
  }
}

class _ImageTab extends StatelessWidget {
  const _ImageTab({
    required this.prompt,
    required this.count,
    required this.drawing,
    required this.assets,
    required this.api,
    required this.refPersona,
    required this.personaName,
    required this.onCount,
    required this.onRefPersona,
    required this.onDraw,
    required this.onFix,
    required this.onDelete,
    required this.onOpen,
  });

  final TextEditingController prompt;
  final int count;
  final bool drawing;
  final List<Map<String, dynamic>> assets;
  final ApiClient? api;
  final bool refPersona;
  final String personaName;
  final ValueChanged<int> onCount;
  final ValueChanged<bool> onRefPersona;
  final VoidCallback onDraw;
  final Future<void> Function(Map<String, dynamic>) onFix;
  final Future<void> Function(Map<String, dynamic>) onDelete;
  final ValueChanged<int> onOpen;

  @override
  Widget build(BuildContext context) {
    return ListView(
      padding: const EdgeInsets.fromLTRB(0, 12, 0, 32),
      children: [
        Section(children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 12, 16, 8),
            child: TextField(
              controller: prompt,
              maxLines: 3,
              decoration: const InputDecoration(
                labelText: '形象提示词（默认单人半身，禁止拼贴多人）',
                border: OutlineInputBorder(),
                isDense: true,
              ),
            ),
          ),
          SwitchListTile(
            contentPadding: const EdgeInsets.symmetric(horizontal: 12),
            title: const Text('参考人设', style: TextStyle(fontSize: 15)),
            subtitle: Text(
              refPersona
                  ? '锚定「$personaName」性别与简介，抽不同长相方案'
                  : '关闭后仅按提示词，可能跑偏性别',
              style: const TextStyle(fontSize: 12, color: AppColors.muted),
            ),
            value: refPersona,
            activeThumbColor: AppColors.green,
            onChanged: drawing ? null : onRefPersona,
          ),
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 0, 16, 12),
            child: Row(
              children: [
                const Text('张数', style: TextStyle(color: AppColors.muted)),
                const SizedBox(width: 12),
                for (final n in [1, 2, 3, 4])
                  Padding(
                    padding: const EdgeInsets.only(right: 8),
                    child: ChoiceChip(
                      label: Text('$n'),
                      selected: count == n,
                      onSelected: drawing ? null : (_) => onCount(n),
                    ),
                  ),
                const Spacer(),
                FilledButton(
                  onPressed: drawing ? null : onDraw,
                  style: FilledButton.styleFrom(backgroundColor: AppColors.green),
                  child: Text(drawing ? '生成中…' : '抽形象'),
                ),
              ],
            ),
          ),
        ]),
        const SizedBox(height: 12),
        if (assets.isEmpty)
          const Padding(
            padding: EdgeInsets.all(24),
            child: Center(child: Text('还没有形象候选', style: TextStyle(color: AppColors.muted))),
          )
        else
          Padding(
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
        const SizedBox(height: 2),
        Row(
          children: [
            TextButton(
              onPressed: pending || failed || fixed ? null : onFix,
              child: Text(
                fixed ? '已固定' : '固定',
                style: TextStyle(color: fixed || pending || failed ? AppColors.muted : AppColors.green),
              ),
            ),
            const Spacer(),
            IconButton(icon: const Icon(Icons.delete_outline, size: 20), onPressed: onDelete),
          ],
        ),
      ],
    );
  }
}
