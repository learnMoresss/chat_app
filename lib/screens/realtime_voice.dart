import 'dart:async';
import 'dart:math' as math;
import 'dart:ui';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../api/api_client.dart';
import '../widgets.dart';

/// 本地实时语音对话页（仅 UI / 假数据）。
///
/// 后续接真音频时：
/// - iOS: AVAudioSession category playAndRecord + mode voiceChat（内置 Voice Processing = AEC）
/// - Android: AudioManager MODE_IN_COMMUNICATION + AcousticEchoCanceler
/// TODO: AEC via AVAudioSession.voiceChat / AcousticEchoCanceler
enum VoiceUiPhase { idle, listening, thinking, speaking }

class RealtimeVoicePage extends StatefulWidget {
  const RealtimeVoicePage({
    super.key,
    required this.peerName,
    required this.peerColor,
    this.peerAvatarFileId,
    this.api,
  });

  final String peerName;
  final Color peerColor;
  final String? peerAvatarFileId;
  final ApiClient? api;

  @override
  State<RealtimeVoicePage> createState() => _RealtimeVoicePageState();
}

class _CaptionLine {
  _CaptionLine({required this.mine, required this.full, this.revealed = ''});
  final bool mine;
  final String full;
  String revealed;
  bool get done => revealed.length >= full.length;
}

class _RealtimeVoicePageState extends State<RealtimeVoicePage> with TickerProviderStateMixin {
  VoiceUiPhase _phase = VoiceUiPhase.idle;
  bool _muted = false;
  bool _speakerOn = false; // 外放；开启后 UI 标 AEC
  bool _showCaptions = true;

  late final AnimationController _breathe;
  late final AnimationController _ripple;
  late final AnimationController _wave;

  final _captions = <_CaptionLine>[];
  final _scroll = ScrollController();
  Timer? _demoTimer;
  Timer? _typeTimer;
  int _demoStep = 0;

  static const _demoScript = <(bool, String)>[
    (true, '嗨，最近怎么样？'),
    (false, '还不错呀，刚在想周末去哪转转。'),
    (true, '要不要一起找个安静的地方坐坐？'),
    (false, '好啊，我听你的。你先说，我在听。'),
    (true, '那我们先随便聊聊近况吧。'),
    (false, '嗯，我今天心情挺轻松的，有点想听你说话。'),
  ];

  @override
  void initState() {
    super.initState();
    _breathe = AnimationController(vsync: this, duration: const Duration(milliseconds: 2400))..repeat(reverse: true);
    _ripple = AnimationController(vsync: this, duration: const Duration(milliseconds: 900));
    _wave = AnimationController(vsync: this, duration: const Duration(milliseconds: 1200))..repeat();
    _seedCaptions();
    _startDemoLoop();
  }

  @override
  void dispose() {
    _demoTimer?.cancel();
    _typeTimer?.cancel();
    _breathe.dispose();
    _ripple.dispose();
    _wave.dispose();
    _scroll.dispose();
    super.dispose();
  }

  void _seedCaptions() {
    _captions.add(_CaptionLine(mine: false, full: '按住中间球体，或点一下切换说话方（演示）。', revealed: '按住中间球体，或点一下切换说话方（演示）。'));
  }

  void _startDemoLoop() {
    _demoTimer?.cancel();
    _demoTimer = Timer.periodic(const Duration(seconds: 4), (_) {
      if (!mounted || _muted) return;
      _advanceDemo();
    });
  }

  void _advanceDemo() {
    final next = _demoScript[_demoStep % _demoScript.length];
    _demoStep++;
    final mine = next.$1;
    final text = next.$2;
    setState(() {
      _phase = mine ? VoiceUiPhase.listening : VoiceUiPhase.thinking;
    });
    if (!mine) {
      Future.delayed(const Duration(milliseconds: 700), () {
        if (!mounted) return;
        setState(() => _phase = VoiceUiPhase.speaking);
        _pushCaption(mine: false, text: text, typewriter: true);
      });
    } else {
      _pushCaption(mine: true, text: text, typewriter: false);
      Future.delayed(const Duration(milliseconds: 1600), () {
        if (!mounted) return;
        setState(() => _phase = VoiceUiPhase.idle);
      });
    }
  }

  void _pushCaption({required bool mine, required String text, required bool typewriter}) {
    final line = _CaptionLine(mine: mine, full: text, revealed: typewriter ? '' : text);
    setState(() => _captions.add(line));
    _typeTimer?.cancel();
    if (typewriter) {
      var i = 0;
      _typeTimer = Timer.periodic(const Duration(milliseconds: 42), (t) {
        if (!mounted) {
          t.cancel();
          return;
        }
        i++;
        setState(() => line.revealed = text.substring(0, i.clamp(0, text.length)));
        _scrollToEnd();
        if (i >= text.length) {
          t.cancel();
          Future.delayed(const Duration(milliseconds: 400), () {
            if (mounted) setState(() => _phase = VoiceUiPhase.idle);
          });
        }
      });
    } else {
      _scrollToEnd();
    }
  }

  void _scrollToEnd() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!_scroll.hasClients) return;
      _scroll.animateTo(
        _scroll.position.maxScrollExtent,
        duration: const Duration(milliseconds: 280),
        curve: Curves.easeOut,
      );
    });
  }

  String get _statusLabel {
    if (_muted) return '已静音';
    switch (_phase) {
      case VoiceUiPhase.idle:
        return '点球体开始说话';
      case VoiceUiPhase.listening:
        return '正在听你说…';
      case VoiceUiPhase.thinking:
        return '${widget.peerName} 思考中…';
      case VoiceUiPhase.speaking:
        return '${widget.peerName} 正在说';
    }
  }

  void _onOrbTap() {
    HapticFeedback.lightImpact();
    _ripple
      ..reset()
      ..forward();
    if (_muted) {
      setState(() => _muted = false);
    }
    // 手动切换轮次：我 ↔ TA
    if (_phase == VoiceUiPhase.listening || _phase == VoiceUiPhase.idle) {
      setState(() => _phase = VoiceUiPhase.thinking);
      Future.delayed(const Duration(milliseconds: 600), () {
        if (!mounted) return;
        setState(() => _phase = VoiceUiPhase.speaking);
        final reply = _demoScript.where((e) => !e.$1).map((e) => e.$2).toList();
        _pushCaption(
          mine: false,
          text: reply[_demoStep % reply.length],
          typewriter: true,
        );
        _demoStep++;
      });
    } else {
      setState(() => _phase = VoiceUiPhase.listening);
      _pushCaption(mine: true, text: '嗯…（演示：轮到我说话）', typewriter: false);
      Future.delayed(const Duration(milliseconds: 1400), () {
        if (mounted) setState(() => _phase = VoiceUiPhase.idle);
      });
    }
  }

  Color get _accent {
    if (_muted) return const Color(0xFF6B7280);
    switch (_phase) {
      case VoiceUiPhase.idle:
        return const Color(0xFF7C9CFF);
      case VoiceUiPhase.listening:
        return const Color(0xFF34D399);
      case VoiceUiPhase.thinking:
        return const Color(0xFFFBBF24);
      case VoiceUiPhase.speaking:
        return const Color(0xFFA78BFA);
    }
  }

  @override
  Widget build(BuildContext context) {
    final pad = MediaQuery.paddingOf(context);
    return AnnotatedRegion<SystemUiOverlayStyle>(
      value: SystemUiOverlayStyle.light.copyWith(
        statusBarColor: Colors.transparent,
        statusBarIconBrightness: Brightness.light,
        statusBarBrightness: Brightness.dark,
      ),
      child: Scaffold(
        backgroundColor: const Color(0xFF0B0D12),
        body: Stack(
          fit: StackFit.expand,
          children: [
            _VoiceBackdrop(accent: _accent, breathe: _breathe),
            SafeArea(
              bottom: false,
              child: Column(
                children: [
                  _buildTopBar(),
                  Expanded(child: _buildOrbArea()),
                  _buildCaptions(),
                  if (_speakerOn) _buildAecChip(),
                  const SizedBox(height: 8),
                  _buildControls(pad.bottom),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildTopBar() {
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 10, 16, 4),
      child: SizedBox(
        height: 52,
        child: Stack(
          alignment: Alignment.center,
          children: [
            // 中间：头像 + 名 + 状态，整体居中
            Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Avatar(
                      widget.peerName,
                      widget.peerColor,
                      size: 22,
                      api: widget.api,
                      avatarFileId: widget.peerAvatarFileId,
                    ),
                    const SizedBox(width: 8),
                    ConstrainedBox(
                      constraints: const BoxConstraints(maxWidth: 160),
                      child: Text(
                        widget.peerName,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(
                          color: Colors.white,
                          fontSize: 16,
                          fontWeight: FontWeight.w600,
                          letterSpacing: 0.2,
                          height: 1.1,
                        ),
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 5),
                AnimatedSwitcher(
                  duration: const Duration(milliseconds: 240),
                  child: Text(
                    _statusLabel,
                    key: ValueKey(_statusLabel),
                    style: TextStyle(
                      color: _accent.withValues(alpha: 0.95),
                      fontSize: 12,
                      fontWeight: FontWeight.w500,
                      height: 1.1,
                    ),
                  ),
                ),
              ],
            ),
            // 左右操作贴边，不挤中间
            Row(
              children: [
                _TopIconBtn(
                  icon: Icons.close_rounded,
                  onTap: () => Navigator.pop(context),
                ),
                const Spacer(),
                _TopChipBtn(
                  icon: _showCaptions ? Icons.subtitles_rounded : Icons.subtitles_off_rounded,
                  label: _showCaptions ? '字幕' : '字幕',
                  active: _showCaptions,
                  onTap: () => setState(() => _showCaptions = !_showCaptions),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildOrbArea() {
    return Center(
      child: GestureDetector(
        onTap: _onOrbTap,
        child: AnimatedBuilder(
          animation: Listenable.merge([_breathe, _ripple, _wave]),
          builder: (context, _) {
            final breath = 0.92 + _breathe.value * 0.08;
            final amp = switch (_phase) {
              VoiceUiPhase.listening => 0.55 + math.sin(_wave.value * math.pi * 2) * 0.35,
              VoiceUiPhase.speaking => 0.45 + math.sin(_wave.value * math.pi * 2) * 0.25,
              VoiceUiPhase.thinking => 0.35 + _breathe.value * 0.2,
              VoiceUiPhase.idle => 0.22 + _breathe.value * 0.08,
            };
            final ripple = Curves.easeOut.transform(_ripple.value);
            return SizedBox(
              width: 280,
              height: 280,
              child: Stack(
                alignment: Alignment.center,
                children: [
                  if (_ripple.value > 0 && _ripple.value < 1)
                    Opacity(
                      opacity: (1 - ripple).clamp(0.0, 1.0),
                      child: Transform.scale(
                        scale: 0.7 + ripple * 0.9,
                        child: Container(
                          width: 180,
                          height: 180,
                          decoration: BoxDecoration(
                            shape: BoxShape.circle,
                            border: Border.all(color: _accent.withValues(alpha: 0.55), width: 2),
                          ),
                        ),
                      ),
                    ),
                  CustomPaint(
                    size: const Size(280, 280),
                    painter: _OrbPainter(
                      accent: _accent,
                      breath: breath,
                      amp: _muted ? 0.08 : amp,
                      phase: _phase,
                      t: _wave.value,
                    ),
                  ),
                  Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Icon(
                        _muted
                            ? Icons.mic_off_rounded
                            : (_phase == VoiceUiPhase.speaking ? Icons.graphic_eq_rounded : Icons.mic_none_rounded),
                        color: Colors.white.withValues(alpha: 0.92),
                        size: 36,
                      ),
                      const SizedBox(height: 8),
                      Text(
                        _muted
                            ? '已静音'
                            : switch (_phase) {
                                VoiceUiPhase.listening => '聆听中',
                                VoiceUiPhase.thinking => '思考中',
                                VoiceUiPhase.speaking => '对方在说',
                                VoiceUiPhase.idle => '轻触切换',
                              },
                        style: const TextStyle(color: Colors.white70, fontSize: 13, letterSpacing: 0.5),
                      ),
                    ],
                  ),
                ],
              ),
            );
          },
        ),
      ),
    );
  }

  Widget _buildCaptions() {
    return AnimatedSize(
      duration: const Duration(milliseconds: 280),
      curve: Curves.easeOutCubic,
      alignment: Alignment.bottomCenter,
      child: !_showCaptions
          ? const SizedBox(width: double.infinity, height: 0)
          : AnimatedOpacity(
              opacity: _showCaptions ? 1 : 0,
              duration: const Duration(milliseconds: 200),
              child: Container(
                height: 148,
                width: double.infinity,
                margin: const EdgeInsets.symmetric(horizontal: 20),
                padding: const EdgeInsets.fromLTRB(14, 12, 14, 10),
                decoration: BoxDecoration(
                  color: Colors.white.withValues(alpha: 0.06),
                  borderRadius: BorderRadius.circular(16),
                  border: Border.all(color: Colors.white.withValues(alpha: 0.08)),
                ),
                child: ListView.builder(
                  controller: _scroll,
                  itemCount: _captions.length,
                  itemBuilder: (context, i) {
                    final c = _captions[i];
                    return Padding(
                      padding: const EdgeInsets.only(bottom: 10),
                      child: RichText(
                        text: TextSpan(
                          children: [
                            TextSpan(
                              text: c.mine ? '我  ' : '${widget.peerName}  ',
                              style: TextStyle(
                                color: c.mine ? const Color(0xFF34D399) : _accent,
                                fontSize: 12,
                                fontWeight: FontWeight.w600,
                              ),
                            ),
                            TextSpan(
                              text: c.revealed,
                              style: const TextStyle(color: Color(0xFFE8E8ED), fontSize: 14, height: 1.35),
                            ),
                            if (!c.done && !c.mine)
                              WidgetSpan(
                                alignment: PlaceholderAlignment.middle,
                                child: Padding(
                                  padding: const EdgeInsets.only(left: 2),
                                  child: _BlinkCaret(color: _accent),
                                ),
                              ),
                          ],
                        ),
                      ),
                    );
                  },
                ),
              ),
            ),
    );
  }

  Widget _buildAecChip() {
    return Padding(
      padding: const EdgeInsets.only(top: 6),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
        decoration: BoxDecoration(
          color: const Color(0xFF1F2937),
          borderRadius: BorderRadius.circular(20),
          border: Border.all(color: const Color(0xFF374151)),
        ),
        child: const Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Icons.hearing_disabled, size: 14, color: Color(0xFF93C5FD)),
            SizedBox(width: 6),
            Text(
              'AEC 回声消除已就绪',
              style: TextStyle(color: Color(0xFF93C5FD), fontSize: 11, fontWeight: FontWeight.w500),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildControls(double bottomInset) {
    // 字幕在顶栏；底栏仅三钮，无托盘背景
    return Padding(
      padding: EdgeInsets.fromLTRB(36, 10, 36, 16 + bottomInset.clamp(0, 28)),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.spaceEvenly,
        crossAxisAlignment: CrossAxisAlignment.center,
        children: [
          _CtrlBtn(
            icon: _muted ? Icons.mic_off_rounded : Icons.mic_rounded,
            label: _muted ? '已静音' : '静音',
            active: _muted,
            onTap: () {
              HapticFeedback.selectionClick();
              setState(() {
                _muted = !_muted;
                if (_muted) _phase = VoiceUiPhase.idle;
              });
            },
          ),
          _HangupBtn(onTap: () => Navigator.pop(context)),
          _CtrlBtn(
            icon: _speakerOn ? Icons.volume_up_rounded : Icons.phone_in_talk_rounded,
            label: _speakerOn ? '外放' : '听筒',
            active: _speakerOn,
            onTap: () {
              HapticFeedback.selectionClick();
              setState(() => _speakerOn = !_speakerOn);
              // TODO: AEC via AVAudioSession.voiceChat / AcousticEchoCanceler
            },
          ),
        ],
      ),
    );
  }
}

class _VoiceBackdrop extends StatelessWidget {
  const _VoiceBackdrop({required this.accent, required this.breathe});
  final Color accent;
  final Animation<double> breathe;

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: breathe,
      builder: (context, _) {
        final t = breathe.value;
        return Stack(
          fit: StackFit.expand,
          children: [
            DecoratedBox(
              decoration: BoxDecoration(
                gradient: RadialGradient(
                  center: const Alignment(0, -0.35),
                  radius: 1.15,
                  colors: [
                    Color.lerp(const Color(0xFF1A1F2E), accent.withValues(alpha: 0.35), t * 0.45)!,
                    const Color(0xFF0B0D12),
                  ],
                ),
              ),
            ),
            Positioned(
              top: -80,
              right: -60,
              child: _GlowBlob(color: accent.withValues(alpha: 0.18 + t * 0.08), size: 220),
            ),
            Positioned(
              bottom: 120,
              left: -40,
              child: _GlowBlob(color: const Color(0xFF6366F1).withValues(alpha: 0.12), size: 180),
            ),
            // 轻微磨砂感
            BackdropFilter(
              filter: ImageFilter.blur(sigmaX: 0.2, sigmaY: 0.2),
              child: const SizedBox.expand(),
            ),
          ],
        );
      },
    );
  }
}

class _GlowBlob extends StatelessWidget {
  const _GlowBlob({required this.color, required this.size});
  final Color color;
  final double size;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: size,
      height: size,
      decoration: BoxDecoration(
        shape: BoxShape.circle,
        gradient: RadialGradient(colors: [color, color.withValues(alpha: 0)]),
      ),
    );
  }
}

class _OrbPainter extends CustomPainter {
  _OrbPainter({
    required this.accent,
    required this.breath,
    required this.amp,
    required this.phase,
    required this.t,
  });

  final Color accent;
  final double breath;
  final double amp;
  final VoiceUiPhase phase;
  final double t;

  @override
  void paint(Canvas canvas, Size size) {
    final c = Offset(size.width / 2, size.height / 2);
    final r = 78.0 * breath;

    for (var i = 3; i >= 1; i--) {
      final ringR = r + 18.0 * i + amp * 14 * i;
      final p = Paint()
        ..style = PaintingStyle.stroke
        ..strokeWidth = 1.4
        ..color = accent.withValues(alpha: 0.12 + amp * 0.18 / i);
      canvas.drawCircle(c, ringR, p);
    }

    // 波形环
    final wave = Path();
    const n = 64;
    for (var i = 0; i <= n; i++) {
      final a = (i / n) * math.pi * 2;
      final wobble = math.sin(a * 5 + t * math.pi * 2) * amp * 10
          + math.sin(a * 3 - t * math.pi * 4) * amp * 5;
      final rr = r + 28 + wobble;
      final x = c.dx + math.cos(a) * rr;
      final y = c.dy + math.sin(a) * rr;
      if (i == 0) {
        wave.moveTo(x, y);
      } else {
        wave.lineTo(x, y);
      }
    }
    wave.close();
    canvas.drawPath(
      wave,
      Paint()
        ..style = PaintingStyle.stroke
        ..strokeWidth = 2
        ..color = accent.withValues(alpha: 0.45 + amp * 0.35),
    );

    final core = Paint()
      ..shader = RadialGradient(
        colors: [
          Colors.white.withValues(alpha: 0.22),
          accent.withValues(alpha: 0.55),
          accent.withValues(alpha: 0.15),
        ],
        stops: const [0.0, 0.45, 1.0],
      ).createShader(Rect.fromCircle(center: c, radius: r));
    canvas.drawCircle(c, r, core);

    canvas.drawCircle(
      c,
      r,
      Paint()
        ..style = PaintingStyle.stroke
        ..strokeWidth = 1.2
        ..color = Colors.white.withValues(alpha: 0.25),
    );
  }

  @override
  bool shouldRepaint(covariant _OrbPainter old) =>
      old.accent != accent || old.breath != breath || old.amp != amp || old.phase != phase || old.t != t;
}

class _BlinkCaret extends StatefulWidget {
  const _BlinkCaret({required this.color});
  final Color color;

  @override
  State<_BlinkCaret> createState() => _BlinkCaretState();
}

class _BlinkCaretState extends State<_BlinkCaret> with SingleTickerProviderStateMixin {
  late final AnimationController _c;

  @override
  void initState() {
    super.initState();
    _c = AnimationController(vsync: this, duration: const Duration(milliseconds: 550))..repeat(reverse: true);
  }

  @override
  void dispose() {
    _c.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return FadeTransition(
      opacity: _c,
      child: Container(width: 2, height: 14, color: widget.color),
    );
  }
}

class _TopIconBtn extends StatelessWidget {
  const _TopIconBtn({required this.icon, required this.onTap});
  final IconData icon;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Material(
      color: Colors.white.withValues(alpha: 0.1),
      shape: const CircleBorder(),
      clipBehavior: Clip.antiAlias,
      child: InkWell(
        onTap: onTap,
        child: SizedBox(
          width: 36,
          height: 36,
          child: Icon(icon, color: Colors.white, size: 20),
        ),
      ),
    );
  }
}

class _TopChipBtn extends StatelessWidget {
  const _TopChipBtn({
    required this.icon,
    required this.label,
    required this.active,
    required this.onTap,
  });

  final IconData icon;
  final String label;
  final bool active;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Material(
      color: active ? Colors.white.withValues(alpha: 0.16) : Colors.white.withValues(alpha: 0.08),
      borderRadius: BorderRadius.circular(18),
      clipBehavior: Clip.antiAlias,
      child: InkWell(
        onTap: onTap,
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(icon, size: 16, color: active ? Colors.white : Colors.white60),
              const SizedBox(width: 5),
              Text(
                label,
                style: TextStyle(
                  color: active ? Colors.white : Colors.white60,
                  fontSize: 13,
                  fontWeight: FontWeight.w500,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _CtrlBtn extends StatelessWidget {
  const _CtrlBtn({
    required this.icon,
    required this.label,
    required this.onTap,
    this.active = false,
  });

  final IconData icon;
  final String label;
  final VoidCallback onTap;
  final bool active;

  @override
  Widget build(BuildContext context) {
    const size = 52.0;
    return GestureDetector(
      onTap: onTap,
      behavior: HitTestBehavior.opaque,
      child: SizedBox(
        width: 64,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            AnimatedContainer(
              duration: const Duration(milliseconds: 200),
              width: size,
              height: size,
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                color: active ? Colors.white.withValues(alpha: 0.2) : Colors.white.withValues(alpha: 0.1),
                border: Border.all(
                  color: active ? Colors.white.withValues(alpha: 0.4) : Colors.white.withValues(alpha: 0.12),
                ),
              ),
              child: Icon(icon, color: Colors.white.withValues(alpha: 0.95), size: 22),
            ),
            const SizedBox(height: 8),
            Text(
              label,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: TextStyle(
                color: Colors.white.withValues(alpha: 0.55),
                fontSize: 11,
                fontWeight: FontWeight.w500,
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _HangupBtn extends StatelessWidget {
  const _HangupBtn({required this.onTap});
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: () {
        HapticFeedback.mediumImpact();
        onTap();
      },
      behavior: HitTestBehavior.opaque,
      child: SizedBox(
        width: 72,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Container(
              width: 60,
              height: 60,
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                gradient: const LinearGradient(
                  begin: Alignment.topLeft,
                  end: Alignment.bottomRight,
                  colors: [Color(0xFFFF6B6B), Color(0xFFE11D48)],
                ),
                boxShadow: [
                  BoxShadow(
                    color: const Color(0xFFE11D48).withValues(alpha: 0.35),
                    blurRadius: 14,
                    offset: const Offset(0, 4),
                  ),
                ],
              ),
              child: const Icon(Icons.call_end_rounded, color: Colors.white, size: 26),
            ),
            const SizedBox(height: 8),
            Text(
              '挂断',
              style: TextStyle(
                color: Colors.white.withValues(alpha: 0.55),
                fontSize: 11,
                fontWeight: FontWeight.w500,
              ),
            ),
          ],
        ),
      ),
    );
  }
}
