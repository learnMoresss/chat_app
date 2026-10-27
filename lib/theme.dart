import 'package:flutter/material.dart';

class AppColors {
  static const green = Color(0xFF07C160);
  static const bg = Color(0xFFEDEDED);
  static const mine = Color(0xFF95EC69);
  static const card = Colors.white;
  static const line = Color(0xFFE5E5E5);
  static const muted = Color(0xFFB2B2B2);
  static const sub = Color(0xFF888888);
  static const name = Color(0xFF576B95);
  static const badge = Color(0xFFFA5151);
  static const pinned = Color(0xFFEDEDED);
  static const chip = Color(0xFFD5D5D5);
}

/// 去掉 Android 默认「缩小消失」缩放转场；各平台统一侧滑/淡入，无 zoom。
class _NoZoomPageTransitionsBuilder extends PageTransitionsBuilder {
  const _NoZoomPageTransitionsBuilder();

  @override
  Widget buildTransitions<T>(
    PageRoute<T> route,
    BuildContext context,
    Animation<double> animation,
    Animation<double> secondaryAnimation,
    Widget child,
  ) {
    return FadeTransition(
      opacity: CurvedAnimation(parent: animation, curve: Curves.easeOut),
      child: child,
    );
  }
}

ThemeData appTheme() {
  return ThemeData(
    useMaterial3: false,
    scaffoldBackgroundColor: AppColors.bg,
    splashColor: const Color(0x11000000),
    highlightColor: const Color(0x11000000),
    dividerColor: AppColors.line,
    primaryColor: AppColors.green,
    textSelectionTheme: const TextSelectionThemeData(cursorColor: AppColors.green),
    pageTransitionsTheme: PageTransitionsTheme(
      builders: {
        for (final p in TargetPlatform.values) p: const _NoZoomPageTransitionsBuilder(),
      },
    ),
    appBarTheme: const AppBarTheme(
      backgroundColor: AppColors.bg,
      foregroundColor: Color(0xFF111111),
      elevation: 0.5,
      centerTitle: true,
      titleTextStyle: TextStyle(color: Color(0xFF111111), fontSize: 17, fontWeight: FontWeight.w600),
    ),
    filledButtonTheme: FilledButtonThemeData(
      style: FilledButton.styleFrom(backgroundColor: AppColors.green, foregroundColor: Colors.white),
    ),
  );
}
