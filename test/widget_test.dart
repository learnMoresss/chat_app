import 'package:chat_app/main.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  testWidgets('欢迎页拆开，随便填也能进主界面', (tester) async {
    await tester.pumpWidget(const ChatApp());
    expect(find.text('幻信'), findsOneWidget);
    expect(find.text('连接服务器'), findsOneWidget);
    expect(find.text('本机演示'), findsOneWidget);

    await tester.tap(find.text('连接服务器'));
    await tester.pumpAndSettle();
    expect(find.text('连接服务器'), findsWidgets);
    await tester.enterText(find.byType(TextField), '随便写');
    await tester.tap(find.text('继续'));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 500));
    await tester.pumpAndSettle();

    expect(find.text('登录'), findsWidgets);
    await tester.enterText(find.byType(TextField).first, '阿宁');
    await tester.tap(find.widgetWithText(FilledButton, '登录'));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 500));
    await tester.pumpAndSettle();

    expect(find.text('微信'), findsWidgets);
  });

  testWidgets('本机演示一键进入', (tester) async {
    await tester.pumpWidget(const ChatApp());
    await tester.tap(find.text('本机演示'));
    await tester.pumpAndSettle();
    expect(find.text('微信'), findsWidgets);
  });
}
