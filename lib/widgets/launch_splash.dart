import 'dart:async';

import 'package:flutter/material.dart';
import 'package:webview_flutter/webview_flutter.dart';

/// 开屏动画叠加层：与会话恢复并行，不阻塞启动路径。
///
/// - 不 await WebView；失败时静默退回白底
/// - [dismiss] 为 true 后立刻 fade，最短展示由调用方控制（默认不等待完整动画）
class LaunchSplashOverlay extends StatefulWidget {
  const LaunchSplashOverlay({
    super.key,
    required this.dismiss,
    this.onDismissed,
  });

  final bool dismiss;
  final VoidCallback? onDismissed;

  @override
  State<LaunchSplashOverlay> createState() => _LaunchSplashOverlayState();
}

class _LaunchSplashOverlayState extends State<LaunchSplashOverlay> {
  WebViewController? _controller;
  double _opacity = 1;
  bool _gone = false;
  bool _pageReady = false;
  Timer? _forceGone;

  @override
  void initState() {
    super.initState();
    // 异步装载，绝不挡住 Bootstrap._restore
    unawaited(_boot());
  }

  @override
  void dispose() {
    _forceGone?.cancel();
    super.dispose();
  }

  void _forceDismissSoon() {
    _forceGone?.cancel();
    _forceGone = Timer(const Duration(milliseconds: 700), () {
      if (!mounted || _gone) return;
      setState(() {
        _opacity = 0;
        _gone = true;
      });
      widget.onDismissed?.call();
    });
  }

  Future<void> _boot() async {
    try {
      final c = WebViewController()
        ..setJavaScriptMode(JavaScriptMode.unrestricted)
        ..setBackgroundColor(const Color(0xFFFFFFFF))
        ..setNavigationDelegate(
          NavigationDelegate(
            onPageFinished: (_) {
              _pageReady = true;
              if (widget.dismiss) _tellFinish();
            },
            onWebResourceError: (_) {
              // 资源失败不拖启动：直接淡出
              if (mounted && !widget.dismiss) {
                _fadeOut();
              }
            },
          ),
        );
      await c.loadFlutterAsset('assets/splash/huanxin_splash.html');
      if (!mounted) return;
      setState(() => _controller = c);
      if (widget.dismiss) _tellFinish();
    } catch (e, st) {
      debugPrint('launch splash load failed: $e\n$st');
      if (mounted) _fadeOut();
    }
  }

  void _tellFinish() {
    final c = _controller;
    if (c == null || !_pageReady) return;
    unawaited(
      c.runJavaScript(
        'window.CompanionSplash&&CompanionSplash.finish()',
      ),
    );
  }

  void _fadeOut() {
    if (_gone || !mounted) return;
    setState(() => _opacity = 0);
  }

  @override
  void didUpdateWidget(covariant LaunchSplashOverlay oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (widget.dismiss && !oldWidget.dismiss) {
      _tellFinish();
      _fadeOut();
      _forceDismissSoon();
    }
  }

  @override
  Widget build(BuildContext context) {
    if (_gone) return const SizedBox.shrink();
    return IgnorePointer(
      child: AnimatedOpacity(
        opacity: _opacity,
        duration: const Duration(milliseconds: 280),
        curve: Curves.easeOut,
        onEnd: () {
          if (_opacity == 0 && !_gone && mounted) {
            setState(() => _gone = true);
            widget.onDismissed?.call();
          }
        },
        child: ColoredBox(
          color: const Color(0xFFFFFFFF),
          child: _controller == null
              ? const SizedBox.expand()
              : WebViewWidget(controller: _controller!),
        ),
      ),
    );
  }
}
