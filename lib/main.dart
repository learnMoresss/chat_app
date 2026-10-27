import 'dart:io';

import 'package:audio_service/audio_service.dart';
import 'package:flutter/material.dart';

import 'media/app_audio.dart';
import 'media/listen_session.dart';
import 'screens/bootstrap.dart';
import 'store.dart';
import 'theme.dart';
import 'widgets/listen_mini_pip.dart';

/// 跳板自签证书：仅放行产品公网 IP（119.45.30.73）。
class _PublicJumpHttpOverrides extends HttpOverrides {
  @override
  HttpClient createHttpClient(SecurityContext? context) {
    final client = super.createHttpClient(context);
    client.badCertificateCallback = (cert, host, port) => host == '119.45.30.73';
    return client;
  }
}

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  HttpOverrides.global = _PublicJumpHttpOverrides();
  // 模拟器 / 真机：playback + 扬声器，声音才从 Mac / 外放出来
  try {
    await AppAudio.ensureConfigured();
  } catch (_) {}
  runApp(const ChatApp());
}

class ChatApp extends StatelessWidget {
  const ChatApp({super.key});

  @override
  Widget build(BuildContext context) {
    return AudioServiceWidget(
      child: MaterialApp(
        navigatorKey: ListenSessionController.instance.navigatorKey,
        debugShowCheckedModeBanner: false,
        title: '未读',
        theme: appTheme(),
        home: const BootstrapPage(),
        // 听歌浮窗挂在根 Navigator 之上；同时监听 session，登录后/收起时都能刷出来
        builder: (context, child) {
          return ListenableBuilder(
            listenable: ListenSessionController.instance,
            builder: (context, _) {
              final store = AppStore.active;
              final content = child ?? const SizedBox.shrink();
              if (store == null) return content;
              return ListenMiniPipHost(store: store, child: content);
            },
          );
        },
      ),
    );
  }
}
