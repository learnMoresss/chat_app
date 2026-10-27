import 'dart:async';
import 'dart:convert';

import 'package:web_socket_channel/web_socket_channel.dart';

typedef RealtimeHandler = void Function(String event, Map<String, dynamic> payload);
typedef ConnectionChanged = void Function(bool online);

/// 一条长连接：新消息推送 + 心跳。
/// [urlBuilder] 每次连接/重连取最新 token；[onBeforeReconnect] 可先 refresh。
class RealtimeClient {
  RealtimeClient({
    required this.urlBuilder,
    required this.onEvent,
    this.onConnectionChanged,
    this.onBeforeReconnect,
  });

  final String Function() urlBuilder;
  final RealtimeHandler onEvent;
  final ConnectionChanged? onConnectionChanged;
  final Future<void> Function()? onBeforeReconnect;

  WebSocketChannel? _channel;
  Timer? _ping;
  bool _closed = false;
  bool _reconnecting = false;
  int _backoffSec = 2;

  void connect() {
    _closed = false;
    final url = urlBuilder();
    try {
      _channel = WebSocketChannel.connect(Uri.parse(url));
    } catch (_) {
      onConnectionChanged?.call(false);
      _scheduleReconnect();
      return;
    }
    onConnectionChanged?.call(true);
    _backoffSec = 2;
    _channel!.stream.listen(
      (raw) {
        try {
          final map = jsonDecode(raw as String) as Map<String, dynamic>;
          final event = map['event'] as String? ?? '';
          final payload = Map<String, dynamic>.from(map['payload'] as Map? ?? {});
          if (event == 'ping') {
            _channel?.sink.add(jsonEncode({'type': 'ping'}));
            return;
          }
          if (event == 'auth_error') {
            onConnectionChanged?.call(false);
            _channel?.sink.close();
            _scheduleReconnect(forceRefresh: true);
            return;
          }
          onEvent(event, payload);
        } catch (_) {}
      },
      onDone: () {
        onConnectionChanged?.call(false);
        _scheduleReconnect();
      },
      onError: (_) {
        onConnectionChanged?.call(false);
        _scheduleReconnect();
      },
      cancelOnError: true,
    );
    _ping?.cancel();
    _ping = Timer.periodic(const Duration(seconds: 30), (_) {
      _channel?.sink.add('ping');
    });
  }

  void _scheduleReconnect({bool forceRefresh = false}) {
    _ping?.cancel();
    if (_closed || _reconnecting) return;
    _reconnecting = true;
    final wait = _backoffSec;
    _backoffSec = (_backoffSec * 2).clamp(2, 30);
    Future<void>.delayed(Duration(seconds: wait), () async {
      _reconnecting = false;
      if (_closed) return;
      if (forceRefresh || onBeforeReconnect != null) {
        try {
          await onBeforeReconnect?.call();
        } catch (_) {}
      }
      if (!_closed) connect();
    });
  }

  void close() {
    _closed = true;
    _reconnecting = false;
    _ping?.cancel();
    _channel?.sink.close();
    _channel = null;
  }
}
