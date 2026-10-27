import 'dart:typed_data';

import 'package:flutter/material.dart';

import '../media/media_pick.dart';
import '../store.dart';
import '../theme.dart';
import '../widgets.dart';
import 'chat.dart';
import 'persona.dart';

/// 新建人设：名字、头像色、说明、渠道——一次配齐再开聊。
class CreatePersonaPage extends StatefulWidget {
  const CreatePersonaPage({super.key, required this.store});
  final AppStore store;

  @override
  State<CreatePersonaPage> createState() => _CreatePersonaPageState();
}

class _CreatePersonaPageState extends State<CreatePersonaPage> {
  final name = TextEditingController();
  final bio = TextEditingController(
    text: '你是一个温柔、简短的聊天对象。用口语说话，不要列点，不要提自己是 AI。',
  );
  late String channelId;
  Color color = const Color(0xFF2F6BFF);
  late bool greet;
  late bool momentsOn;
  bool saving = false;
  Uint8List? avatarBytes;
  String avatarName = 'avatar.jpg';
  String avatarFileId = '';

  static const _palette = <Color>[
    Color(0xFF2F6BFF),
    Color(0xFFE6A23C),
    Color(0xFF9B59B6),
    Color(0xFF16A085),
    Color(0xFFE67E22),
    Color(0xFFE74C3C),
    Color(0xFF34495E),
    Color(0xFF07C160),
    Color(0xFF1ABC9C),
    Color(0xFF8E44AD),
    Color(0xFF2980B9),
    Color(0xFFC0392B),
  ];

  @override
  void initState() {
    super.initState();
    // 与设置页全局默认一致：自己问候 / 可发朋友圈默认开
    greet = widget.store.greet;
    momentsOn = widget.store.momentsGlobal;
    final keyed = widget.store.channels.where((c) => c.keyFilled).toList();
    channelId = (keyed.isNotEmpty ? keyed.first : widget.store.channels.firstOrNull)?.id ?? '';
  }

  @override
  void dispose() {
    name.dispose();
    bio.dispose();
    super.dispose();
  }

  InputDecoration _cellDeco(String hint) => InputDecoration(
        hintText: hint,
        hintStyle: const TextStyle(color: AppColors.muted, fontSize: 16),
        border: InputBorder.none,
        isDense: true,
        contentPadding: const EdgeInsets.symmetric(vertical: 4),
      );

  Future<void> _pickAvatar() async {
    final shot = await MediaPick.pickImagesFromGallery(already: 0);
    if (shot.isEmpty) return;
    setState(() {
      avatarBytes = shot.first.bytes;
      avatarName = shot.first.name;
      avatarFileId = '';
    });
  }

  Future<void> _pickChannel() async {
    final store = widget.store;
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
                        trailing: c.id == channelId ? const Icon(Icons.check, color: AppColors.green) : null,
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
    if (sel != null) setState(() => channelId = sel);
  }

  Future<void> _submit() async {
    final n = name.text.trim();
    if (n.isEmpty) {
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('请填写名字')));
      return;
    }
    if (channelId.isEmpty && !widget.store.demo) {
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('请先在「我 → 渠道」填一个模型 Key')));
      return;
    }
    setState(() => saving = true);
    try {
      var avId = avatarFileId;
      if (avatarBytes != null && !widget.store.demo && widget.store.api != null) {
        final up = await widget.store.api!.uploadChatMedia(
          bytes: avatarBytes!,
          filename: avatarName,
          kind: 'image',
        );
        avId = up['file_id']?.toString() ?? '';
      }
      final p = await widget.store.createPersona(
        name: n,
        bio: bio.text,
        color: color,
        channelId: channelId,
        greet: greet,
        momentsOn: momentsOn,
        avatarFileId: avId,
      );
      if (!mounted) return;
      final c = widget.store.conversations.where((e) => e.personaId == p.id).firstOrNull;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('已创建「${p.display}」')));
      if (c != null) {
        Navigator.pushReplacement(
          context,
          MaterialPageRoute(builder: (_) => ChatPage(store: widget.store, conv: c)),
        );
      } else {
        Navigator.pushReplacement(
          context,
          MaterialPageRoute(builder: (_) => PersonaPage(store: widget.store, persona: p)),
        );
      }
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$e')));
      setState(() => saving = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final store = widget.store;
    final previewName = name.text.trim().isEmpty ? '名' : name.text.trim();
    final ch = store.channels.where((x) => x.id == channelId).firstOrNull;
    final channelLabel = ch == null ? '未选择' : '${ch.shared ? "公共" : "我的"} · ${ch.name}';

    return Scaffold(
      backgroundColor: AppColors.bg,
      appBar: AppBar(
        title: const Text('新建人设'),
        actions: [
          TextButton(
            onPressed: saving ? null : _submit,
            child: Text(saving ? '创建中…' : '完成', style: const TextStyle(color: AppColors.green)),
          ),
        ],
      ),
      body: ListView(
        padding: EdgeInsets.only(bottom: 24 + MediaQuery.paddingOf(context).bottom),
        children: [
          Section(children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 16, 16, 16),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.center,
                children: [
                  GestureDetector(
                    onTap: _pickAvatar,
                    child: Avatar(
                      previewName,
                      color,
                      size: 64,
                      localBytes: avatarBytes,
                      api: store.api,
                      avatarFileId: avatarFileId.isEmpty ? null : avatarFileId,
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
                          textInputAction: TextInputAction.next,
                        ),
                        GestureDetector(
                          onTap: _pickAvatar,
                          child: const Text('从相册选头像', style: TextStyle(color: AppColors.name, fontSize: 14)),
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
              child: Text('头像底色（无图时用）', style: TextStyle(fontSize: 13, color: AppColors.sub)),
            ),
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 12, 16, 16),
              child: Wrap(
                spacing: 10,
                runSpacing: 10,
                children: [
                  for (final c in _palette)
                    GestureDetector(
                      onTap: () => setState(() => color = c),
                      child: Container(
                        width: 36,
                        height: 36,
                        decoration: BoxDecoration(
                          color: c,
                          borderRadius: BorderRadius.circular(8),
                          border: Border.all(
                            color: color.toARGB32() == c.toARGB32() ? Colors.black : Colors.transparent,
                            width: 2,
                          ),
                        ),
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
            const Padding(
              padding: EdgeInsets.fromLTRB(16, 4, 16, 0),
              child: Text('会写进系统提示词，决定 TA 怎么说话', style: TextStyle(fontSize: 12, color: AppColors.muted)),
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
            if (store.channels.every((c) => !c.keyFilled) && !store.demo)
              const Padding(
                padding: EdgeInsets.fromLTRB(16, 0, 16, 10),
                child: Text(
                  '还没有填 Key：去「我 → 渠道 / Key」先配一个，否则人设说不了话。',
                  style: TextStyle(color: AppColors.badge, fontSize: 13),
                ),
              ),
            const Divider(height: 1, indent: 16, color: AppColors.line),
            SwitchListTile(
              title: const Text('自己问候'),
              subtitle: const Text('以后接上调度再用；现在先记下偏好'),
              value: greet,
              activeTrackColor: AppColors.green,
              onChanged: (v) => setState(() => greet = v),
            ),
            SwitchListTile(
              title: const Text('可发朋友圈'),
              value: momentsOn,
              activeTrackColor: AppColors.green,
              onChanged: (v) => setState(() => momentsOn = v),
            ),
          ]),
          const Padding(
            padding: EdgeInsets.fromLTRB(16, 8, 16, 0),
            child: Text(
              '创建后会自动开会话。可在资料页 →「人设与模型」继续改说明、记忆和渠道。',
              style: TextStyle(color: AppColors.muted, fontSize: 13),
            ),
          ),
        ],
      ),
    );
  }
}
