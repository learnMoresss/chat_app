import 'dart:convert';

import 'package:flutter_secure_storage/flutter_secure_storage.dart';

import 'api_client.dart';

/// 当前登录会话（令牌放安全存储）。退出后仍保留服务器地址与用户名，方便下次填表。
class SessionStore {
  SessionStore({FlutterSecureStorage? storage})
      : _storage = storage ??
            const FlutterSecureStorage(
              // v11 已无 EncryptedSharedPreferences；resetOnError 避免坏密钥导致写入死锁
              aOptions: AndroidOptions(resetOnError: true),
            );

  static const _kServer = 'chat.server_url';
  static const _kAccess = 'chat.access_token';
  static const _kRefresh = 'chat.refresh_token';
  static const _kUser = 'chat.user_json';
  static const _kLastUser = 'chat.last_username';

  final FlutterSecureStorage _storage;

  Future<void> _write(String key, String value) async {
    await _storage.write(key: key, value: value).timeout(const Duration(seconds: 8));
  }

  Future<void> save({
    required String serverUrl,
    required AuthTokens tokens,
  }) async {
    await _write(_kServer, serverUrl);
    await _write(_kAccess, tokens.accessToken);
    await _write(_kRefresh, tokens.refreshToken);
    await _write(_kUser, jsonEncode(tokens.user));
    final name = tokens.user['username'] as String?;
    if (name != null && name.isNotEmpty) {
      await _write(_kLastUser, name);
    }
  }

  Future<SavedSession?> load() async {
    var server = await _storage.read(key: _kServer);
    final access = await _storage.read(key: _kAccess);
    final refresh = await _storage.read(key: _kRefresh);
    final userRaw = await _storage.read(key: _kUser);
    if (server == null || access == null || refresh == null || userRaw == null) return null;
    // iOS 模拟器里若误存了本机地址，改走公网跳板（与 auth 默认一致）
    if (server.contains('127.0.0.1') || server.contains('localhost')) {
      server = 'https://119.45.30.73:8443/chat-api';
      await _storage.write(key: _kServer, value: server);
    }
    return SavedSession(
      serverUrl: server,
      accessToken: access,
      refreshToken: refresh,
      user: Map<String, dynamic>.from(jsonDecode(userRaw) as Map),
    );
  }

  Future<String?> lastServerUrl() => _storage.read(key: _kServer);

  Future<String?> lastUsername() => _storage.read(key: _kLastUser);

  /// 仅改服务器地址，保留令牌（设置页切换后端时用）。
  Future<void> setServerUrl(String serverUrl) async {
    await _storage.write(key: _kServer, value: serverUrl);
  }

  /// 退出登录：清令牌，保留服务器与上次用户名。
  Future<void> clear() async {
    await _storage.delete(key: _kAccess);
    await _storage.delete(key: _kRefresh);
    await _storage.delete(key: _kUser);
  }
}

class SavedSession {
  SavedSession({
    required this.serverUrl,
    required this.accessToken,
    required this.refreshToken,
    required this.user,
  });

  final String serverUrl;
  final String accessToken;
  final String refreshToken;
  final Map<String, dynamic> user;
}
