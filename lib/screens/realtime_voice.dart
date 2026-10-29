import 'dart:async';
import 'dart:math' as math;
import 'dart:ui';

import 'package:audioplayers/audioplayers.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:record/record.dart';

import '../api/api_client.dart';
import '../media/app_audio.dart';
import '../media/voice_record.dart';
import '../store.dart';
import '../widgets.dart';

/// 实时语音通话：进页即进入通话，VAD 自动分句，对方说完继续听。
///
/// 说明：当前为「通话式连续半双工」——听/说自动轮转，无需按一次发一次；
/// 真全双工（边听边说叠加）需平台 AEC，后续接 voiceChat session。
enum VoiceUiPhase { connecting, inCall, listening, thinking, speaking, blocked }

class RealtimeVoicePage extends StatefulWidget {
  const RealtimeVoicePage({
    super.key,
    required this.store,
    required this.convId,
    required this.peerName,
    required this.peerColor,
    this.peerAvatarFileId,
  });

  final AppStore store;
  final String convId;
  final String peerName;
  final Color peerColor;
  final String? peerAvatarFileId;

  @override
  State<RealtimeVoicePage> createState() => _RealtimeVoicePageState();
}

class _CaptionLine {
  _CaptionLine({required this.mine, required this.full, this.revealed = ''});
  final bool mine;
  final String full;
  String revealed;
}

class _PlayItem {
  _PlayItem({required this.fileId, required this.bytes});
  final String fileId;
  final Uint8List bytes;
}

class _RealtimeVoicePageState extends State<RealtimeVoicePage> with TickerProviderStateMixin {
  VoiceUiPhase _phase = VoiceUiPhase.connecting;
  bool _muted = false;
  bool _speakerOn = true;
  bool _showCaptions = true;
  bool _ready = false;
  bool _inCall = false;
  bool _busyTurn = false;
  bool _hangingUp = false;
  String? _turnId;
  String _statusOverride = '';
  String _asrInfo = '';

  late final AnimationController _breathe;
  late final AnimationController _ripple;
  late final AnimationController _wave;

  final _captions = <_CaptionLine>[];
  final _scroll = ScrollController();
  final _player = AudioPlayer(playerId: 'realtime_voice');
  final _queue = <_PlayItem>[];
  bool _playing = false;
  StreamSubscription? _playerDone;
  StreamSubscription<Amplitude>? _ampSub;

  bool _recording = false;
  bool _speechStarted = false;
  int _silenceTicks = 0;
  DateTime? _listenStarted;
  DateTime? _speechStartedAt;
  int _listenGen = 0;

  // dBFS：越大越响。经验阈值，可再调。
  static const double _speechDb = -38;
  static const double _silenceDb = -48;
  static const int _silenceNeeded = 10; // ~800ms @80ms
  static const int _minSpeechMs = 500;
  static const int _maxListenSec = 22;

  ApiClient? get _api => widget.store.api;

  @override
  void initState() {
    super.initState();
    _breathe = AnimationController(vsync: this, duration: const Duration(milliseconds: 2400))..repeat(reverse: true);
    _ripple = AnimationController(vsync: this, duration: const Duration(milliseconds: 900));
    _wave = AnimationController(vsync: this, duration: const Duration(milliseconds: 1200))..repeat();
    widget.store.addVoiceRealtimeListener(_onVoiceEvent);
    _playerDone = _player.onPlayerComplete.listen((_) => _onPlayComplete());
    unawaited(_bootstrap());
  }

  @override
  void dispose() {
    _hangingUp = true;
    unawaited(_ampSub?.cancel());
    widget.store.removeVoiceRealtimeListener(_onVoiceEvent);
    _playerDone?.cancel();
    unawaited(_player.stop());
    _player.dispose();
    _breathe.dispose();
    _ripple.dispose();
    _wave.dispose();
    _scroll.dispose();
    if (_recording) {
      unawaited(VoiceRecorder.stop(cancel: true));
    }
    final api = _api;
    if (api != null) {
      unawaited(api.voiceRealtimeHangup(convId: widget.convId).catchError((_) => <String, dynamic>{}));
    }
    super.dispose();
  }

  Future<void> _bootstrap() async {
    setState(() {
      _phase = VoiceUiPhase.connecting;
      _statusOverride = '连接中…';
    });
    await AppAudio.ensureConfigured();
    final api = _api;
    if (api == null) {
      if (mounted) {
        setState(() {
          _statusOverride = '未登录，无法语音通话';
          _phase = VoiceUiPhase.blocked;
        });
      }
      return;
    }
    try {
      unawaited(api.voiceRealtimeWarm());
      for (var i = 0; i < 40; i++) {
        final r = await api.voiceRealtimeReady(convId: widget.convId);
        if (!mounted || _hangingUp) return;

        if (r['need_fixed_voice'] == true) {
          setState(() {
            _ready = false;
            _phase = VoiceUiPhase.blocked;
            _statusOverride = '请先固定音色后再通话';
          });
          return;
        }

        final backend = (r['asr_backend'] ?? '').toString();
        final model = (r['asr_model'] ?? '').toString();
        if (backend.isNotEmpty) {
          _asrInfo = model.isEmpty ? backend : '$backend · $model';
        }

        final ready = r['ready'] == true;
        if (ready) {
          setState(() {
            _ready = true;
            _inCall = true;
            _phase = VoiceUiPhase.inCall;
            _statusOverride = '';
          });
          await _startListenLoop();
          return;
        }
        await Future<void>.delayed(const Duration(milliseconds: 500));
      }
      if (mounted) {
        setState(() {
          _ready = false;
          _phase = VoiceUiPhase.blocked;
          _statusOverride = '连接超时，点球体重试';
        });
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          _statusOverride = '连接失败，点球体重试';
          _phase = VoiceUiPhase.blocked;
        });
      }
    }
  }

  void _onVoiceEvent(String event, Map<String, dynamic> payload) {
    final tid = payload['turn_id']?.toString();
    if (_turnId != null && tid != null && tid != _turnId) return;
    if (!mounted || _hangingUp) return;

    switch (event) {
      case 'voice_asr':
        final text = payload['text']?.toString() ?? '';
        if (text.isNotEmpty) _pushCaption(mine: true, text: text);
        setState(() {
          _phase = VoiceUiPhase.thinking;
          _statusOverride = '';
        });
      case 'voice_status':
        final phase = payload['phase']?.toString() ?? '';
        setState(() {
          if (phase == 'asr' || phase == 'thinking') _phase = VoiceUiPhase.thinking;
          if (phase == 'speaking') _phase = VoiceUiPhase.speaking;
        });
      case 'voice_sentence':
        final text = payload['text']?.toString() ?? '';
        final fid = payload['file_id']?.toString() ?? '';
        if (text.isNotEmpty) _pushCaption(mine: false, text: text);
        if (fid.isNotEmpty) unawaited(_enqueuePlay(fid));
        setState(() => _phase = VoiceUiPhase.speaking);
      case 'voice_done':
        setState(() {
          _busyTurn = false;
          if (!_playing && _queue.isEmpty) {
            _phase = VoiceUiPhase.inCall;
          }
        });
        if (!_playing && _queue.isEmpty && !_muted && _inCall) {
          unawaited(_startListenLoop());
        }
      case 'voice_error':
        final code = payload['error_code']?.toString() ?? '';
        final err = payload['error']?.toString() ?? '出了点问题';
        setState(() {
          _busyTurn = false;
          if (code == 'need_fixed_voice') {
            _phase = VoiceUiPhase.blocked;
            _inCall = false;
            _statusOverride = err;
          } else {
            _phase = VoiceUiPhase.inCall;
            _statusOverride = err;
          }
        });
        if (code != 'need_fixed_voice' && !_muted && _inCall) {
          // 听不清等：短暂提示后继续听
          Future<void>.delayed(const Duration(milliseconds: 900), () {
            if (!mounted || _hangingUp || _muted || !_inCall || _busyTurn || _recording) return;
            setState(() => _statusOverride = '');
            unawaited(_startListenLoop());
          });
        }
    }
  }

  void _pushCaption({required bool mine, required String text}) {
    setState(() {
      _captions.add(_CaptionLine(mine: mine, full: text, revealed: text));
    });
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!_scroll.hasClients) return;
      _scroll.animateTo(
        _scroll.position.maxScrollExtent,
        duration: const Duration(milliseconds: 240),
        curve: Curves.easeOut,
      );
    });
  }

  Future<void> _enqueuePlay(String fileId) async {
    final api = _api;
    if (api == null) return;
    try {
      final cached = await AppAudio.getCache(fileId);
      Uint8List bytes;
      if (cached != null) {
        bytes = await cached.readAsBytes();
      } else {
        bytes = await api.downloadMediaBytes(fileId);
        await AppAudio.putCache(fileId, bytes);
      }
      if (!mounted || _hangingUp) return;
      _queue.add(_PlayItem(fileId: fileId, bytes: bytes));
      if (!_playing) unawaited(_pumpQueue());
    } catch (_) {}
  }

  Future<void> _pumpQueue() async {
    if (_playing) return;
    while (_queue.isNotEmpty && mounted && !_hangingUp) {
      final item = _queue.removeAt(0);
      _playing = true;
      setState(() => _phase = VoiceUiPhase.speaking);
      try {
        await AppAudio.playBytes(_player, item.bytes, cacheFileId: item.fileId);
        await _player.onPlayerComplete.first.timeout(
          const Duration(seconds: 60),
          onTimeout: () {},
        );
      } catch (_) {}
      _playing = false;
    }
    if (!mounted || _hangingUp) return;
    if (!_busyTurn) {
      setState(() => _phase = VoiceUiPhase.inCall);
      if (!_muted && _inCall) {
        unawaited(_startListenLoop());
      }
    }
  }

  void _onPlayComplete() {
    // pump 循环 await onPlayerComplete
  }

  Future<void> _interruptPlayback() async {
    _queue.clear();
    try {
      await _player.stop();
    } catch (_) {}
    _playing = false;
  }

  Future<void> _stopAmp() async {
    await _ampSub?.cancel();
    _ampSub = null;
  }

  Future<void> _startListenLoop() async {
    if (!_ready || !_inCall || _muted || _busyTurn || _recording || _hangingUp) return;
    if (_playing || _queue.isNotEmpty) return;
    final gen = ++_listenGen;
    await _interruptPlayback();
    try {
      await VoiceRecorder.start();
      if (!mounted || _hangingUp || gen != _listenGen) {
        await VoiceRecorder.stop(cancel: true);
        return;
      }
      _recording = true;
      _speechStarted = false;
      _silenceTicks = 0;
      _listenStarted = DateTime.now();
      _speechStartedAt = null;
      setState(() {
        _phase = VoiceUiPhase.listening;
        _statusOverride = '';
      });
      await _stopAmp();
      _ampSub = VoiceRecorder.onAmplitude().listen((amp) {
        if (!mounted || _hangingUp || gen != _listenGen || !_recording) return;
        _onAmp(amp);
      });
    } catch (e) {
      if (mounted) {
        setState(() {
          _statusOverride = '$e';
          _phase = VoiceUiPhase.inCall;
        });
      }
    }
  }

  void _onAmp(Amplitude amp) {
    final now = DateTime.now();
    final started = _listenStarted;
    if (started == null) return;

    final db = amp.current;
    final elapsed = now.difference(started);

    if (elapsed.inSeconds >= _maxListenSec) {
      if (_speechStarted) {
        unawaited(_stopListenAndSend());
      } else {
        // 一直没说话：丢掉重开一轮听，保持通话态
        unawaited(_restartListenQuiet());
      }
      return;
    }

    if (db >= _speechDb) {
      if (!_speechStarted) {
        _speechStarted = true;
        _speechStartedAt = now;
      }
      _silenceTicks = 0;
      return;
    }

    if (!_speechStarted) return;
    if (db > _silenceDb) {
      _silenceTicks = 0;
      return;
    }
    _silenceTicks += 1;
    final spokenMs = _speechStartedAt == null ? 0 : now.difference(_speechStartedAt!).inMilliseconds;
    if (_silenceTicks >= _silenceNeeded && spokenMs >= _minSpeechMs) {
      unawaited(_stopListenAndSend());
    }
  }

  Future<void> _restartListenQuiet() async {
    await _stopAmp();
    if (!_recording) return;
    _recording = false;
    await VoiceRecorder.stop(cancel: true);
    if (!mounted || _hangingUp || _muted || !_inCall) return;
    setState(() => _phase = VoiceUiPhase.inCall);
    await Future<void>.delayed(const Duration(milliseconds: 120));
    if (!mounted || _hangingUp || _muted || !_inCall || _busyTurn) return;
    await _startListenLoop();
  }

  Future<void> _stopListenAndSend() async {
    await _stopAmp();
    if (!_recording) return;
    _recording = false;
    final captured = await VoiceRecorder.stop();
    if (!mounted || _hangingUp) return;
    if (captured == null) {
      setState(() => _phase = VoiceUiPhase.inCall);
      if (!_muted && _inCall) unawaited(_startListenLoop());
      return;
    }
    if (captured.seconds < 1 && (_speechStartedAt == null ||
        DateTime.now().difference(_speechStartedAt!).inMilliseconds < _minSpeechMs)) {
      try {
        captured.file.deleteSync();
      } catch (_) {}
      setState(() => _phase = VoiceUiPhase.inCall);
      if (!_muted && _inCall) unawaited(_startListenLoop());
      return;
    }
    final api = _api;
    if (api == null) return;
    setState(() {
      _busyTurn = true;
      _phase = VoiceUiPhase.thinking;
      _statusOverride = '';
    });
    try {
      final bytes = await captured.file.readAsBytes();
      final r = await api.voiceRealtimeTurn(
        convId: widget.convId,
        bytes: Uint8List.fromList(bytes),
        filename: 'voice.m4a',
      );
      _turnId = r['turn_id']?.toString();
      try {
        captured.file.deleteSync();
      } catch (_) {}
    } catch (e) {
      if (mounted) {
        setState(() {
          _busyTurn = false;
          _phase = VoiceUiPhase.inCall;
          _statusOverride = '$e';
        });
        if (!_muted && _inCall) {
          Future<void>.delayed(const Duration(milliseconds: 800), () {
            if (!mounted || _hangingUp || _muted || !_inCall || _busyTurn) return;
            setState(() => _statusOverride = '');
            unawaited(_startListenLoop());
          });
        }
      }
    }
  }

  Future<void> _onOrbTap() async {
    HapticFeedback.lightImpact();
    _ripple
      ..reset()
      ..forward();

    if (_phase == VoiceUiPhase.blocked || !_ready) {
      await _bootstrap();
      return;
    }
    if (_muted) {
      setState(() => _muted = false);
      await _startListenLoop();
      return;
    }

    // 打断对方：立刻听你说
    if (_phase == VoiceUiPhase.speaking || _playing || _queue.isNotEmpty || _busyTurn) {
      _busyTurn = false;
      await _interruptPlayback();
      await _startListenLoop();
      return;
    }
  }

  Future<void> _hangup() async {
    if (_hangingUp) return;
    _hangingUp = true;
    _inCall = false;
    _listenGen++;
    await _stopAmp();
    if (_recording) {
      _recording = false;
      await VoiceRecorder.stop(cancel: true);
    }
    await _interruptPlayback();
    final api = _api;
    if (api != null) {
      unawaited(api.voiceRealtimeHangup(convId: widget.convId).catchError((_) => <String, dynamic>{}));
    }
    if (mounted) Navigator.pop(context);
  }

  String get _statusLabel {
    if (_statusOverride.isNotEmpty) return _statusOverride;
    if (_muted) return '已静音';
    switch (_phase) {
      case VoiceUiPhase.connecting:
        return '连接中…';
      case VoiceUiPhase.blocked:
        return _statusOverride.isNotEmpty ? _statusOverride : '无法通话';
      case VoiceUiPhase.inCall:
        return '通话中';
      case VoiceUiPhase.listening:
        return '正在听你说…';
      case VoiceUiPhase.thinking:
        return '${widget.peerName} 思考中…';
      case VoiceUiPhase.speaking:
        return '${widget.peerName} 正在说';
    }
  }

  Color get _accent {
    if (_muted) return const Color(0xFF6B7280);
    switch (_phase) {
      case VoiceUiPhase.connecting:
      case VoiceUiPhase.blocked:
        return const Color(0xFF94A3B8);
      case VoiceUiPhase.inCall:
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
                  if (_asrInfo.isNotEmpty) _buildAsrChip(),
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
                      api: _api,
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
            Row(
              children: [
                _TopIconBtn(
                  icon: Icons.close_rounded,
                  onTap: () => unawaited(_hangup()),
                ),
                const Spacer(),
                _TopChipBtn(
                  icon: _showCaptions ? Icons.subtitles_rounded : Icons.subtitles_off_rounded,
                  label: '字幕',
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
    final connecting = _phase == VoiceUiPhase.connecting;
    return Center(
      child: GestureDetector(
        onTap: () => unawaited(_onOrbTap()),
        child: AnimatedBuilder(
          animation: Listenable.merge([_breathe, _ripple, _wave]),
          builder: (context, _) {
            final breath = 0.92 + _breathe.value * 0.08;
            final amp = switch (_phase) {
              VoiceUiPhase.listening => 0.55 + math.sin(_wave.value * math.pi * 2) * 0.35,
              VoiceUiPhase.speaking => 0.45 + math.sin(_wave.value * math.pi * 2) * 0.25,
              VoiceUiPhase.thinking || VoiceUiPhase.connecting => 0.35 + _breathe.value * 0.2,
              VoiceUiPhase.inCall || VoiceUiPhase.blocked => 0.22 + _breathe.value * 0.08,
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
                      if (connecting)
                        SizedBox(
                          width: 28,
                          height: 28,
                          child: CircularProgressIndicator(strokeWidth: 2.4, color: Colors.white.withValues(alpha: 0.85)),
                        )
                      else
                        Icon(
                          _muted
                              ? Icons.mic_off_rounded
                              : (_phase == VoiceUiPhase.speaking
                                  ? Icons.graphic_eq_rounded
                                  : Icons.mic_none_rounded),
                          color: Colors.white.withValues(alpha: 0.92),
                          size: 36,
                        ),
                      const SizedBox(height: 8),
                      Text(
                        connecting
                            ? '连接中'
                            : _muted
                                ? '已静音'
                                : switch (_phase) {
                                    VoiceUiPhase.listening => '聆听中',
                                    VoiceUiPhase.thinking => '思考中',
                                    VoiceUiPhase.speaking => '对方在说',
                                    VoiceUiPhase.inCall => '通话中',
                                    VoiceUiPhase.blocked => '未就绪',
                                    VoiceUiPhase.connecting => '连接中',
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
          : Container(
              height: 148,
              width: double.infinity,
              margin: const EdgeInsets.symmetric(horizontal: 20),
              padding: const EdgeInsets.fromLTRB(14, 12, 14, 10),
              decoration: BoxDecoration(
                color: Colors.white.withValues(alpha: 0.06),
                borderRadius: BorderRadius.circular(16),
                border: Border.all(color: Colors.white.withValues(alpha: 0.08)),
              ),
              child: _captions.isEmpty
                  ? const Center(
                      child: Text('通话字幕只在这里显示，不会进文字聊天', style: TextStyle(color: Colors.white38, fontSize: 13)),
                    )
                  : ListView.builder(
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
                              ],
                            ),
                          ),
                        );
                      },
                    ),
            ),
    );
  }

  Widget _buildAsrChip() {
    return Padding(
      padding: const EdgeInsets.only(top: 6),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
        decoration: BoxDecoration(
          color: const Color(0xFF1F2937),
          borderRadius: BorderRadius.circular(20),
          border: Border.all(color: const Color(0xFF374151)),
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Icon(Icons.hearing, size: 14, color: Color(0xFF93C5FD)),
            const SizedBox(width: 6),
            Text(
              '识别 $_asrInfo',
              style: const TextStyle(color: Color(0xFF93C5FD), fontSize: 11, fontWeight: FontWeight.w500),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildControls(double bottomInset) {
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
              final next = !_muted;
              setState(() => _muted = next);
              if (next) {
                _listenGen++;
                unawaited(_stopAmp());
                if (_recording) {
                  _recording = false;
                  unawaited(VoiceRecorder.stop(cancel: true));
                }
                unawaited(_interruptPlayback());
                setState(() => _phase = VoiceUiPhase.inCall);
              } else if (_inCall && _ready && !_busyTurn) {
                unawaited(_startListenLoop());
              }
            },
          ),
          _HangupBtn(onTap: () => unawaited(_hangup())),
          _CtrlBtn(
            icon: _speakerOn ? Icons.volume_up_rounded : Icons.phone_in_talk_rounded,
            label: _speakerOn ? '外放' : '听筒',
            active: _speakerOn,
            onTap: () {
              HapticFeedback.selectionClick();
              setState(() => _speakerOn = !_speakerOn);
              unawaited(AppAudio.ensureConfigured());
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

    final wave = Path();
    const n = 64;
    for (var i = 0; i <= n; i++) {
      final a = (i / n) * math.pi * 2;
      final wobble = math.sin(a * 5 + t * math.pi * 2) * amp * 10 + math.sin(a * 3 - t * math.pi * 4) * amp * 5;
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
