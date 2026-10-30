import 'dart:typed_data';

import 'package:flutter/material.dart';

import 'api/api_client.dart';
import 'media/auth_image.dart';
import 'theme.dart';

class Avatar extends StatelessWidget {
  const Avatar(
    this.name,
    this.color, {
    super.key,
    this.size = 48,
    this.group = false,
    this.avatarFileId,
    this.api,
    this.localBytes,
  });
  final String name;
  final Color color;
  final double size;
  final bool group;
  final String? avatarFileId;
  final ApiClient? api;
  final Uint8List? localBytes;

  @override
  Widget build(BuildContext context) {
    final radius = BorderRadius.circular(size * 0.12);
    Widget child;
    if (group) {
      child = Icon(Icons.groups, color: Colors.white, size: size * 0.55);
    } else if (localBytes != null) {
      child = ClipRRect(
        borderRadius: radius,
        child: Image.memory(localBytes!, width: size, height: size, fit: BoxFit.cover),
      );
    } else if (avatarFileId != null && avatarFileId!.isNotEmpty && api != null) {
      child = ClipRRect(
        borderRadius: radius,
        child: SizedBox(
          width: size,
          height: size,
          child: AuthFileImage(
            api: api,
            fileId: avatarFileId!,
            placeholder: ColoredBox(
              color: color,
              child: Center(
                child: Text(
                  name.isEmpty ? '?' : name.characters.first,
                  style: TextStyle(color: Colors.white, fontSize: size * 0.38, fontWeight: FontWeight.w600, height: 1),
                ),
              ),
            ),
          ),
        ),
      );
    } else {
      child = Text(
        name.isEmpty ? '?' : name.characters.first,
        style: TextStyle(color: Colors.white, fontSize: size * 0.38, fontWeight: FontWeight.w600, height: 1),
      );
    }
    // 固定正方形，避免被 ListTile leading 纵向拉成长方形。
    return SizedBox(
      width: size,
      height: size,
      child: localBytes != null || (avatarFileId != null && avatarFileId!.isNotEmpty && api != null)
          ? child
          : DecoratedBox(
              decoration: BoxDecoration(color: color, borderRadius: radius),
              child: Center(child: child),
            ),
    );
  }
}

class RedBadge extends StatelessWidget {
  const RedBadge({super.key, required this.count, required this.child, this.dot = false});
  final int count;
  final Widget child;
  final bool dot;

  @override
  Widget build(BuildContext context) {
    if (count <= 0) return child;
    return Badge(
      isLabelVisible: true,
      backgroundColor: AppColors.badge,
      label: dot ? null : Text('$count'),
      smallSize: dot ? 8 : null,
      child: child,
    );
  }
}

class Section extends StatelessWidget {
  const Section({super.key, required this.children, this.margin = const EdgeInsets.only(top: 4)});
  final List<Widget> children;
  final EdgeInsetsGeometry margin;

  @override
  Widget build(BuildContext context) {
    return Container(
      margin: margin,
      color: AppColors.card,
      child: Material(
        color: AppColors.card,
        child: Column(mainAxisSize: MainAxisSize.min, children: children),
      ),
    );
  }
}

class ArrowTile extends StatelessWidget {
  const ArrowTile({super.key, required this.title, this.subtitle, this.leading, this.trailing, this.onTap});
  final String title;
  final String? subtitle;
  final Widget? leading;
  final Widget? trailing;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    return ListTile(
      leading: leading,
      title: Text(title),
      subtitle: subtitle == null ? null : Text(subtitle!, style: const TextStyle(color: AppColors.sub, fontSize: 13)),
      trailing: trailing ?? const Icon(Icons.chevron_right, color: Color(0xFFC8C8C8)),
      onTap: onTap,
    );
  }
}

/// 微信风格确认弹窗：圆角白底、标题/说明居中、底栏左右分栏按钮。
///
/// [runOnConfirm] 非空时：点确认先在按钮上转圈执行，成功再关闭；失败留在弹窗内。
Future<bool?> showWxConfirmDialog(
  BuildContext context, {
  required String title,
  String? message,
  String cancelText = '取消',
  String confirmText = '确定',
  String busyText = '处理中…',
  Color confirmColor = const Color(0xFF576B95),
  bool destructive = false,
  /// 可选勾选项；返回时通过 [checkboxValue] 读出最终值（用 List 传引用）
  String? checkboxLabel,
  List<bool>? checkboxValue,
  Future<void> Function()? runOnConfirm,
}) {
  final check = checkboxValue ?? <bool>[];
  var checked = check.isNotEmpty ? check.first : true;
  final actionColor = destructive ? AppColors.badge : confirmColor;
  var busy = false;
  String? err;

  return showDialog<bool>(
    context: context,
    barrierDismissible: runOnConfirm == null,
    barrierColor: const Color(0x66000000),
    builder: (ctx) {
      return Center(
        child: Material(
          color: Colors.transparent,
          child: StatefulBuilder(
            builder: (ctx, setLocal) {
              Future<void> confirm() async {
                if (busy) return;
                if (checkboxLabel != null) {
                  if (check.isEmpty) {
                    check.add(checked);
                  } else {
                    check[0] = checked;
                  }
                }
                final run = runOnConfirm;
                if (run == null) {
                  Navigator.pop(ctx, true);
                  return;
                }
                setLocal(() {
                  busy = true;
                  err = null;
                });
                try {
                  await run();
                  if (ctx.mounted) Navigator.pop(ctx, true);
                } catch (e) {
                  if (ctx.mounted) {
                    setLocal(() {
                      busy = false;
                      err = '$e';
                    });
                  }
                }
              }

              return Container(
                width: 270,
                decoration: BoxDecoration(
                  color: Colors.white,
                  borderRadius: BorderRadius.circular(14),
                ),
                clipBehavior: Clip.antiAlias,
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Padding(
                      padding: EdgeInsets.fromLTRB(20, 24, 20, checkboxLabel == null ? 22 : 12),
                      child: Column(
                        children: [
                          Text(
                            title,
                            textAlign: TextAlign.center,
                            style: const TextStyle(
                              fontSize: 17,
                              fontWeight: FontWeight.w600,
                              color: Color(0xFF111111),
                              height: 1.35,
                            ),
                          ),
                          if (message != null && message.isNotEmpty) ...[
                            const SizedBox(height: 10),
                            Text(
                              message,
                              textAlign: TextAlign.center,
                              style: const TextStyle(
                                fontSize: 14,
                                color: Color(0xFF888888),
                                height: 1.4,
                              ),
                            ),
                          ],
                          if (err != null && err!.isNotEmpty) ...[
                            const SizedBox(height: 10),
                            Text(
                              err!,
                              textAlign: TextAlign.center,
                              style: const TextStyle(fontSize: 13, color: AppColors.badge, height: 1.35),
                            ),
                          ],
                          if (checkboxLabel != null) ...[
                            const SizedBox(height: 14),
                            GestureDetector(
                              behavior: HitTestBehavior.opaque,
                              onTap: busy
                                  ? null
                                  : () => setLocal(() {
                                        checked = !checked;
                                        if (check.isEmpty) {
                                          check.add(checked);
                                        } else {
                                          check[0] = checked;
                                        }
                                      }),
                              child: Row(
                                mainAxisAlignment: MainAxisAlignment.center,
                                children: [
                                  Container(
                                    width: 18,
                                    height: 18,
                                    decoration: BoxDecoration(
                                      color: checked ? AppColors.green : Colors.white,
                                      borderRadius: BorderRadius.circular(3),
                                      border: Border.all(
                                        color: checked ? AppColors.green : const Color(0xFFC8C8C8),
                                        width: 1.2,
                                      ),
                                    ),
                                    child: checked
                                        ? const Icon(Icons.check, size: 14, color: Colors.white)
                                        : null,
                                  ),
                                  const SizedBox(width: 8),
                                  Text(
                                    checkboxLabel,
                                    style: const TextStyle(fontSize: 14, color: Color(0xFF333333)),
                                  ),
                                ],
                              ),
                            ),
                          ],
                        ],
                      ),
                    ),
                    const Divider(height: 1, thickness: 0.5, color: Color(0xFFE5E5E5)),
                    SizedBox(
                      height: 48,
                      child: Row(
                        children: [
                          Expanded(
                            child: InkWell(
                              onTap: busy ? null : () => Navigator.pop(ctx, false),
                              child: Center(
                                child: Text(
                                  cancelText,
                                  style: TextStyle(
                                    fontSize: 17,
                                    color: busy ? const Color(0xFFBDBDBD) : const Color(0xFF111111),
                                  ),
                                ),
                              ),
                            ),
                          ),
                          const VerticalDivider(width: 0.5, thickness: 0.5, color: Color(0xFFE5E5E5)),
                          Expanded(
                            child: InkWell(
                              onTap: busy ? null : confirm,
                              child: Center(
                                child: busy
                                    ? Row(
                                        mainAxisSize: MainAxisSize.min,
                                        children: [
                                          SizedBox(
                                            width: 14,
                                            height: 14,
                                            child: CircularProgressIndicator(
                                              strokeWidth: 2,
                                              color: actionColor,
                                            ),
                                          ),
                                          const SizedBox(width: 8),
                                          Text(
                                            busyText,
                                            style: TextStyle(
                                              fontSize: 15,
                                              color: actionColor,
                                              fontWeight: FontWeight.w500,
                                            ),
                                          ),
                                        ],
                                      )
                                    : Text(
                                        confirmText,
                                        style: TextStyle(
                                          fontSize: 17,
                                          color: actionColor,
                                          fontWeight: FontWeight.w500,
                                        ),
                                      ),
                              ),
                            ),
                          ),
                        ],
                      ),
                    ),
                  ],
                ),
              );
            },
          ),
        ),
      );
    },
  );
}

/// 微信风格单行输入弹窗（新建组名等）。
Future<String?> showWxInputDialog(
  BuildContext context, {
  required String title,
  String? message,
  String initial = '',
  String hint = '',
  String cancelText = '取消',
  String confirmText = '确定',
  Color confirmColor = const Color(0xFF576B95),
  int maxLength = 32,
}) {
  final ctrl = TextEditingController(text: initial);
  return showDialog<String>(
    context: context,
    barrierColor: const Color(0x66000000),
    builder: (ctx) {
      return Center(
        child: Material(
          color: Colors.transparent,
          child: Container(
            width: 290,
            decoration: BoxDecoration(
              color: Colors.white,
              borderRadius: BorderRadius.circular(14),
            ),
            clipBehavior: Clip.antiAlias,
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                Padding(
                  padding: const EdgeInsets.fromLTRB(20, 22, 20, 16),
                  child: Column(
                    children: [
                      Text(
                        title,
                        textAlign: TextAlign.center,
                        style: const TextStyle(
                          fontSize: 17,
                          fontWeight: FontWeight.w600,
                          color: Color(0xFF111111),
                        ),
                      ),
                      if (message != null && message.isNotEmpty) ...[
                        const SizedBox(height: 8),
                        Text(
                          message,
                          textAlign: TextAlign.center,
                          style: const TextStyle(fontSize: 13, color: Color(0xFF888888), height: 1.35),
                        ),
                      ],
                      const SizedBox(height: 14),
                      TextField(
                        controller: ctrl,
                        autofocus: true,
                        maxLength: maxLength,
                        textAlign: TextAlign.center,
                        style: const TextStyle(fontSize: 16, color: Color(0xFF111111)),
                        decoration: InputDecoration(
                          hintText: hint,
                          hintStyle: const TextStyle(color: Color(0xFFB2B2B2), fontSize: 15),
                          counterText: '',
                          filled: true,
                          fillColor: const Color(0xFFF5F5F5),
                          contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
                          border: OutlineInputBorder(
                            borderRadius: BorderRadius.circular(8),
                            borderSide: BorderSide.none,
                          ),
                        ),
                        onSubmitted: (v) {
                          final t = v.trim();
                          if (t.isEmpty) return;
                          Navigator.pop(ctx, t);
                        },
                      ),
                    ],
                  ),
                ),
                const Divider(height: 1, thickness: 0.5, color: Color(0xFFE5E5E5)),
                SizedBox(
                  height: 48,
                  child: Row(
                    children: [
                      Expanded(
                        child: InkWell(
                          onTap: () => Navigator.pop(ctx),
                          child: Center(
                            child: Text(cancelText, style: const TextStyle(fontSize: 17, color: Color(0xFF111111))),
                          ),
                        ),
                      ),
                      const VerticalDivider(width: 0.5, thickness: 0.5, color: Color(0xFFE5E5E5)),
                      Expanded(
                        child: InkWell(
                          onTap: () {
                            final t = ctrl.text.trim();
                            if (t.isEmpty) return;
                            Navigator.pop(ctx, t);
                          },
                          child: Center(
                            child: Text(
                              confirmText,
                              style: TextStyle(fontSize: 17, color: confirmColor, fontWeight: FontWeight.w500),
                            ),
                          ),
                        ),
                      ),
                    ],
                  ),
                ),
              ],
            ),
          ),
        ),
      );
    },
  ).whenComplete(ctrl.dispose);
}

Future<List<String>?> showFieldsDialog(
  BuildContext context, {
  required String title,
  required List<String> labels,
  List<String> initial = const [],
  String confirm = '确定',
}) {
  return showDialog<List<String>>(
    context: context,
    builder: (context) => _FieldsDialog(title: title, labels: labels, initial: initial, confirm: confirm),
  );
}

class _FieldsDialog extends StatefulWidget {
  const _FieldsDialog({required this.title, required this.labels, required this.confirm, this.initial = const []});
  final String title;
  final List<String> labels;
  final List<String> initial;
  final String confirm;

  @override
  State<_FieldsDialog> createState() => _FieldsDialogState();
}

class _FieldsDialogState extends State<_FieldsDialog> {
  late final controllers = [
    for (var i = 0; i < widget.labels.length; i++) TextEditingController(text: i < widget.initial.length ? widget.initial[i] : ''),
  ];

  @override
  void dispose() {
    for (final c in controllers) {
      c.dispose();
    }
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: Text(widget.title),
      content: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          for (var i = 0; i < controllers.length; i++)
            TextField(controller: controllers[i], autofocus: i == 0, decoration: InputDecoration(labelText: widget.labels[i])),
        ],
      ),
      actions: [
        TextButton(onPressed: () => Navigator.pop(context), child: const Text('取消')),
        TextButton(onPressed: () => Navigator.pop(context, [for (final c in controllers) c.text.trim()]), child: Text(widget.confirm)),
      ],
    );
  }
}

class EmptyHint extends StatelessWidget {
  const EmptyHint(this.text, {super.key});
  final String text;
  @override
  Widget build(BuildContext context) {
    return Padding(padding: const EdgeInsets.all(32), child: Center(child: Text(text, style: const TextStyle(color: AppColors.muted))));
  }
}
