import 'dart:async';

import 'package:flutter/material.dart';

import '../media/listen_session.dart';
import '../models.dart';
import '../store.dart';
import '../theme.dart';

/// 人设翻唱过的歌（曲库 cover）。
class PersonaSungPage extends StatefulWidget {
  const PersonaSungPage({super.key, required this.store, required this.persona});
  final AppStore store;
  final Persona persona;

  @override
  State<PersonaSungPage> createState() => _PersonaSungPageState();
}

class _PersonaSungPageState extends State<PersonaSungPage> {
  var _loading = true;
  String? _err;
  List<Map<String, dynamic>> _items = const [];

  @override
  void initState() {
    super.initState();
    unawaited(_load());
  }

  Future<void> _load() async {
    final api = widget.store.api;
    if (api == null) {
      setState(() {
        _loading = false;
        _err = '未登录';
      });
      return;
    }
    setState(() {
      _loading = true;
      _err = null;
    });
    try {
      final raw = await api.listPersonaSung(widget.persona.id);
      if (!mounted) return;
      setState(() {
        _items = [
          for (final e in raw)
            if (e is Map) Map<String, dynamic>.from(e),
        ];
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _err = '$e';
      });
    }
  }

  String _fmtDur(int secs) {
    if (secs <= 0) return '--:--';
    final m = secs ~/ 60;
    final s = (secs % 60).toString().padLeft(2, '0');
    return '$m:$s';
  }

  @override
  Widget build(BuildContext context) {
    final name = widget.persona.display;
    return Scaffold(
      backgroundColor: AppColors.bg,
      appBar: AppBar(
        title: Text('$name唱过的歌'),
        actions: [
          IconButton(onPressed: _loading ? null : _load, icon: const Icon(Icons.refresh)),
        ],
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _err != null
              ? Center(child: Text(_err!, style: const TextStyle(color: AppColors.badge)))
              : _items.isEmpty
                  ? const Center(child: Text('还没有翻唱记录', style: TextStyle(color: AppColors.muted)))
                  : ListView.separated(
                      itemCount: _items.length,
                      separatorBuilder: (_, __) => const Divider(height: 1),
                      itemBuilder: (context, i) {
                        final it = _items[i];
                        final title = (it['title']?.toString() ?? '翻唱').trim();
                        final artist = (it['artist']?.toString() ?? '').trim();
                        final secs = it['seconds'] is num
                            ? (it['seconds'] as num).toInt()
                            : int.tryParse('${it['seconds']}') ?? 0;
                        final sub = [
                          if (artist.isNotEmpty) artist,
                          '翻唱',
                          _fmtDur(secs),
                        ].join(' · ');
                        return ListTile(
                          leading: Container(
                            width: 44,
                            height: 44,
                            decoration: BoxDecoration(
                              borderRadius: BorderRadius.circular(8),
                              color: const Color(0xFF2A2A2A),
                            ),
                            child: const Icon(Icons.music_note_rounded, color: Colors.white70),
                          ),
                          title: Text(title, maxLines: 1, overflow: TextOverflow.ellipsis),
                          subtitle: Text(sub, maxLines: 1, overflow: TextOverflow.ellipsis),
                          trailing: const Icon(Icons.play_arrow_rounded, color: Color(0xFF07C160)),
                          onTap: () async {
                            final api = widget.store.api;
                            final fid = (it['file_id']?.toString() ?? '').trim();
                            if (api == null || fid.isEmpty) return;
                            final mid = (it['message_id']?.toString() ?? fid).trim();
                            try {
                              await ListenSessionController.instance.playCoverInPip(
                                sessionId: 'sing_$mid',
                                audioUrl: api.mediaFileAudioUrl(fid),
                                headers: api.authHeaders(),
                                convId: it['conv_id']?.toString(),
                                personaName: widget.persona.display,
                                title: title,
                                artist: artist,
                                duration: secs > 0 ? Duration(seconds: secs) : null,
                              );
                            } catch (e) {
                              if (!context.mounted) return;
                              ScaffoldMessenger.of(context).showSnackBar(
                                SnackBar(content: Text('播放失败：$e')),
                              );
                            }
                          },
                        );
                      },
                    ),
    );
  }
}
