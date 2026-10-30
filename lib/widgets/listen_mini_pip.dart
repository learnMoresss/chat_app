import 'dart:async';
import 'dart:math' as math;
import 'dart:ui';

import 'package:flutter/material.dart';

import '../media/listen_player.dart';
import '../media/listen_session.dart';
import '../screens/listen_style_preview.dart';
import '../screens/listen_together.dart';
import '../store.dart';
import '../theme.dart';

const _idleCollapse = Duration(seconds: 8);
const _discSize = 54.0;
const _panelWidth = 196.0;
const _edgePad = 8.0;
const _tapSlop = 8.0;

/// App 内听歌悬浮窗：Disc（旋转唱片）↔ Panel（控件）↔ 全屏。
class ListenMiniPipHost extends StatelessWidget {
  const ListenMiniPipHost({super.key, required this.store, required this.child});

  final AppStore store;
  final Widget child;

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: ListenSessionController.instance,
      builder: (context, _) {
        final mini = ListenSessionController.instance.mini;
        return Stack(
          fit: StackFit.expand,
          children: [
            child,
            if (mini != null) _ListenPipOverlay(store: store, mini: mini),
          ],
        );
      },
    );
  }
}

class _ListenPipOverlay extends StatefulWidget {
  const _ListenPipOverlay({required this.store, required this.mini});

  final AppStore store;
  final ListenMiniState mini;

  @override
  State<_ListenPipOverlay> createState() => _ListenPipOverlayState();
}

class _ListenPipOverlayState extends State<_ListenPipOverlay>
    with TickerProviderStateMixin {
  late AnimationController _spin;
  late AnimationController _stylus;
  Timer? _idle;
  Offset? _dragPos;
  Offset _panStart = Offset.zero;
  Offset _posAtPanStart = Offset.zero;
  bool _dragging = false;
  double _dragDistance = 0;

  ListenSessionController get _ctl => ListenSessionController.instance;

  @override
  void initState() {
    super.initState();
    _spin = AnimationController(vsync: this, duration: const Duration(seconds: 8));
    _stylus = AnimationController(vsync: this, duration: const Duration(milliseconds: 1400))
      ..repeat(reverse: true);
    _syncSpin(widget.mini.playing);
    _armIdle();
  }

  @override
  void didUpdateWidget(covariant _ListenPipOverlay oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.mini.playing != widget.mini.playing) {
      _syncSpin(widget.mini.playing);
    }
    if (oldWidget.mini.sessionId != widget.mini.sessionId ||
        oldWidget.mini.mode != widget.mini.mode) {
      _armIdle();
    }
  }

  void _syncSpin(bool playing) {
    if (playing) {
      if (!_spin.isAnimating) _spin.repeat();
    } else {
      _spin.stop();
    }
  }

  void _armIdle() {
    _idle?.cancel();
    // 翻唱单曲没有全屏页，保持 panel 控件可见，不收到纯唱片
    if (widget.mini.sessionId.startsWith('sing_')) return;
    _idle = Timer(_idleCollapse, () {
      if (!mounted) return;
      if (_ctl.mini == null) return;
      if (_ctl.mini!.sessionId.startsWith('sing_')) return;
      if (_ctl.mini!.mode != ListenPipMode.disc) {
        _ctl.setPipMode(ListenPipMode.disc);
      }
    });
  }

  void _bumpIdle() => _armIdle();

  @override
  void dispose() {
    _idle?.cancel();
    _spin.dispose();
    _stylus.dispose();
    super.dispose();
  }

  Size _bubbleSize(ListenPipMode mode) {
    if (mode == ListenPipMode.disc) {
      // 唱针略伸出圆外
      return const Size(_discSize + 14, _discSize + 10);
    }
    return const Size(_panelWidth, 64);
  }

  Offset _defaultPos(Size screen, EdgeInsets pad, Size bubble) {
    return Offset(
      screen.width - bubble.width - _edgePad,
      screen.height - bubble.height - pad.bottom - 24,
    );
  }

  Offset _clampPos(Offset p, Size screen, EdgeInsets pad, Size bubble) {
    final minX = _edgePad;
    final maxX = math.max(minX, screen.width - bubble.width - _edgePad);
    final minY = pad.top + 48;
    final maxY = math.max(minY, screen.height - bubble.height - pad.bottom - 12);
    return Offset(p.dx.clamp(minX, maxX), p.dy.clamp(minY, maxY));
  }

  Offset _snapPos(Offset p, Size screen, EdgeInsets pad, Size bubble) {
    final mid = p.dx + bubble.width / 2;
    final left = _edgePad;
    final right = screen.width - bubble.width - _edgePad;
    final x = mid < screen.width / 2 ? left : right;
    return _clampPos(Offset(x, p.dy), screen, pad, bubble);
  }

  void _openFullscreen() {
    _bumpIdle();
    final mini = widget.mini;
    final store = widget.store;
    // 翻唱单曲：没有一起听会话，放大成带控件的 panel 即可
    if (mini.sessionId.startsWith('sing_')) {
      _ctl.setPipMode(ListenPipMode.panel);
      return;
    }
    // 浮窗在 MaterialApp.builder 里，context 没有 Navigator 祖先；用根 navigatorKey
    final nav = _ctl.navigatorKey.currentState;
    if (nav == null) return;
    final route = mini.sessionId == 'preview'
        ? MaterialPageRoute(builder: (_) => ListenStylePreviewPage(store: store))
        : MaterialPageRoute(
            builder: (_) => ListenTogetherPage(
              store: store,
              sessionId: mini.sessionId,
              convId: mini.convId,
              personaName: mini.personaName,
              resumeOnly: true,
            ),
          );
    // 先 push 再清浮窗，避免卸掉自身导致操作中断
    nav.push(route);
    _ctl.expand();
  }

  Future<void> _close() async {
    final mini = widget.mini;
    if (mini.sessionId != 'preview' && !mini.sessionId.startsWith('sing_')) {
      try {
        await widget.store.api?.patchListenSession(mini.sessionId, status: 'ended');
      } catch (_) {}
    }
    await _ctl.closeAndStop();
  }

  Future<void> _togglePlay() async {
    _bumpIdle();
    final h = await ensureListenHandler();
    if (widget.mini.playing) {
      await h.pause();
    } else {
      await h.play();
    }
  }

  void _onTap() {
    _bumpIdle();
    if (widget.mini.mode == ListenPipMode.disc) {
      _ctl.setPipMode(ListenPipMode.panel);
    }
  }

  @override
  Widget build(BuildContext context) {
    final media = MediaQuery.of(context);
    final screen = media.size;
    final pad = media.padding;
    final mode = widget.mini.mode;
    final bubble = _bubbleSize(mode);
    final raw = _dragPos ?? _ctl.pipAnchor ?? _defaultPos(screen, pad, bubble);
    final pos = _dragging ? _clampPos(raw, screen, pad, bubble) : _snapPos(raw, screen, pad, bubble);

    return Positioned(
      left: pos.dx,
      top: pos.dy,
      child: GestureDetector(
        behavior: HitTestBehavior.opaque,
        onPanStart: (d) {
          _bumpIdle();
          _dragging = true;
          _dragDistance = 0;
          _panStart = d.globalPosition;
          _posAtPanStart = pos;
        },
        onPanUpdate: (d) {
          _dragDistance += d.delta.distance;
          setState(() {
            _dragPos = _clampPos(
              _posAtPanStart + (d.globalPosition - _panStart),
              screen,
              pad,
              bubble,
            );
          });
        },
        onPanEnd: (_) {
          final wasTap = _dragDistance < _tapSlop;
          final snapped = _snapPos(_dragPos ?? pos, screen, pad, bubble);
          setState(() {
            _dragging = false;
            _dragPos = snapped;
          });
          _ctl.setPipAnchor(snapped);
          _bumpIdle();
          if (wasTap) _onTap();
        },
        onPanCancel: () {
          _dragging = false;
          _bumpIdle();
        },
        child: mode == ListenPipMode.disc
            ? _DiscBubble(
                mini: widget.mini,
                spin: _spin,
                stylus: _stylus,
              )
            : _PanelBubble(
                mini: widget.mini,
                onExpand: _openFullscreen,
                onClose: _close,
                onTogglePlay: _togglePlay,
                onCollapse: () {
                  _bumpIdle();
                  _ctl.setPipMode(ListenPipMode.disc);
                },
              ),
      ),
    );
  }
}

class _DiscBubble extends StatelessWidget {
  const _DiscBubble({
    required this.mini,
    required this.spin,
    required this.stylus,
  });

  final ListenMiniState mini;
  final AnimationController spin;
  final AnimationController stylus;

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      width: _discSize + 14,
      height: _discSize + 10,
      child: Stack(
        clipBehavior: Clip.none,
        children: [
          Positioned(
            left: 0,
            bottom: 0,
            child: RotationTransition(
              turns: spin,
              child: _CoverDisc(artUri: mini.artUri, size: _discSize),
            ),
          ),
          Positioned(
            right: 0,
            top: 0,
            child: AnimatedBuilder(
              animation: stylus,
              builder: (context, child) {
                final t = Curves.easeInOut.transform(stylus.value);
                // 唱针轻微点动（探测感）
                final angle = -0.35 + t * 0.12;
                return Transform.rotate(
                  angle: angle,
                  alignment: const Alignment(0.2, -0.8),
                  child: child,
                );
              },
              child: CustomPaint(
                size: const Size(22, 28),
                painter: _StylusPainter(),
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _CoverDisc extends StatelessWidget {
  const _CoverDisc({required this.artUri, required this.size});

  final Uri? artUri;
  final double size;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: size,
      height: size,
      decoration: BoxDecoration(
        shape: BoxShape.circle,
        boxShadow: [
          BoxShadow(
            color: Colors.black.withValues(alpha: 0.35),
            blurRadius: 10,
            offset: const Offset(0, 3),
          ),
        ],
        border: Border.all(color: Colors.white24, width: 1.5),
      ),
      child: ClipOval(
        child: Stack(
          fit: StackFit.expand,
          children: [
            const ColoredBox(color: Color(0xFF1A1028)),
            if (artUri != null)
              Image.network(
                artUri.toString(),
                fit: BoxFit.cover,
                errorBuilder: (_, error, stackTrace) => const _DefaultVinylFace(),
              )
            else
              const _DefaultVinylFace(),
            // 中心轴孔
            Center(
              child: Container(
                width: size * 0.18,
                height: size * 0.18,
                decoration: BoxDecoration(
                  shape: BoxShape.circle,
                  color: const Color(0xFF0B0B0F),
                  border: Border.all(color: Colors.white24, width: 1),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _DefaultVinylFace extends StatelessWidget {
  const _DefaultVinylFace();

  @override
  Widget build(BuildContext context) {
    return CustomPaint(
      painter: _VinylGroovePainter(),
      child: const Center(
        child: Icon(Icons.music_note_rounded, color: Colors.white54, size: 22),
      ),
    );
  }
}

class _VinylGroovePainter extends CustomPainter {
  @override
  void paint(Canvas canvas, Size size) {
    final c = Offset(size.width / 2, size.height / 2);
    canvas.drawCircle(c, size.width * 0.5, Paint()..color = const Color(0xFF2A1840));
    final paint = Paint()
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1
      ..color = Colors.white.withValues(alpha: 0.08);
    for (var r = size.width * 0.22; r < size.width * 0.48; r += 3.2) {
      canvas.drawCircle(c, r, paint);
    }
  }

  @override
  bool shouldRepaint(covariant CustomPainter oldDelegate) => false;
}

class _StylusPainter extends CustomPainter {
  @override
  void paint(Canvas canvas, Size size) {
    final arm = Paint()
      ..color = const Color(0xFFE8D5A3)
      ..strokeWidth = 2.2
      ..strokeCap = StrokeCap.round
      ..style = PaintingStyle.stroke;
    final pivot = Offset(size.width * 0.75, 4);
    final tip = Offset(size.width * 0.15, size.height - 2);
    canvas.drawLine(pivot, tip, arm);
    canvas.drawCircle(pivot, 3.2, Paint()..color = const Color(0xFFC4A35A));
    canvas.drawCircle(tip, 2.4, Paint()..color = const Color(0xFFFFF3C4));
  }

  @override
  bool shouldRepaint(covariant CustomPainter oldDelegate) => false;
}

class _PanelBubble extends StatelessWidget {
  const _PanelBubble({
    required this.mini,
    required this.onExpand,
    required this.onClose,
    required this.onTogglePlay,
    required this.onCollapse,
  });

  final ListenMiniState mini;
  final VoidCallback onExpand;
  final VoidCallback onClose;
  final VoidCallback onTogglePlay;
  final VoidCallback onCollapse;

  @override
  Widget build(BuildContext context) {
    return Material(
      color: Colors.transparent,
      elevation: 8,
      borderRadius: BorderRadius.circular(14),
      child: ClipRRect(
        borderRadius: BorderRadius.circular(14),
        child: BackdropFilter(
          filter: ImageFilter.blur(sigmaX: 16, sigmaY: 16),
          child: Container(
            width: _panelWidth,
            padding: const EdgeInsets.fromLTRB(8, 8, 4, 8),
            decoration: BoxDecoration(
              color: const Color(0xE61A1028),
              borderRadius: BorderRadius.circular(14),
              border: Border.all(color: Colors.white24),
            ),
            child: Row(
              children: [
                GestureDetector(
                  onTap: onCollapse,
                  child: _CoverDisc(artUri: mini.artUri, size: 40),
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: GestureDetector(
                    onTap: onExpand,
                    behavior: HitTestBehavior.opaque,
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Text(
                          mini.title.isEmpty ? '一起听' : mini.title,
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: const TextStyle(
                            color: Colors.white,
                            fontSize: 12,
                            fontWeight: FontWeight.w600,
                          ),
                        ),
                        Text(
                          mini.artist.isNotEmpty
                              ? mini.artist
                              : (mini.personaName == null || mini.personaName!.isEmpty
                                  ? '一起听'
                                  : '和 ${mini.personaName} 一起听'),
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: TextStyle(
                            color: Colors.white.withValues(alpha: 0.5),
                            fontSize: 10,
                          ),
                        ),
                      ],
                    ),
                  ),
                ),
                // 浮窗在 MaterialApp.builder 外侧，无 Overlay；勿加 tooltip（会 RawTooltip 报错）
                // 翻唱无一起听全屏页，隐藏放大按钮，只留播放/关闭
                if (!mini.sessionId.startsWith('sing_'))
                  IconButton(
                    visualDensity: VisualDensity.compact,
                    padding: EdgeInsets.zero,
                    constraints: const BoxConstraints(minWidth: 28, minHeight: 28),
                    onPressed: onExpand,
                    icon: const Icon(Icons.open_in_full_rounded, color: Colors.white70, size: 16),
                  ),
                IconButton(
                  visualDensity: VisualDensity.compact,
                  padding: EdgeInsets.zero,
                  constraints: const BoxConstraints(minWidth: 28, minHeight: 28),
                  onPressed: onTogglePlay,
                  icon: Icon(
                    mini.playing ? Icons.pause_rounded : Icons.play_arrow_rounded,
                    color: AppColors.green,
                    size: 20,
                  ),
                ),
                IconButton(
                  visualDensity: VisualDensity.compact,
                  padding: EdgeInsets.zero,
                  constraints: const BoxConstraints(minWidth: 28, minHeight: 28),
                  onPressed: onClose,
                  icon: const Icon(Icons.close, color: Colors.white54, size: 16),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
