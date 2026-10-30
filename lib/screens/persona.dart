import 'dart:typed_data';

import 'package:flutter/material.dart';

import '../media/auth_image.dart';
import '../media/media_pick.dart';
import '../models.dart';
import '../store.dart';
import '../theme.dart';
import '../widgets.dart';
import 'chat.dart';
import 'chat_info.dart';
import 'moments.dart';
import 'companion_board.dart';
import 'fullscreen_images.dart';
import 'persona_draw.dart';
import 'persona_sung.dart';

/// 微信风格好友资料页（非编辑表单）。
class PersonaPage extends StatelessWidget {
  const PersonaPage({super.key, required this.store, required this.persona});
  final AppStore store;
  final Persona persona;

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: store,
      builder: (context, _) {
        final p = persona;
        final moments = store.moments.where((m) => m.authorId == p.id && m.images > 0).take(4).toList();
        final hasRemark = p.remark.trim().isNotEmpty;
        final displayName = p.display;
        final wxName = p.name;

        String profileSub() {
          final parts = <String>[];
          if (p.tagNames.isNotEmpty) parts.add(p.tagNames.join('、'));
          if (p.phone.isNotEmpty) parts.add(p.phone);
          if (p.desc.isNotEmpty) parts.add(p.desc);
          if (parts.isEmpty) return '备注、标签、电话、描述、权限';
          return parts.join(' · ');
        }

        String permSub() {
          if (p.blacklisted) return '已拉黑';
          final bits = <String>[];
          if (p.starred) bits.add('星标');
          if (p.hideHisMoments) bits.add('不看他');
          if (p.hideMyMoments) bits.add('不让他看');
          return bits.isEmpty ? '聊天、朋友圈等' : bits.join(' · ');
        }

        return Scaffold(
          backgroundColor: AppColors.bg,
          appBar: AppBar(
            backgroundColor: AppColors.bg,
            title: const SizedBox.shrink(),
            actions: [
              IconButton(
                icon: const Icon(Icons.more_horiz),
                onPressed: () => _more(context, p),
              ),
            ],
          ),
          body: ListView(
            children: [
              // 头像 + 昵称区
              Section(
                margin: EdgeInsets.zero,
                children: [
                  Padding(
                    padding: const EdgeInsets.fromLTRB(16, 20, 16, 20),
                    child: Row(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        GestureDetector(
                          onTap: () {
                            if (p.avatarFileId.isEmpty) return;
                            openAvatarPreview(
                              context,
                              api: store.api,
                              fileId: p.avatarFileId,
                              label: displayName,
                            );
                          },
                          child: Avatar(
                            wxName,
                            p.color,
                            size: 64,
                            api: store.api,
                            avatarFileId: p.avatarFileId.isEmpty ? null : p.avatarFileId,
                          ),
                        ),
                        const SizedBox(width: 16),
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text(displayName, style: const TextStyle(fontSize: 22, fontWeight: FontWeight.w600, height: 1.2)),
                              if (hasRemark) ...[
                                const SizedBox(height: 6),
                                Text('昵称：$wxName', style: const TextStyle(fontSize: 14, color: AppColors.sub)),
                              ],
                              if (p.isBot) ...[
                                const SizedBox(height: 4),
                                const Text('人设', style: TextStyle(fontSize: 13, color: AppColors.muted)),
                              ],
                            ],
                          ),
                        ),
                      ],
                    ),
                  ),
                ],
              ),
              Section(children: [
                ArrowTile(
                  title: '朋友资料',
                  subtitle: profileSub(),
                  onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => FriendRemarkPage(store: store, persona: p))).then((_) {
                    // ListenableBuilder 会刷新
                  }),
                ),
                ArrowTile(
                  title: '朋友权限',
                  subtitle: permSub(),
                  onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => FriendPermPage(store: store, persona: p))),
                ),
              ]),
              Section(children: [
                ListTile(
                  title: const Text('朋友圈'),
                  trailing: Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      if (moments.isNotEmpty)
                        SizedBox(
                          width: 36.0 * moments.length.clamp(1, 4) + 4,
                          height: 36,
                          child: Row(
                            children: [
                              for (final m in moments)
                                Container(
                                  width: 36,
                                  height: 36,
                                  margin: const EdgeInsets.only(left: 4),
                                  color: p.color.withValues(alpha: 0.35 + (m.images % 3) * 0.1),
                                ),
                            ],
                          ),
                        )
                      else
                        Text(
                          '${store.moments.where((m) => m.authorId == p.id).length}',
                          style: const TextStyle(color: AppColors.muted, fontSize: 14),
                        ),
                      const SizedBox(width: 4),
                      const Icon(Icons.chevron_right, color: Color(0xFFC8C8C8)),
                    ],
                  ),
                  onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => UserMomentsPage(store: store, authorId: p.id))),
                ),
              ]),
              if (p.isBot)
                Section(children: [
                  ArrowTile(
                    title: '人设与模型',
                    subtitle: store.channel(p.channelId)?.name ?? '未选渠道',
                    onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => PersonaConfigPage(store: store, persona: p))),
                  ),
                  const Divider(height: 1, indent: 16),
                  ArrowTile(
                    title: '定时任务',
                    subtitle: '仅此人设的聊天提醒 / 每日任务',
                    onTap: () => Navigator.push(
                      context,
                      MaterialPageRoute(builder: (_) => PersonaSchedulesPage(store: store, persona: p)),
                    ),
                  ),
                  const Divider(height: 1, indent: 16),
                  ArrowTile(
                    title: '音色 / 形象抽卡',
                    subtitle: '自拍出镜前请先固定形象（头像不算）',
                    onTap: () => Navigator.push(
                      context,
                      MaterialPageRoute(
                        builder: (_) => PersonaDrawPage(store: store, persona: p, initialTab: 1),
                      ),
                    ),
                  ),
                  const Divider(height: 1, indent: 16),
                  ArrowTile(
                    title: '唱过的歌',
                    subtitle: '翻唱曲库记录，点开可播放',
                    onTap: () => Navigator.push(
                      context,
                      MaterialPageRoute(
                        builder: (_) => PersonaSungPage(store: store, persona: p),
                      ),
                    ),
                  ),
                ]),
              const SizedBox(height: 12),
              // 底部操作：居中绿色/蓝色字，仿微信
              Section(children: [
                _ActionLine(
                  icon: Icons.chat_bubble,
                  iconColor: AppColors.green,
                  label: '发消息',
                  labelColor: AppColors.green,
                  onTap: () async {
                    try {
                      final c = await store.openChatWithPersona(p);
                      if (!context.mounted) return;
                      Navigator.push(context, MaterialPageRoute(builder: (_) => ChatPage(store: store, conv: c)));
                    } catch (e) {
                      if (!context.mounted) return;
                      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('打开会话失败：$e')));
                    }
                  },
                ),
                const Divider(height: 1, indent: 56),
                _ActionLine(
                  icon: Icons.phone_in_talk,
                  iconColor: AppColors.name,
                  label: '音视频通话',
                  labelColor: AppColors.name,
                  onTap: () => _callSheet(context),
                ),
              ]),
              const SizedBox(height: 40),
            ],
          ),
        );
      },
    );
  }

  void _callSheet(BuildContext context) {
    showModalBottomSheet<void>(
      context: context,
      backgroundColor: Colors.white,
      builder: (ctx) => SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            ListTile(
              leading: const Icon(Icons.call, color: AppColors.name),
              title: const Text('语音通话'),
              onTap: () {
                Navigator.pop(ctx);
                ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('语音通话（演示）'), duration: Duration(seconds: 1)));
              },
            ),
            ListTile(
              leading: const Icon(Icons.videocam, color: AppColors.name),
              title: const Text('视频通话'),
              onTap: () {
                Navigator.pop(ctx);
                ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('视频通话（演示）'), duration: Duration(seconds: 1)));
              },
            ),
            ListTile(title: const Center(child: Text('取消')), onTap: () => Navigator.pop(ctx)),
          ],
        ),
      ),
    );
  }

  void _more(BuildContext context, Persona p) {
    showModalBottomSheet<void>(
      context: context,
      backgroundColor: Colors.white,
      builder: (ctx) => SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            ListTile(
              title: const Text('推荐给朋友'),
              onTap: () {
                Navigator.pop(ctx);
                ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('推荐（演示）'), duration: Duration(seconds: 1)));
              },
            ),
            ListTile(
              title: const Text('加入黑名单'),
              onTap: () {
                p.blacklisted = true;
                store.ping();
                Navigator.pop(ctx);
              },
            ),
            ListTile(
              title: const Text('删除联系人', style: TextStyle(color: AppColors.badge)),
              onTap: () async {
                Navigator.pop(ctx);
                final ok = await showDialog<bool>(
                  context: context,
                  builder: (c) => AlertDialog(
                    title: Text('删除「${p.display}」'),
                    content: const Text('将删除该联系人及聊天记录；通讯录不再显示此人设。'),
                    actions: [
                      TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('取消')),
                      TextButton(onPressed: () => Navigator.pop(c, true), child: const Text('删除', style: TextStyle(color: AppColors.badge))),
                    ],
                  ),
                );
                if (ok != true) return;
                store.deleteContact(p, clearHistory: true);
                if (context.mounted) Navigator.pop(context);
              },
            ),
            ListTile(title: const Center(child: Text('取消')), onTap: () => Navigator.pop(ctx)),
          ],
        ),
      ),
    );
  }
}

class _ActionLine extends StatelessWidget {
  const _ActionLine({
    required this.icon,
    required this.iconColor,
    required this.label,
    required this.labelColor,
    required this.onTap,
  });
  final IconData icon;
  final Color iconColor;
  final String label;
  final Color labelColor;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return InkWell(
      onTap: onTap,
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: 14),
        child: Row(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Icon(icon, color: iconColor, size: 22),
            const SizedBox(width: 8),
            Text(label, style: TextStyle(fontSize: 17, color: labelColor, fontWeight: FontWeight.w500)),
          ],
        ),
      ),
    );
  }
}

/// 人设专属配置（从资料页二级进入，不挤占主资料）。
class PersonaConfigPage extends StatefulWidget {
  const PersonaConfigPage({super.key, required this.store, required this.persona});
  final AppStore store;
  final Persona persona;
  @override
  State<PersonaConfigPage> createState() => _PersonaConfigPageState();
}

class _PersonaConfigPageState extends State<PersonaConfigPage> {
  late final name = TextEditingController(text: widget.persona.name);
  late final bio = TextEditingController(text: widget.persona.bio);
  late final signature = TextEditingController(text: widget.persona.signature);
  late final model = TextEditingController(text: widget.store.channel(widget.persona.channelId)?.model ?? '');
  late final url = TextEditingController(text: widget.store.channel(widget.persona.channelId)?.baseUrl ?? '');
  late final quietStart = TextEditingController(text: widget.persona.quietStart);
  late final quietEnd = TextEditingController(text: widget.persona.quietEnd);
  late Color color = widget.persona.color;
  Uint8List? avatarBytes;
  String avatarName = 'avatar.jpg';
  late String avatarFileId = widget.persona.avatarFileId;
  bool generatingAvatar = false;

  @override
  void initState() {
    super.initState();
    // 延后到首帧后，避免 build 期间 setState / notifyListeners
    WidgetsBinding.instance.addPostFrameCallback((_) => _ensureDefaultChannel());
  }

  /// 未选或渠道失效时，自动落到「Key 已填」的可用渠道。
  void _ensureDefaultChannel() {
    if (!mounted) return;
    final store = widget.store;
    final p = widget.persona;
    final current = store.channel(p.channelId);
    if (current != null && current.keyFilled) {
      setState(() {
        model.text = current.model;
        url.text = current.baseUrl;
      });
      return;
    }
    final preferred = store.channels.where((c) => c.keyFilled).firstOrNull ?? store.channels.firstOrNull;
    if (preferred == null) return;
    setState(() {
      p.channelId = preferred.id;
      model.text = preferred.model;
      url.text = preferred.baseUrl;
    });
    store.setPersonaChannel(p, preferred.id);
  }

  @override
  void dispose() {
    name.dispose();
    bio.dispose();
    signature.dispose();
    model.dispose();
    url.dispose();
    quietStart.dispose();
    quietEnd.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    final p = widget.persona;
    final n = name.text.trim();
    if (n.isEmpty) {
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('名字不能为空')));
      return;
    }
    final nextName = n;
    final nextBio = bio.text.trim();
    final nextSignature = signature.text.trim();
    final nextQuietStart = quietStart.text.trim();
    final nextQuietEnd = quietEnd.text.trim();
    var nextAvatarId = avatarFileId;

    if (avatarBytes != null && !widget.store.demo && widget.store.api != null) {
      try {
        final up = await widget.store.api!.uploadChatMedia(
          bytes: avatarBytes!,
          filename: avatarName,
          kind: 'image',
        );
        final fid = up['file_id']?.toString() ?? '';
        if (fid.isEmpty) {
          if (mounted) {
            ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('头像上传失败：未返回文件 id')));
          }
          return;
        }
        nextAvatarId = fid;
        avatarFileId = fid;
        // 立刻写入内存缓存，避免还没拉到文件时看起来像「还原成色块字」
        AuthImageCache.put(fid, avatarBytes!);
      } catch (e) {
        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('头像上传失败：$e')));
        }
        return;
      }
    }

    // 本地先写入，界面立刻一致
    p.name = nextName;
    p.bio = nextBio;
    p.signature = nextSignature;
    p.color = color;
    p.quietStart = nextQuietStart;
    p.quietEnd = nextQuietEnd;
    p.avatarFileId = nextAvatarId;
    final ch = widget.store.channel(p.channelId);
    if (ch != null) {
      ch.model = model.text.trim();
      ch.baseUrl = url.text.trim();
    }
    widget.store.ping();

    if (!widget.store.demo && widget.store.api != null) {
      try {
        final raw = await widget.store.api!.patchPersona(p.id, {
          'name': nextName,
          'bio': nextBio,
          'signature': nextSignature,
          'color': color.toARGB32(),
          'channel_id': p.channelId,
          'greet': p.greet,
          'moments_on': p.momentsOn,
          'remark': p.remark,
          'avatar_file_id': nextAvatarId,
          'voice_reply_pref': p.voiceReplyPref,
        });
        // 以服务器返回为准，并落本地缓存（热重启/刷新不会丢）
        await widget.store.applyPersonaRemote(Map<String, dynamic>.from(raw));
        final synced = widget.store.persona(p.id);
        if (synced != null) {
          avatarFileId = synced.avatarFileId;
          name.text = synced.name;
          bio.text = synced.bio;
          signature.text = synced.signature;
        }
      } catch (e) {
        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('保存到服务器失败：$e')));
        }
        return;
      }
    }
    if (mounted) {
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('已保存'), duration: Duration(seconds: 1)));
    }
  }

  InputDecoration _cellDeco(String hint) => InputDecoration(
        hintText: hint,
        hintStyle: const TextStyle(color: AppColors.muted, fontSize: 16),
        border: InputBorder.none,
        isDense: true,
        contentPadding: const EdgeInsets.symmetric(vertical: 4),
      );

  Widget _fieldRow({required String label, required Widget child, bool last = false}) {
    return Column(
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 12, 16, 12),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.center,
            children: [
              SizedBox(
                width: 88,
                child: Text(label, style: const TextStyle(fontSize: 16, color: Color(0xFF111111))),
              ),
              Expanded(child: child),
            ],
          ),
        ),
        if (!last) const Divider(height: 1, indent: 16, color: AppColors.line),
      ],
    );
  }

  Future<void> _pickChannel() async {
    final store = widget.store;
    final p = widget.persona;
    final sel = await showModalBottomSheet<String>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.white,
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(12))),
      builder: (ctx) {
        final sheetH = MediaQuery.sizeOf(ctx).height * 0.62;
        return SafeArea(
          child: SizedBox(
            height: sheetH,
            child: Column(
              children: [
                const Padding(
                  padding: EdgeInsets.fromLTRB(16, 14, 16, 8),
                  child: Text('选用渠道', style: TextStyle(fontSize: 16, fontWeight: FontWeight.w600)),
                ),
                const Divider(height: 1),
                Expanded(
                  child: ListView.builder(
                    padding: const EdgeInsets.only(bottom: 8),
                    itemCount: store.channels.length,
                    itemBuilder: (_, i) {
                      final c = store.channels[i];
                      return ListTile(
                        title: Text('${c.shared ? "公共" : "我的"} · ${c.name}'),
                        subtitle: Text(
                          c.keyFilled ? 'Key 已填 · ${c.model}' : 'Key 未填 · ${c.model}',
                          style: const TextStyle(fontSize: 13, color: AppColors.sub),
                        ),
                        trailing: c.id == p.channelId ? const Icon(Icons.check, color: AppColors.green) : null,
                        onTap: () => Navigator.pop(ctx, c.id),
                      );
                    },
                  ),
                ),
                const Divider(height: 1),
                ListTile(
                  title: const Center(child: Text('取消', style: TextStyle(color: AppColors.sub))),
                  onTap: () => Navigator.pop(ctx),
                ),
                const SizedBox(height: 8),
              ],
            ),
          ),
        );
      },
    );
    if (sel == null) return;
    setState(() {
      p.channelId = sel;
      final next = store.channel(sel);
      model.text = next?.model ?? '';
      url.text = next?.baseUrl ?? '';
    });
    store.setPersonaChannel(p, sel);
  }

  Future<void> _pickAvatar() async {
    final shot = await MediaPick.pickImagesFromGallery(already: 0);
    if (shot.isEmpty) return;
    setState(() {
      avatarBytes = shot.first.bytes;
      avatarName = shot.first.name;
    });
  }

  Future<void> _genAvatarByPrompt() async {
    if (generatingAvatar) return;
    final store = widget.store;
    final api = store.api;
    if (store.demo || api == null) {
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('需登录后才能生成头像')));
      return;
    }
    final ctrl = TextEditingController(text: '二次元头像，清爽，正面半身');
    final prompt = await showDialog<String>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('提示词生成头像'),
        content: TextField(
          controller: ctrl,
          maxLines: 3,
          decoration: const InputDecoration(hintText: '描述长相/画风…（生成正方形，文生图走 Z-Image）'),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('取消')),
          TextButton(
            onPressed: () => Navigator.pop(ctx, ctrl.text.trim()),
            child: const Text('生成'),
          ),
        ],
      ),
    );
    ctrl.dispose();
    if (prompt == null || prompt.isEmpty || !mounted) return;
    setState(() => generatingAvatar = true);
    ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('正在生成头像…')));
    try {
      final items = await api.drawPersonaImage(
        widget.persona.id,
        prompt: '正方形头像 1:1，$prompt',
        count: 1,
        refPersona: false,
        purpose: 'avatar',
      ).timeout(const Duration(seconds: 120));
      if (items.isEmpty) throw Exception('未返回图片');
      final fid = (items.first as Map)['file_id']?.toString() ?? '';
      if (fid.isEmpty) throw Exception('无 file_id');
      if (!mounted) return;
      setState(() => avatarFileId = fid);
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('头像已生成，点完成保存')));
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('生成失败：$e')));
      }
    } finally {
      if (mounted) setState(() => generatingAvatar = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final p = widget.persona;
    final store = widget.store;
    final ch = store.channel(p.channelId);
    final channelLabel = ch == null
        ? '未选择'
        : '${ch.shared ? "公共" : "我的"} · ${ch.name}';
    final channelSub = ch == null ? '去选择模型渠道' : (ch.keyFilled ? 'Key 已填' : 'Key 未填');

    return Scaffold(
      backgroundColor: AppColors.bg,
      appBar: AppBar(
        title: const Text('人设与模型'),
        actions: [
          TextButton(onPressed: _save, child: const Text('完成', style: TextStyle(color: AppColors.green))),
        ],
      ),
      body: ListView(
        padding: EdgeInsets.only(bottom: 24 + MediaQuery.paddingOf(context).bottom),
        children: [
          Section(
            margin: EdgeInsets.zero,
            children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 16, 16, 16),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.center,
                children: [
                  Align(
                    alignment: Alignment.centerLeft,
                    child: GestureDetector(
                      onTap: () {
                        openAvatarPreview(
                          context,
                          api: store.api,
                          fileId: avatarFileId.isEmpty ? null : avatarFileId,
                          bytes: avatarBytes,
                          label: name.text.trim().isEmpty ? p.name : name.text.trim(),
                        );
                      },
                      child: Avatar(
                        name.text.trim().isEmpty ? p.name : name.text.trim(),
                        color,
                        size: 64,
                        localBytes: avatarBytes,
                        api: store.api,
                        avatarFileId: avatarFileId.isEmpty ? null : avatarFileId,
                      ),
                    ),
                  ),
                  const SizedBox(width: 14),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        TextField(
                          controller: name,
                          style: const TextStyle(fontSize: 17, fontWeight: FontWeight.w600),
                          decoration: _cellDeco('名字'),
                          onChanged: (_) => setState(() {}),
                        ),
                        Wrap(
                          spacing: 12,
                          children: [
                            GestureDetector(
                              onTap: _pickAvatar,
                              child: const Text('上传头像', style: TextStyle(color: AppColors.name, fontSize: 14)),
                            ),
                            GestureDetector(
                              onTap: generatingAvatar ? null : _genAvatarByPrompt,
                              child: Text(
                                generatingAvatar ? '生成中…' : '提示词生成',
                                style: TextStyle(
                                  color: generatingAvatar ? AppColors.muted : AppColors.name,
                                  fontSize: 14,
                                ),
                              ),
                            ),
                          ],
                        ),
                      ],
                    ),
                  ),
                ],
              ),
            ),
          ]),
          Section(children: [
            const Padding(
              padding: EdgeInsets.fromLTRB(16, 12, 16, 0),
              child: Text('人设说明', style: TextStyle(fontSize: 13, color: AppColors.sub)),
            ),
            Padding(
              padding: const EdgeInsets.fromLTRB(12, 6, 12, 12),
              child: TextField(
                controller: bio,
                maxLines: 5,
                minLines: 3,
                style: const TextStyle(fontSize: 16, height: 1.35),
                decoration: InputDecoration(
                  hintText: '性格、口吻、背景…',
                  hintStyle: const TextStyle(color: AppColors.muted),
                  filled: true,
                  fillColor: const Color(0xFFF7F7F7),
                  border: OutlineInputBorder(borderRadius: BorderRadius.circular(8), borderSide: BorderSide.none),
                  contentPadding: const EdgeInsets.all(12),
                ),
              ),
            ),
          ]),
          Section(children: [
            const Padding(
              padding: EdgeInsets.fromLTRB(16, 12, 16, 0),
              child: Text('朋友圈签名', style: TextStyle(fontSize: 13, color: AppColors.sub)),
            ),
            Padding(
              padding: const EdgeInsets.fromLTRB(12, 6, 12, 12),
              child: TextField(
                controller: signature,
                maxLength: 40,
                style: const TextStyle(fontSize: 16),
                decoration: InputDecoration(
                  hintText: '短签名，展示在朋友圈封面',
                  hintStyle: const TextStyle(color: AppColors.muted),
                  filled: true,
                  fillColor: const Color(0xFFF7F7F7),
                  border: OutlineInputBorder(borderRadius: BorderRadius.circular(8), borderSide: BorderSide.none),
                  contentPadding: const EdgeInsets.all(12),
                  counterText: '',
                ),
              ),
            ),
          ]),
          Section(children: [
            ArrowTile(
              title: '选用渠道',
              trailing: Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  ConstrainedBox(
                    constraints: const BoxConstraints(maxWidth: 180),
                    child: Text(
                      channelLabel,
                      textAlign: TextAlign.right,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(color: AppColors.sub, fontSize: 15),
                    ),
                  ),
                  const Icon(Icons.chevron_right, color: Color(0xFFC8C8C8)),
                ],
              ),
              onTap: _pickChannel,
            ),
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 0, 16, 10),
              child: Text(channelSub, style: const TextStyle(fontSize: 12, color: AppColors.muted)),
            ),
            const Divider(height: 1, indent: 16, color: AppColors.line),
            _fieldRow(
              label: '模型',
              child: TextField(controller: model, style: const TextStyle(fontSize: 16), decoration: _cellDeco('如 deepseek-chat')),
            ),
            _fieldRow(
              label: '接口地址',
              last: true,
              child: TextField(controller: url, style: const TextStyle(fontSize: 16), decoration: _cellDeco('https://…')),
            ),
          ]),
          Section(children: [
            SwitchListTile(
              title: const Text('自己问候'),
              value: p.greet,
              activeTrackColor: AppColors.green,
              onChanged: (v) => setState(() => p.greet = v),
            ),
            SwitchListTile(
              title: const Text('自己发朋友圈'),
              value: p.momentsOn,
              activeTrackColor: AppColors.green,
              onChanged: (v) => setState(() => p.momentsOn = v),
            ),
            SwitchListTile(
              title: const Text('消息免打扰'),
              value: p.muted,
              activeTrackColor: AppColors.green,
              onChanged: (v) => setState(() => p.muted = v),
            ),
            ArrowTile(
              title: '语音回复',
              trailing: Text(
                switch (p.voiceReplyPref) {
                  'never' => '从不',
                  'always' => '总是',
                  _ => '偶尔',
                },
                style: const TextStyle(color: AppColors.sub, fontSize: 16),
              ),
              onTap: () => setState(() {
                p.voiceReplyPref = switch (p.voiceReplyPref) {
                  'never' => 'sometimes',
                  'sometimes' => 'always',
                  _ => 'never',
                };
              }),
            ),
            ArrowTile(
              title: '问候每天上限',
              trailing: Text('${p.greetCap}', style: const TextStyle(color: AppColors.sub, fontSize: 16)),
              onTap: () => setState(() => p.greetCap = p.greetCap >= 5 ? 1 : p.greetCap + 1),
            ),
            ArrowTile(
              title: '朋友圈每天上限',
              trailing: Text('${p.momentsCap}', style: const TextStyle(color: AppColors.sub, fontSize: 16)),
              onTap: () => setState(() => p.momentsCap = p.momentsCap >= 3 ? 1 : p.momentsCap + 1),
            ),
            ArrowTile(
              title: '最短间隔',
              trailing: Text('${p.gapMinutes} 分钟', style: const TextStyle(color: AppColors.sub, fontSize: 16)),
              onTap: () => setState(() => p.gapMinutes = p.gapMinutes >= 240 ? 30 : p.gapMinutes + 30),
            ),
            _fieldRow(
              label: '免打扰',
              last: true,
              child: Row(
                children: [
                  Expanded(child: TextField(controller: quietStart, textAlign: TextAlign.center, decoration: _cellDeco('23:00'))),
                  const Text('—', style: TextStyle(color: AppColors.muted)),
                  Expanded(child: TextField(controller: quietEnd, textAlign: TextAlign.center, decoration: _cellDeco('08:00'))),
                ],
              ),
            ),
          ]),
          Section(children: [
            ListTile(
              title: const Text('定时任务'),
              subtitle: const Text('仅此人设的聊天提醒 / 每日任务', style: TextStyle(fontSize: 12, color: AppColors.sub)),
              trailing: const Icon(Icons.chevron_right, color: AppColors.muted),
              onTap: () {
                Navigator.of(context).push(
                  MaterialPageRoute(
                    builder: (_) => PersonaSchedulesPage(store: store, persona: p),
                  ),
                );
              },
            ),
            const Divider(height: 1, indent: 16),
            ListTile(
              title: const Text('陪伴看板'),
              subtitle: const Text('情绪 / 成长 / 记忆（只读）', style: TextStyle(fontSize: 12, color: AppColors.sub)),
              trailing: const Icon(Icons.chevron_right, color: AppColors.muted),
              onTap: () {
                Navigator.of(context).push(
                  MaterialPageRoute(
                    builder: (_) => CompanionBoardPage(
                      store: store,
                      personaId: p.id,
                      title: p.display,
                    ),
                  ),
                );
              },
            ),
            ListTile(
              title: Text('记忆（${p.memories.length}）'),
              trailing: TextButton(
                onPressed: () async {
                  final values = await showFieldsDialog(context, title: '加一条记忆', labels: const ['内容'], confirm: '添加');
                  if (values == null || values.first.isEmpty) return;
                  if (!store.demo && store.api != null) {
                    try {
                      final raw = await store.api!.addMemory(p.id, values.first);
                      setState(() => p.memories.insert(0, MemoryItem(raw['id'] as String? ?? 'm', raw['text'] as String? ?? values.first)));
                      store.ping();
                    } catch (e) {
                      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$e')));
                    }
                    return;
                  }
                  setState(() => p.memories.add(MemoryItem('m${DateTime.now().microsecondsSinceEpoch}', values.first)));
                  store.ping();
                },
                child: const Text('添加', style: TextStyle(color: AppColors.green)),
              ),
            ),
            for (final m in p.memories)
              ListTile(
                title: Text(m.text),
                trailing: IconButton(
                  icon: const Icon(Icons.delete_outline),
                  onPressed: () async {
                    if (!store.demo && store.api != null) {
                      try {
                        await store.api!.deleteMemory(p.id, m.id);
                      } catch (e) {
                        if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$e')));
                        return;
                      }
                    }
                    setState(() => p.memories.remove(m));
                    store.ping();
                  },
                ),
                onTap: () async {
                  final values = await showFieldsDialog(context, title: '改记忆', labels: const ['内容'], initial: [m.text], confirm: '保存');
                  if (values == null) return;
                  final next = values.first;
                  if (!store.demo && store.api != null) {
                    try {
                      await store.api!.updateMemory(p.id, m.id, next);
                    } catch (e) {
                      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$e')));
                      return;
                    }
                  }
                  setState(() => m.text = next);
                  store.ping();
                },
              ),
          ]),
          const SizedBox(height: 48),
        ],
      ),
    );
  }
}
