import 'dart:ui';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../media/listen_session.dart';
import '../store.dart';
import '../theme.dart';

/// 纯样式预览：不连曲库；可测收起小窗 / 关闭。
class ListenStylePreviewPage extends StatelessWidget {
  const ListenStylePreviewPage({super.key, this.store});

  final AppStore? store;

  static const _tracks = [
    ('夜空中最亮的星', '逃跑计划'),
    ('晴天', '周杰伦'),
    ('起风了', '买辣椒也用券'),
    ('光年之外', 'G.E.M.邓紫棋'),
    ('平凡之路', '朴树'),
    ('海阔天空', 'Beyond'),
    ('匆匆那年', '王菲'),
    ('理想三旬', '陈鸿宇'),
  ];

  static const _bg = Color(0xFF0B0B0F);
  static const _top = Color(0xFF2A1840);

  @override
  Widget build(BuildContext context) {
    const current = 0;
    const playing = true;
    const name = '雪儿';

    return AnnotatedRegion<SystemUiOverlayStyle>(
      value: const SystemUiOverlayStyle(
        statusBarColor: _top,
        statusBarIconBrightness: Brightness.light,
        statusBarBrightness: Brightness.dark,
        systemNavigationBarColor: _bg,
        systemNavigationBarIconBrightness: Brightness.light,
      ),
      child: Scaffold(
        backgroundColor: _bg,
        body: Stack(
          fit: StackFit.expand,
          children: [
            DecoratedBox(
              decoration: BoxDecoration(
                gradient: LinearGradient(
                  begin: Alignment.topCenter,
                  end: Alignment.bottomCenter,
                  colors: [
                    _top.withValues(alpha: 0.95),
                    _bg,
                    const Color(0xFF121018),
                  ],
                ),
              ),
            ),
            SafeArea(
              child: Column(
                children: [
                  Padding(
                    padding: const EdgeInsets.fromLTRB(8, 4, 8, 0),
                    child: Row(
                      children: [
                        IconButton(
                          tooltip: '收起',
                          onPressed: () {
                            ListenSessionController.instance.minimize(
                              sessionId: 'preview',
                              personaName: name,
                              title: _tracks[current].$1,
                              artist: _tracks[current].$2,
                              playing: playing,
                            );
                            Navigator.pop(context);
                          },
                          icon: const Icon(Icons.keyboard_arrow_down, color: Colors.white, size: 32),
                        ),
                        const Expanded(
                          child: Text(
                            '和 $name 一起听',
                            textAlign: TextAlign.center,
                            style: TextStyle(color: Colors.white, fontSize: 16, fontWeight: FontWeight.w600),
                          ),
                        ),
                        TextButton(
                          onPressed: () async {
                            await ListenSessionController.instance.closeAndStop();
                            if (context.mounted) Navigator.pop(context);
                          },
                          child: const Text('关闭', style: TextStyle(color: Colors.white70)),
                        ),
                      ],
                    ),
                  ),
                  const SizedBox(height: 28),
                  ClipRRect(
                    borderRadius: BorderRadius.circular(20),
                    child: BackdropFilter(
                      filter: ImageFilter.blur(sigmaX: 12, sigmaY: 12),
                      child: Container(
                        width: 220,
                        height: 220,
                        alignment: Alignment.center,
                        decoration: BoxDecoration(
                          color: Colors.white.withValues(alpha: 0.08),
                          borderRadius: BorderRadius.circular(20),
                          border: Border.all(color: Colors.white24),
                        ),
                        child: const Icon(Icons.music_note_rounded, size: 88, color: Colors.white70),
                      ),
                    ),
                  ),
                  const SizedBox(height: 28),
                  const Padding(
                    padding: EdgeInsets.symmetric(horizontal: 28),
                    child: Text(
                      '夜空中最亮的星',
                      maxLines: 2,
                      textAlign: TextAlign.center,
                      overflow: TextOverflow.ellipsis,
                      style: TextStyle(color: Colors.white, fontSize: 22, fontWeight: FontWeight.w700, height: 1.25),
                    ),
                  ),
                  const SizedBox(height: 8),
                  Text(
                    '逃跑计划',
                    style: TextStyle(color: Colors.white.withValues(alpha: 0.5), fontSize: 14),
                  ),
                  const SizedBox(height: 28),
                  Row(
                    mainAxisAlignment: MainAxisAlignment.center,
                    children: [
                      IconButton(
                        iconSize: 36,
                        color: Colors.white,
                        onPressed: () {},
                        icon: const Icon(Icons.skip_previous_rounded),
                      ),
                      const SizedBox(width: 12),
                      Material(
                        color: Colors.white,
                        shape: const CircleBorder(),
                        child: InkWell(
                          customBorder: const CircleBorder(),
                          onTap: () {},
                          child: const SizedBox(
                            width: 68,
                            height: 68,
                            child: Icon(Icons.pause_rounded, size: 40, color: Color(0xFF111111)),
                          ),
                        ),
                      ),
                      const SizedBox(width: 12),
                      IconButton(
                        iconSize: 36,
                        color: Colors.white,
                        onPressed: () {},
                        icon: const Icon(Icons.skip_next_rounded),
                      ),
                    ],
                  ),
                  const SizedBox(height: 20),
                  Expanded(
                    child: ListView.builder(
                      padding: const EdgeInsets.fromLTRB(16, 0, 16, 24),
                      itemCount: _tracks.length,
                      itemBuilder: (context, i) {
                        final t = _tracks[i];
                        final on = i == current;
                        return ListTile(
                          dense: true,
                          onTap: () {},
                          leading: Text(
                            '${i + 1}'.padLeft(2, '0'),
                            style: TextStyle(
                              color: on ? AppColors.green : Colors.white38,
                              fontSize: 13,
                              fontFeatures: const [FontFeature.tabularFigures()],
                            ),
                          ),
                          title: Text(
                            t.$1,
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: TextStyle(
                              color: on ? Colors.white : Colors.white70,
                              fontWeight: on ? FontWeight.w600 : FontWeight.w400,
                            ),
                          ),
                          subtitle: Text(
                            t.$2,
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: const TextStyle(color: Colors.white38, fontSize: 12),
                          ),
                          trailing: on ? const Icon(Icons.equalizer, color: AppColors.green, size: 18) : null,
                        );
                      },
                    ),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

/// 从任意处打开样式预览（带 Shell 小窗宿主时点左上可测悬浮）。
Future<void> openListenStylePreview(BuildContext context, {AppStore? store}) async {
  await Navigator.of(context).push(
    MaterialPageRoute(builder: (_) => ListenStylePreviewPage(store: store)),
  );
}
