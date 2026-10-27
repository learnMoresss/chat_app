import 'dart:math' as math;

import 'package:flutter/material.dart';

/// 微信语音条宽度：约 60 + duration*10，夹在 60～200。
double voiceBubbleWidth(int seconds) {
  final s = seconds.clamp(1, 60);
  return (60.0 + s * 10.0).clamp(60.0, 200.0);
}

/// 微信式三道弧喇叭；播放时由内向外依次闪现。
class VoiceWaveIcon extends StatefulWidget {
  const VoiceWaveIcon({
    super.key,
    required this.pointRight,
    this.playing = false,
    this.color = const Color(0xFF111111),
    this.size = 22,
  });
  final bool pointRight;
  final bool playing;
  final Color color;
  final double size;

  @override
  State<VoiceWaveIcon> createState() => _VoiceWaveIconState();
}

class _VoiceWaveIconState extends State<VoiceWaveIcon> with SingleTickerProviderStateMixin {
  late final AnimationController _ctrl;

  @override
  void initState() {
    super.initState();
    _ctrl = AnimationController(vsync: this, duration: const Duration(milliseconds: 900));
    if (widget.playing) _ctrl.repeat();
  }

  @override
  void didUpdateWidget(covariant VoiceWaveIcon old) {
    super.didUpdateWidget(old);
    if (widget.playing && !_ctrl.isAnimating) {
      _ctrl.repeat();
    } else if (!widget.playing && _ctrl.isAnimating) {
      _ctrl.stop();
      _ctrl.value = 0;
    }
  }

  @override
  void dispose() {
    _ctrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      width: widget.size,
      height: widget.size,
      child: AnimatedBuilder(
        animation: _ctrl,
        builder: (context, child) {
          // 静止：三道弧全显；播放：内→中→外 循环闪（微信小喇叭）
          final frame = widget.playing ? (_ctrl.value * 3).floor().clamp(0, 2) : 2;
          return Transform(
            alignment: Alignment.center,
            transform: Matrix4.diagonal3Values(widget.pointRight ? 1.0 : -1.0, 1.0, 1.0),
            child: CustomPaint(
              painter: _WavePainter(color: widget.color, frame: frame),
            ),
          );
        },
      ),
    );
  }
}

class _WavePainter extends CustomPainter {
  _WavePainter({required this.color, required this.frame});
  final Color color;
  final int frame;

  @override
  void paint(Canvas canvas, Size size) {
    final paint = Paint()
      ..color = color
      ..style = PaintingStyle.stroke
      ..strokeWidth = 2
      ..strokeCap = StrokeCap.round;

    final c = Offset(size.width * 0.28, size.height * 0.5);
    canvas.drawCircle(c, 1.8, Paint()..color = color);

    void arc(double r) {
      canvas.drawArc(Rect.fromCircle(center: c, radius: r), -math.pi * 0.35, math.pi * 0.7, false, paint);
    }

    if (frame >= 0) arc(size.width * 0.22);
    if (frame >= 1) arc(size.width * 0.38);
    if (frame >= 2) arc(size.width * 0.54);
  }

  @override
  bool shouldRepaint(covariant _WavePainter old) => old.frame != frame || old.color != color;
}
