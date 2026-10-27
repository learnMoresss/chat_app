import 'package:flutter/material.dart';
import 'package:webview_flutter/webview_flutter.dart';

import '../store.dart';
import '../theme.dart';

/// 只读陪伴看板：WebView 加载同源 board.html（ECharts）。
class CompanionBoardPage extends StatefulWidget {
  const CompanionBoardPage({super.key, required this.store, required this.personaId, required this.title});

  final AppStore store;
  final String personaId;
  final String title;

  @override
  State<CompanionBoardPage> createState() => _CompanionBoardPageState();
}

class _CompanionBoardPageState extends State<CompanionBoardPage> {
  WebViewController? _controller;
  String? _error;

  @override
  void initState() {
    super.initState();
    _boot();
  }

  Future<void> _boot() async {
    final api = widget.store.api;
    if (api == null || api.accessToken == null || api.accessToken!.isEmpty) {
      setState(() => _error = '未登录');
      return;
    }
    final url = '${api.baseUrl}/api/v1/companion/personas/${widget.personaId}/board.html';
    final c = WebViewController()
      ..setJavaScriptMode(JavaScriptMode.unrestricted)
      ..setNavigationDelegate(
        NavigationDelegate(
          onWebResourceError: (e) {
            if (mounted) setState(() => _error = e.description);
          },
        ),
      );
    await c.loadRequest(
      Uri.parse(url),
      headers: {'Authorization': 'Bearer ${api.accessToken}'},
    );
    if (mounted) setState(() => _controller = c);
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.bg,
      appBar: AppBar(
        title: Text('${widget.title} · 陪伴看板'),
        actions: [
          IconButton(
            icon: const Icon(Icons.refresh),
            onPressed: () {
              setState(() {
                _error = null;
                _controller = null;
              });
              _boot();
            },
          ),
        ],
      ),
      body: _error != null
          ? Center(child: Text(_error!, style: const TextStyle(color: AppColors.sub)))
          : _controller == null
              ? const Center(child: CircularProgressIndicator())
              : WebViewWidget(controller: _controller!),
    );
  }
}
