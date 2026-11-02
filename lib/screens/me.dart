import 'dart:async';
import 'dart:typed_data';

import 'package:flutter/material.dart';

import '../api/session.dart';
import '../media/auth_image.dart';
import '../media/media_cache.dart';
import '../media/media_pick.dart';
import '../models.dart';
import '../store.dart';
import '../theme.dart';
import '../widgets.dart';
import 'bootstrap.dart';
import 'fullscreen_images.dart';
import 'moments.dart';
import 'usage.dart';

class MePage extends StatelessWidget {
  const MePage({super.key, required this.store});
  final AppStore store;

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: store,
      builder: (context, _) {
        return Scaffold(
          backgroundColor: AppColors.bg,
          appBar: AppBar(title: const Text('我')),
          body: ListView(
            children: [
              Section(
                margin: EdgeInsets.zero,
                children: [
                  ListTile(
                    contentPadding: const EdgeInsets.fromLTRB(16, 16, 16, 16),
                    titleAlignment: ListTileTitleAlignment.center,
                    leading: Avatar(
                      store.meName,
                      const Color(0xFF4C8DFF),
                      size: 56,
                      api: store.api,
                      avatarFileId: store.meAvatarFileId.isEmpty ? null : store.meAvatarFileId,
                    ),
                    title: Text(store.meName, style: const TextStyle(fontSize: 20, fontWeight: FontWeight.w600)),
                    subtitle: Text(
                      store.meSignature.isNotEmpty
                          ? store.meSignature
                          : '${store.serverName} · ${store.accountName}',
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                    ),
                    trailing: const Icon(Icons.chevron_right, color: AppColors.muted),
                    onTap: () => Navigator.push(
                      context,
                      MaterialPageRoute(builder: (_) => ProfileEditPage(store: store)),
                    ),
                  ),
                ],
              ),
              Section(children: [
                ArrowTile(
                  leading: const Icon(Icons.photo_album, color: Color(0xFF2782D7)),
                  title: '朋友圈',
                  onTap: () => Navigator.push(
                    context,
                    MaterialPageRoute(builder: (_) => MomentsAlbumPage(store: store)),
                  ),
                ),
              ]),
              Section(children: [
                ArrowTile(leading: const Icon(Icons.settings, color: Color(0xFF1485EE)), title: '设置', onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => SettingsPage(store: store)))),
              ]),
            ],
          ),
        );
      },
    );
  }
}

class ProfileEditPage extends StatefulWidget {
  const ProfileEditPage({super.key, required this.store});
  final AppStore store;

  @override
  State<ProfileEditPage> createState() => _ProfileEditPageState();
}

class _ProfileEditPageState extends State<ProfileEditPage> {
  late final name = TextEditingController(text: widget.store.meName);
  late final signature = TextEditingController(text: widget.store.meSignature);
  Uint8List? avatarBytes;
  String avatarName = 'avatar.jpg';
  late String avatarFileId = widget.store.meAvatarFileId;
  var saving = false;

  @override
  void dispose() {
    name.dispose();
    signature.dispose();
    super.dispose();
  }

  Future<void> _pickAvatar() async {
    final picked = await MediaPick.pickImagesFromGallery(already: 8);
    if (picked.isEmpty) return;
    setState(() {
      avatarBytes = picked.first.bytes;
      avatarName = picked.first.name;
    });
  }

  Future<void> _save() async {
    final n = name.text.trim();
    if (n.isEmpty) {
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('名字不能为空')));
      return;
    }
    setState(() => saving = true);
    try {
      var fid = avatarFileId;
      if (avatarBytes != null && !widget.store.demo && widget.store.api != null) {
        final up = await widget.store.api!.uploadChatMedia(
          bytes: avatarBytes!,
          filename: avatarName,
          kind: 'image',
        );
        fid = up['file_id']?.toString() ?? fid;
      }
      await widget.store.saveMyProfile(
        displayName: n,
        avatarFileId: fid,
        signature: signature.text.trim(),
      );
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('已保存'), duration: Duration(seconds: 1)));
        Navigator.pop(context);
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('保存失败：$e')));
      }
    } finally {
      if (mounted) setState(() => saving = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.bg,
      appBar: AppBar(
        title: const Text('个人资料'),
        actions: [
          TextButton(
            onPressed: saving ? null : _save,
            child: saving
                ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2))
                : const Text('保存'),
          ),
        ],
      ),
      body: ListView(
        children: [
          Section(
            children: [
              ListTile(
                title: const Text('头像'),
                trailing: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    GestureDetector(
                      onTap: () {
                        openAvatarPreview(
                          context,
                          api: widget.store.api,
                          fileId: avatarFileId.isEmpty ? null : avatarFileId,
                          bytes: avatarBytes,
                          label: name.text.isEmpty ? widget.store.meName : name.text,
                        );
                      },
                      child: Avatar(
                        name.text.isEmpty ? widget.store.meName : name.text,
                        const Color(0xFF4C8DFF),
                        size: 48,
                        api: widget.store.api,
                        avatarFileId: avatarFileId.isEmpty ? null : avatarFileId,
                        localBytes: avatarBytes,
                      ),
                    ),
                    const SizedBox(width: 8),
                    TextButton(onPressed: _pickAvatar, child: const Text('上传')),
                  ],
                ),
              ),
              const Divider(height: 1),
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
                child: TextField(
                  controller: name,
                  decoration: const InputDecoration(
                    labelText: '名字',
                    border: InputBorder.none,
                  ),
                  onChanged: (_) => setState(() {}),
                ),
              ),
              const Divider(height: 1),
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
                child: TextField(
                  controller: signature,
                  maxLength: 120,
                  maxLines: 3,
                  minLines: 1,
                  decoration: const InputDecoration(
                    labelText: '个性签名',
                    hintText: '介绍一下自己',
                    border: InputBorder.none,
                    counterText: '',
                  ),
                  onChanged: (_) => setState(() {}),
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }
}

class SettingsPage extends StatelessWidget {
  const SettingsPage({super.key, required this.store});
  final AppStore store;

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: store,
      builder: (context, _) {
        final filled = store.channels.where((c) => c.keyFilled).length;
        final mine = store.channels.where((c) => !c.shared).length;
        final defaultCh = store.channels.where((c) => c.keyFilled).firstOrNull ?? store.channels.firstOrNull;

        return Scaffold(
          backgroundColor: AppColors.bg,
          appBar: AppBar(title: const Text('设置')),
          body: ListView(
            children: [
              Section(
                margin: EdgeInsets.zero,
                children: [
                  ArrowTile(
                    title: '账号与服务器',
                    subtitle: store.serverName,
                    onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => AccountServerPage(store: store))),
                  ),
                ],
              ),
              Section(children: [
                // 一行入口，别把 7 个模型全摊开
                ArrowTile(
                  title: '模型与接口',
                  subtitle: defaultCh == null
                      ? '未配置'
                      : '常用 ${defaultCh.name} · 已填 Key $filled/${store.channels.length}${mine > 0 ? ' · 自建 $mine' : ''}',
                  onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => ChannelsPage(store: store))),
                ),
                ArrowTile(
                  title: '用量',
                  subtitle: '调用次数与 Token',
                  onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => UsagePage(store: store))),
                ),
              ]),
              Section(children: [
                _WxSwitch(
                  title: '回复整形',
                  subtitle: '出站拆壳/去 MD/回声/橡皮擦；关掉则与旧行为一致',
                  value: store.shapeEnabled,
                  onChanged: (v) {
                    store.shapeEnabled = v;
                    store.ping();
                    store.saveShapePrefs();
                  },
                ),
                _WxSwitch(
                  title: '长回复分句发送',
                  subtitle: '按句号拆成 2–3 条，间隔模拟打字',
                  value: store.splitSend,
                  onChanged: (v) {
                    store.splitSend = v;
                    store.ping();
                    store.saveShapePrefs();
                  },
                ),
                ArrowTile(
                  title: '表情触发频率',
                  subtitle: store.stickerFreq == 'low'
                      ? '低 · 偶尔配表情'
                      : store.stickerFreq == 'medium'
                          ? '中 · 气氛到了再发'
                          : '高 · 默认，可较常发单张',
                  onTap: () async {
                    final next = await showModalBottomSheet<String>(
                      context: context,
                      builder: (ctx) => SafeArea(
                        child: Column(
                          mainAxisSize: MainAxisSize.min,
                          children: [
                            const ListTile(
                              title: Text('表情触发频率', style: TextStyle(fontWeight: FontWeight.w600)),
                              subtitle: Text('日常只发单张；组图需对方明确要收集'),
                            ),
                            for (final e in const [
                              ('high', '高（默认）', '气氛到了常发单张表情'),
                              ('medium', '中', '偶尔配表情'),
                              ('low', '低', '很少自动发表情'),
                            ])
                              ListTile(
                                title: Text(e.$2),
                                subtitle: Text(e.$3),
                                trailing: store.stickerFreq == e.$1
                                    ? const Icon(Icons.check, color: Color(0xFF07C160))
                                    : null,
                                onTap: () => Navigator.pop(ctx, e.$1),
                              ),
                          ],
                        ),
                      ),
                    );
                    if (next == null || next == store.stickerFreq) return;
                    store.stickerFreq = next;
                    store.ping();
                    unawaited(store.saveShapePrefs());
                  },
                ),
                _WxSwitch(
                  title: '人设自动问候',
                  subtitle: '新人设通过后可自动打招呼（全局默认）',
                  value: store.greet,
                  onChanged: (v) {
                    store.greet = v;
                    store.ping();
                  },
                ),
                _WxSwitch(
                  title: '人设可发朋友圈',
                  subtitle: '全局默认，可在人设页单独改',
                  value: store.momentsGlobal,
                  onChanged: (v) {
                    store.momentsGlobal = v;
                    store.ping();
                  },
                ),
                ArrowTile(
                  title: '免打扰时段',
                  subtitle: '${store.quietStart} – ${store.quietEnd}',
                  onTap: () async {
                    final values = await showFieldsDialog(
                      context,
                      title: '免打扰时段',
                      labels: const ['开始（如 23:00）', '结束（如 08:00）'],
                      initial: [store.quietStart, store.quietEnd],
                      confirm: '保存',
                    );
                    if (values == null) return;
                    store.quietStart = values[0].trim().isEmpty ? store.quietStart : values[0].trim();
                    store.quietEnd = values[1].trim().isEmpty ? store.quietEnd : values[1].trim();
                    store.ping();
                  },
                ),
              ]),
              Section(children: [
                ArrowTile(title: '新消息通知', subtitle: '免打扰会话不响铃'),
                ArrowTile(
                  title: '通用',
                  subtitle: '发现页入口等',
                  onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => const GeneralSettingsPage())),
                ),
              ]),
              Section(children: [
                ArrowTile(
                  title: '存储空间',
                  subtitle: '图片 / 视频 / 语音缓存',
                  onTap: () => Navigator.push(
                    context,
                    MaterialPageRoute(builder: (_) => const StorageCachePage()),
                  ),
                ),
                ArrowTile(title: '聊天记录备份', subtitle: '入口已留'),
              ]),
              Section(children: [
                ArrowTile(title: '关于幻信', subtitle: '私域对话 · 人设与记忆'),
              ]),
              Section(children: [
                ListTile(
                  title: const Center(child: Text('退出登录', style: TextStyle(color: AppColors.badge, fontSize: 16))),
                  onTap: () async {
                    // 必须清会话，否则 Bootstrap 会自动恢复登录，看起来像「退不出去」
                    store.disposeRealtime();
                    await SessionStore().clear();
                    if (!context.mounted) return;
                    Navigator.of(context).pushAndRemoveUntil(
                      MaterialPageRoute(builder: (_) => const BootstrapPage()),
                      (_) => false,
                    );
                  },
                ),
              ]),
              const SizedBox(height: 32),
            ],
          ),
        );
      },
    );
  }
}

/// 存储空间：统计并清理图片/视频/语音本地缓存（微信式）。
class StorageCachePage extends StatefulWidget {
  const StorageCachePage({super.key});

  @override
  State<StorageCachePage> createState() => _StorageCachePageState();
}

class _StorageCachePageState extends State<StorageCachePage> {
  var _loading = true;
  var _clearing = false;
  int _mediaBytes = 0;
  int _mediaFiles = 0;
  int _audioBytes = 0;

  @override
  void initState() {
    super.initState();
    _refresh();
  }

  Future<void> _refresh() async {
    setState(() => _loading = true);
    final media = await MediaDiskCache.stats();
    final audio = await MediaDiskCache.audioCacheBytes();
    if (!mounted) return;
    setState(() {
      _mediaBytes = media.bytes;
      _mediaFiles = media.files;
      _audioBytes = audio;
      _loading = false;
    });
  }

  Future<void> _clear() async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('清理缓存'),
        content: const Text('将删除已下载的图片、视频与语音缓存。聊天记录不受影响，下次打开会重新从服务器加载。'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('取消')),
          TextButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('清理', style: TextStyle(color: AppColors.badge)),
          ),
        ],
      ),
    );
    if (ok != true) return;
    setState(() => _clearing = true);
    try {
      await MediaDiskCache.clear();
      await MediaDiskCache.clearAudioCache();
      AuthImageCache.clearMemory();
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('缓存已清理')));
      await _refresh();
    } finally {
      if (mounted) setState(() => _clearing = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final total = _mediaBytes + _audioBytes;
    return Scaffold(
      backgroundColor: AppColors.bg,
      appBar: AppBar(title: const Text('存储空间')),
      body: ListView(
        children: [
          Section(children: [
            ListTile(
              title: const Text('缓存总量'),
              trailing: _loading
                  ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2))
                  : Text(MediaDiskCache.formatSize(total), style: const TextStyle(color: AppColors.sub, fontSize: 15)),
            ),
            const Divider(height: 1, indent: 16, color: AppColors.line),
            ListTile(
              title: const Text('图片 / 视频'),
              subtitle: Text(_loading ? '…' : '$_mediaFiles 个文件'),
              trailing: Text(
                _loading ? '' : MediaDiskCache.formatSize(_mediaBytes),
                style: const TextStyle(color: AppColors.sub, fontSize: 15),
              ),
            ),
            const Divider(height: 1, indent: 16, color: AppColors.line),
            ListTile(
              title: const Text('语音'),
              trailing: Text(
                _loading ? '' : MediaDiskCache.formatSize(_audioBytes),
                style: const TextStyle(color: AppColors.sub, fontSize: 15),
              ),
            ),
          ]),
          Section(children: [
            ListTile(
              title: Center(
                child: Text(
                  _clearing ? '清理中…' : '清理缓存',
                  style: TextStyle(
                    color: (_clearing || total == 0) ? AppColors.muted : AppColors.badge,
                    fontSize: 16,
                  ),
                ),
              ),
              onTap: (_clearing || total == 0 || _loading) ? null : _clear,
            ),
          ]),
          const Padding(
            padding: EdgeInsets.fromLTRB(16, 8, 16, 0),
            child: Text(
              '聊天里看过的图和视频会留在本机，下次直接读缓存；列表优先用压缩预览，点开可看原图。清理后不影响聊天记录。',
              style: TextStyle(color: AppColors.muted, fontSize: 13),
            ),
          ),
        ],
      ),
    );
  }
}

/// 模型管理：紧凑列表，默认折叠详情；按需编辑，可「只用常用」。
class ChannelsPage extends StatelessWidget {
  const ChannelsPage({super.key, required this.store});
  final AppStore store;

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: store,
      builder: (context, _) {
        final mine = store.channels.where((c) => !c.shared).toList();
        final shared = store.channels.where((c) => c.shared).toList();
        final ready = store.channels.where((c) => c.keyFilled).toList();
        final readyIds = ready.map((c) => c.id).toSet();
        final mineRest = mine.where((c) => !readyIds.contains(c.id)).toList();
        final sharedRest = shared.where((c) => !readyIds.contains(c.id)).toList();

        return Scaffold(
          backgroundColor: AppColors.bg,
          appBar: AppBar(
            title: const Text('模型与接口'),
            actions: [
              TextButton(
                onPressed: () => _editChannel(context, null),
                child: const Text('添加', style: TextStyle(color: AppColors.green)),
              ),
            ],
          ),
          body: ListView(
            children: [
              Padding(
                padding: const EdgeInsets.fromLTRB(16, 14, 16, 8),
                child: Text(
                  ready.isEmpty
                      ? '还没有填 Key。填一个就能让人设真正回复。'
                      : '已就绪 ${ready.length} 个。日常只需维护你真正用的，其余可忽略。',
                  style: const TextStyle(fontSize: 13, color: AppColors.sub, height: 1.35),
                ),
              ),
              if (ready.isNotEmpty) ...[
                const _SectionHead('正在使用'),
                Section(
                  margin: EdgeInsets.zero,
                  children: [
                    for (final ch in ready) _ChannelTile(store: store, channel: ch, onEdit: () => _editChannel(context, ch)),
                  ],
                ),
              ],
              if (mineRest.isNotEmpty) ...[
                const _SectionHead('我的接口'),
                Section(
                  margin: EdgeInsets.zero,
                  children: [
                    for (final ch in mineRest) _ChannelTile(store: store, channel: ch, onEdit: () => _editChannel(context, ch)),
                  ],
                ),
              ],
              if (sharedRest.isNotEmpty) ...[
                const _SectionHead('公共预设（按需配置）'),
                Section(
                  margin: EdgeInsets.zero,
                  children: [
                    for (final ch in sharedRest) _ChannelTile(store: store, channel: ch, onEdit: () => _editChannel(context, ch)),
                  ],
                ),
              ],
              const Padding(
                padding: EdgeInsets.all(16),
                child: Text(
                  '提示：不必把每个厂商都配一遍。选一个填好 Key 即可；人设页可单独指定用哪条通道。',
                  style: TextStyle(fontSize: 12, color: AppColors.muted, height: 1.4),
                ),
              ),
            ],
          ),
        );
      },
    );
  }

  Future<void> _editChannel(BuildContext context, Channel? existing) async {
    final isNew = existing == null;
    final name = TextEditingController(text: existing?.name ?? 'DeepSeek');
    final model = TextEditingController(text: existing?.model ?? 'deepseek-chat');
    final url = TextEditingController(text: existing?.baseUrl ?? 'https://api.deepseek.com');
    final key = TextEditingController();
    var keyFilled = existing?.keyFilled ?? false;
    var shared = existing?.shared ?? false;
    var clearKey = false;
    var obscureKey = false; // 默认明文，方便核对
    var loadingKey = !store.demo && existing != null && existing.keyFilled;

    if (loadingKey) {
      try {
        final detail = await store.api!.getChannel(existing.id);
        final plain = detail['api_key'] as String? ?? '';
        key.text = plain;
        keyFilled = plain.isNotEmpty || (detail['key_filled'] as bool? ?? false);
      } catch (_) {
        // 拉不到明文时仍可手改
      }
      loadingKey = false;
    }

    if (!context.mounted) return;
    final ok = await showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.white,
      builder: (ctx) {
        return StatefulBuilder(builder: (ctx, setLocal) {
          return Padding(
            padding: EdgeInsets.only(bottom: MediaQuery.viewInsetsOf(ctx).bottom),
            child: SafeArea(
              child: SingleChildScrollView(
                padding: const EdgeInsets.fromLTRB(16, 12, 16, 16),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Row(
                      children: [
                        TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('取消')),
                        Expanded(child: Center(child: Text(isNew ? '添加接口' : '编辑 ${existing.name}', style: const TextStyle(fontWeight: FontWeight.w600)))),
                        TextButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('保存', style: TextStyle(color: AppColors.green))),
                      ],
                    ),
                    TextField(controller: name, decoration: const InputDecoration(labelText: '名称', hintText: '如 DeepSeek')),
                    TextField(controller: model, decoration: const InputDecoration(labelText: '模型', hintText: '如 deepseek-chat')),
                    TextField(controller: url, decoration: const InputDecoration(labelText: '接口地址'), keyboardType: TextInputType.url),
                    TextField(
                      controller: key,
                      obscureText: obscureKey,
                      decoration: InputDecoration(
                        labelText: 'API Key',
                        hintText: '明文显示，方便核对',
                        suffixIcon: IconButton(
                          onPressed: () => setLocal(() => obscureKey = !obscureKey),
                          icon: Icon(obscureKey ? Icons.visibility_outlined : Icons.visibility_off_outlined, color: const Color(0xFF8A8A8A)),
                        ),
                      ),
                    ),
                    if (keyFilled || key.text.isNotEmpty)
                      TextButton(
                        onPressed: () {
                          setLocal(() {
                            clearKey = true;
                            key.clear();
                          });
                        },
                        child: const Text('清空 Key', style: TextStyle(color: AppColors.badge)),
                      ),
                    if (isNew)
                      SwitchListTile(
                        contentPadding: EdgeInsets.zero,
                        title: const Text('设为公共渠道（管理员）'),
                        value: shared,
                        activeTrackColor: AppColors.green,
                        onChanged: (v) => setLocal(() => shared = v),
                      ),
                    if (!isNew)
                      TextButton(
                        onPressed: () async {
                          try {
                            await store.deleteChannelRemote(existing);
                          } catch (e) {
                            if (context.mounted) {
                              ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$e')));
                            }
                            return;
                          }
                          if (ctx.mounted) Navigator.pop(ctx, false);
                          if (context.mounted) {
                            ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('已删除'), duration: Duration(seconds: 1)));
                          }
                        },
                        child: const Text('删除此接口', style: TextStyle(color: AppColors.badge)),
                      ),
                  ],
                ),
              ),
            ),
          );
        });
      },
    );

    if (ok != true) return;
    final n = name.text.trim();
    if (n.isEmpty) return;
    final m = model.text.trim().isEmpty ? 'default' : model.text.trim();
    final u = url.text.trim().isEmpty ? 'https://' : url.text.trim();

    try {
      final plain = key.text.trim();
      await store.saveChannelRemote(
        existing: existing,
        name: n,
        model: m,
        baseUrl: u,
        // 编辑时带上当前明文（含未改动的），方便核对后直接保存
        apiKey: clearKey ? '' : (plain.isEmpty && !isNew ? null : plain),
        shared: shared,
        clearKey: clearKey,
      );
    } catch (e) {
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$e')));
      }
    }
  }
}

class _ChannelTile extends StatelessWidget {
  const _ChannelTile({required this.store, required this.channel, required this.onEdit});
  final AppStore store;
  final Channel channel;
  final VoidCallback onEdit;

  @override
  Widget build(BuildContext context) {
    final ch = channel;
    return ListTile(
      leading: CircleAvatar(
        radius: 18,
        backgroundColor: ch.keyFilled ? AppColors.green.withValues(alpha: 0.15) : const Color(0xFFF0F0F0),
        child: Text(
          ch.name.isEmpty ? '?' : ch.name.characters.first,
          style: TextStyle(color: ch.keyFilled ? AppColors.green : AppColors.sub, fontWeight: FontWeight.w600),
        ),
      ),
      title: Text(ch.name),
      subtitle: Text(
        ch.keyFilled ? 'Key 已填 · ${ch.model}' : '未配置 Key',
        style: TextStyle(fontSize: 13, color: ch.keyFilled ? AppColors.sub : AppColors.muted),
        maxLines: 1,
        overflow: TextOverflow.ellipsis,
      ),
      trailing: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          if (ch.keyFilled && !store.demo && store.api != null)
            TextButton(
              onPressed: () async {
                final messenger = ScaffoldMessenger.of(context);
                messenger.showSnackBar(const SnackBar(content: Text('正在探测…'), duration: Duration(seconds: 1)));
                try {
                  final r = await store.api!.probeChannel(ch.id);
                  final ok = r['ok'] == true;
                  final ms = r['ms'];
                  final err = r['error']?.toString() ?? '';
                  messenger.showSnackBar(SnackBar(
                    content: Text(ok ? '连通正常 · ${ms}ms' : '失败 · ${ms}ms：${err.isEmpty ? "未知错误" : err}'),
                    duration: Duration(seconds: ok ? 2 : 5),
                  ));
                } catch (e) {
                  messenger.showSnackBar(SnackBar(content: Text('$e')));
                }
              },
              child: const Text('自检', style: TextStyle(fontSize: 13, color: AppColors.name)),
            ),
          if (!ch.shared)
            Container(
              margin: const EdgeInsets.only(right: 6),
              padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
              decoration: BoxDecoration(color: const Color(0xFFE8F8EF), borderRadius: BorderRadius.circular(3)),
              child: const Text('我的', style: TextStyle(fontSize: 11, color: AppColors.green)),
            ),
          const Icon(Icons.chevron_right, color: Color(0xFFC8C8C8)),
        ],
      ),
      onTap: onEdit,
    );
  }
}

class _SectionHead extends StatelessWidget {
  const _SectionHead(this.text);
  final String text;
  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 16, 16, 6),
      child: Text(text, style: const TextStyle(fontSize: 13, color: AppColors.sub)),
    );
  }
}

class _WxSwitch extends StatelessWidget {
  const _WxSwitch({required this.title, this.subtitle, required this.value, required this.onChanged});
  final String title;
  final String? subtitle;
  final bool value;
  final ValueChanged<bool> onChanged;

  @override
  Widget build(BuildContext context) {
    return SwitchListTile(
      title: Text(title),
      subtitle: subtitle == null ? null : Text(subtitle!, style: const TextStyle(fontSize: 12, color: AppColors.sub)),
      value: value,
      activeThumbColor: Colors.white,
      activeTrackColor: AppColors.green,
      onChanged: onChanged,
    );
  }
}

/// 通用设置：发现页管理入口（演示开关，不持久化复杂状态时可后接 store）。
class GeneralSettingsPage extends StatelessWidget {
  const GeneralSettingsPage({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.bg,
      appBar: AppBar(title: const Text('通用')),
      body: ListView(
        children: [
          Section(children: [
            ArrowTile(
              title: '发现页管理',
              subtitle: '开关发现页各项入口（演示说明）',
              onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => const DiscoverManagePage())),
            ),
          ]),
          const Padding(
            padding: EdgeInsets.all(16),
            child: Text('语言、字体大小等后续再补。', style: TextStyle(fontSize: 13, color: AppColors.sub)),
          ),
        ],
      ),
    );
  }
}

class DiscoverManagePage extends StatelessWidget {
  const DiscoverManagePage({super.key});

  static const items = [
    '朋友圈',
    '视频号',
    '扫一扫',
    '摇一摇',
    '看一看',
    '搜一搜',
    '购物',
    '游戏',
    '小程序',
  ];

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.bg,
      appBar: AppBar(title: const Text('发现页管理')),
      body: ListView(
        children: [
          const Padding(
            padding: EdgeInsets.fromLTRB(16, 14, 16, 8),
            child: Text('关闭后仅隐藏发现页入口，不影响其它地方使用。演示版为说明页。', style: TextStyle(fontSize: 13, color: AppColors.sub)),
          ),
          Section(
            children: [
              for (final name in items)
                ListTile(
                  title: Text(name),
                  trailing: const Text('显示中', style: TextStyle(fontSize: 13, color: AppColors.muted)),
                ),
            ],
          ),
        ],
      ),
    );
  }
}

class AccountServerPage extends StatelessWidget {
  const AccountServerPage({super.key, required this.store});
  final AppStore store;

  Future<void> _editServer(BuildContext context) async {
    final ctrl = TextEditingController(text: store.serverUrl);
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('后端地址'),
        content: TextField(
          controller: ctrl,
          autofocus: true,
          keyboardType: TextInputType.url,
          decoration: const InputDecoration(
            hintText: 'http://127.0.0.1:8000',
            helperText: '改完会保留当前登录，并重新同步',
          ),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('取消')),
          TextButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('保存', style: TextStyle(color: AppColors.green)),
          ),
        ],
      ),
    );
    if (ok != true || !context.mounted) return;
    final next = ctrl.text.trim();
    if (next.isEmpty) return;
    try {
      await store.updateServerUrl(next);
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('已更新服务器'), duration: Duration(seconds: 1)));
      }
    } catch (e) {
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$e')));
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: store,
      builder: (context, _) {
        return Scaffold(
          backgroundColor: AppColors.bg,
          appBar: AppBar(title: const Text('账号与服务器')),
          body: ListView(
            children: [
              Section(children: [
                ArrowTile(
                  title: '后端地址',
                  subtitle: store.serverUrl.isEmpty ? '未设置' : store.serverUrl,
                  onTap: () => _editServer(context),
                ),
                ListTile(title: const Text('当前账号'), subtitle: Text(store.accountName)),
              ]),
              Section(children: [
                ArrowTile(
                  title: '重新同步',
                  subtitle: '后端恢复后点这里拉最新数据',
                  onTap: () async {
                    try {
                      await store.pullSync();
                      if (context.mounted) {
                        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('同步完成'), duration: Duration(seconds: 1)));
                      }
                    } catch (e) {
                      if (context.mounted) {
                        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$e')));
                      }
                    }
                  },
                ),
                ArrowTile(
                  title: '添加另一个服务器的账号',
                  onTap: () {
                    Navigator.of(context).push(MaterialPageRoute(builder: (_) => const BootstrapPage()));
                  },
                ),
                ListTile(
                  title: const Center(child: Text('退出登录', style: TextStyle(color: AppColors.badge, fontSize: 16))),
                  onTap: () async {
                    store.disposeRealtime();
                    await SessionStore().clear();
                    if (!context.mounted) return;
                    Navigator.of(context).pushAndRemoveUntil(MaterialPageRoute(builder: (_) => const BootstrapPage()), (_) => false);
                  },
                ),
              ]),
            ],
          ),
        );
      },
    );
  }
}
