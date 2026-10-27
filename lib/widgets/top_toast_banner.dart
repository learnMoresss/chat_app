import 'dart:ui';

import 'package:flutter/material.dart';

import '../store.dart';
import '../theme.dart';

/// 抖音「一起看」风格：顶部居中半透明胶囊通知。
class TopToastBannerHost extends StatelessWidget {
  const TopToastBannerHost({super.key, required this.store, required this.child});

  final AppStore store;
  final Widget child;

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: store,
      builder: (context, _) {
        final toast = store.topToast;
        return Stack(
          fit: StackFit.expand,
          children: [
            child,
            if (toast != null)
              Positioned(
                top: 0,
                left: 0,
                right: 0,
                child: SafeArea(
                  bottom: false,
                  child: Padding(
                    padding: const EdgeInsets.fromLTRB(28, 6, 28, 0),
                    child: Center(
                      child: _BannerPill(
                        toast: toast,
                        onAccept: () {
                          final t = store.consumeTopToast();
                          if (t != null) store.onTopToastAction(t);
                        },
                        onDismiss: () => store.consumeTopToast(),
                        onTapBody: () {
                          final t = store.consumeTopToast();
                          if (t != null) store.onTopToastOpen(t);
                        },
                      ),
                    ),
                  ),
                ),
              ),
          ],
        );
      },
    );
  }
}

class _BannerPill extends StatefulWidget {
  const _BannerPill({
    required this.toast,
    required this.onAccept,
    required this.onDismiss,
    required this.onTapBody,
  });

  final TopToast toast;
  final VoidCallback onAccept;
  final VoidCallback onDismiss;
  final VoidCallback onTapBody;

  @override
  State<_BannerPill> createState() => _BannerPillState();
}

class _BannerPillState extends State<_BannerPill> with SingleTickerProviderStateMixin {
  late final AnimationController _ctrl = AnimationController(vsync: this, duration: const Duration(milliseconds: 280));
  late final Animation<Offset> _slide = Tween(begin: const Offset(0, -1.2), end: Offset.zero).animate(
    CurvedAnimation(parent: _ctrl, curve: Curves.easeOutCubic),
  );

  @override
  void initState() {
    super.initState();
    _ctrl.forward();
  }

  @override
  void didUpdateWidget(covariant _BannerPill oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.toast.id != widget.toast.id) {
      _ctrl.forward(from: 0);
    }
  }

  @override
  void dispose() {
    _ctrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final t = widget.toast;
    final showAccept = t.kind == TopToastKind.listenInvite;

    return SlideTransition(
      position: _slide,
      child: Material(
        color: Colors.transparent,
        child: ClipRRect(
          borderRadius: BorderRadius.circular(28),
          child: BackdropFilter(
            filter: ImageFilter.blur(sigmaX: 18, sigmaY: 18),
            child: InkWell(
              onTap: widget.onTapBody,
              child: Container(
                constraints: const BoxConstraints(maxWidth: 420),
                padding: const EdgeInsets.fromLTRB(12, 8, 8, 8),
                decoration: BoxDecoration(
                  color: const Color(0xE61A1A1E),
                  borderRadius: BorderRadius.circular(28),
                  border: Border.all(color: Colors.white12),
                  boxShadow: [
                    BoxShadow(color: Colors.black.withValues(alpha: 0.28), blurRadius: 18, offset: const Offset(0, 6)),
                  ],
                ),
                child: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    CircleAvatar(
                      radius: 16,
                      backgroundColor: t.kind == TopToastKind.listenInvite
                          ? const Color(0xFF7C4DFF)
                          : AppColors.green,
                      child: Icon(
                        t.kind == TopToastKind.listenInvite ? Icons.headphones : Icons.chat_bubble,
                        size: 16,
                        color: Colors.white,
                      ),
                    ),
                    const SizedBox(width: 10),
                    Flexible(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          Text(
                            t.title,
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: const TextStyle(color: Colors.white, fontSize: 13, fontWeight: FontWeight.w600),
                          ),
                          if (t.subtitle.isNotEmpty)
                            Text(
                              t.subtitle,
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis,
                              style: TextStyle(color: Colors.white.withValues(alpha: 0.55), fontSize: 11),
                            ),
                        ],
                      ),
                    ),
                    const SizedBox(width: 8),
                    if (showAccept)
                      TextButton(
                        style: TextButton.styleFrom(
                          foregroundColor: Colors.white,
                          backgroundColor: const Color(0xFFFE2C55),
                          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 6),
                          minimumSize: Size.zero,
                          tapTargetSize: MaterialTapTargetSize.shrinkWrap,
                          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
                        ),
                        onPressed: widget.onAccept,
                        child: const Text('接受', style: TextStyle(fontSize: 13, fontWeight: FontWeight.w600)),
                      )
                    else
                      IconButton(
                        visualDensity: VisualDensity.compact,
                        padding: EdgeInsets.zero,
                        constraints: const BoxConstraints(minWidth: 32, minHeight: 32),
                        onPressed: widget.onDismiss,
                        icon: const Icon(Icons.close, size: 16, color: Colors.white54),
                      ),
                  ],
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}
