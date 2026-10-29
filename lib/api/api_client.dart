import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:http/http.dart' as http;
import 'package:http/http.dart' show MultipartFile, MultipartRequest;
import 'package:http/io_client.dart';
import 'package:http_parser/http_parser.dart';

/// 把 FastAPI 的 detail（字符串或校验错误列表）收成用户能看懂的中文。
String _friendlyDetail(Object? detail, int statusCode) {
  if (detail is String && detail.trim().isNotEmpty) return detail.trim();
  if (detail is Map) {
    final err = detail['error'] ?? detail['message'] ?? detail['msg'];
    if (err != null && '$err'.trim().isNotEmpty) return '$err'.trim();
  }
  if (detail is List && detail.isNotEmpty) {
    final parts = <String>[];
    for (final item in detail) {
      if (item is Map) {
        final loc = item['loc'];
        final field = loc is List && loc.isNotEmpty ? '${loc.last}' : '';
        final type = '${item['type'] ?? ''}';
        final raw = '${item['msg'] ?? ''}';
        if (field == 'password' || field == 'new_password') {
          if (type.contains('too_short') || raw.contains('at least')) {
            parts.add('密码至少 8 位');
            continue;
          }
          if (type.contains('too_long')) {
            parts.add('密码太长');
            continue;
          }
        }
        if (field == 'username' && (type.contains('too_short') || raw.contains('at least'))) {
          parts.add('用户名至少 2 个字符');
          continue;
        }
        if (raw.isNotEmpty && !raw.startsWith('Value error') && !raw.contains('String should')) {
          parts.add(raw);
          continue;
        }
        if (field.isNotEmpty) {
          parts.add('$field 填写有误');
          continue;
        }
      } else if (item != null) {
        final s = item.toString();
        if (s.isNotEmpty) parts.add(s);
      }
    }
    if (parts.isNotEmpty) return parts.join('；');
  }
  if (statusCode == 422) return '填写有误，请检查后再试';
  return '请求失败（$statusCode）';
}

class ApiException implements Exception {
  ApiException(this.message, {this.statusCode, this.authNoise = false});
  final String message;
  final int? statusCode;
  /// 令牌类问题：已由客户端续签或应静默处理，UI 不要 Toast。
  final bool authNoise;

  @override
  String toString() => message;

  bool get isAuthFailure =>
      authNoise ||
      statusCode == 401 ||
      statusCode == 403 ||
      message.contains('令牌') ||
      message.contains('未登录') ||
      message.contains('登录已失效');
}

/// UI 是否应弹出错误：令牌续签噪声不弹；登录失败 / 网络错误要提示。
bool shouldToastApiError(Object e) {
  if (e is ApiException) {
    final m = e.message;
    // 用户主动登录/注册失败必须可见
    if (m.contains('用户名或密码') ||
        m.contains('账号已停用') ||
        m.contains('登录失败次数') ||
        m.contains('请填写') ||
        m.contains('邀请码') ||
        m.contains('已注册')) {
      return true;
    }
    // 仅静默「续签/无感鉴权」噪声
    if (e.authNoise || m.contains('登录已失效') || m.contains('令牌无效') || m.contains('令牌已过期')) {
      return false;
    }
    return true;
  }
  final s = e.toString();
  if (s.contains('登录已失效') || s.contains('令牌无效') || s.contains('令牌已过期')) return false;
  return true;
}

class ServerInfo {
  ServerInfo({
    required this.name,
    required this.version,
    required this.registrationMode,
    required this.minAppVersion,
    this.publicBaseUrl = '',
    this.tunnelProvider = 'none',
    this.tunnelActive = false,
    this.tunnelPublicUrl = '',
  });

  final String name;
  final String version;
  final String registrationMode;
  final String minAppVersion;
  final String publicBaseUrl;
  final String tunnelProvider;
  final bool tunnelActive;
  final String tunnelPublicUrl;

  factory ServerInfo.fromJson(Map<String, dynamic> j) => ServerInfo(
        name: j['name'] as String? ?? '',
        version: j['version'] as String? ?? '',
        registrationMode: j['registration_mode'] as String? ?? 'open',
        minAppVersion: j['min_app_version'] as String? ?? '1.0.0',
        publicBaseUrl: j['public_base_url'] as String? ?? '',
        tunnelProvider: j['tunnel_provider'] as String? ?? 'none',
        tunnelActive: j['tunnel_active'] as bool? ?? false,
        tunnelPublicUrl: j['tunnel_public_url'] as String? ?? '',
      );
}

class AuthTokens {
  AuthTokens({
    required this.accessToken,
    required this.refreshToken,
    required this.user,
  });

  final String accessToken;
  final String refreshToken;
  final Map<String, dynamic> user;

  factory AuthTokens.fromJson(Map<String, dynamic> j) => AuthTokens(
        accessToken: j['access_token'] as String,
        refreshToken: j['refresh_token'] as String,
        user: Map<String, dynamic>.from(j['user'] as Map),
      );
}

/// 把 SocketException / Connection refused 转成可读提示，便于区分「断联」和业务错误。
String _netFailMessage(Object e, {String action = '请求'}) {
  final s = e.toString();
  if (s.contains('Connection refused') || s.contains('errno = 61') || s.contains('Failed host lookup')) {
    return '本地服务已断开（连不上后端）。请确认 API 在跑，再重试$action';
  }
  if (s.contains('TimeoutException') || s.contains('timed out')) {
    return '$action超时，服务可能卡住或网络差';
  }
  return '$action失败：$e';
}

/// 规范化用户填的后端地址。
String normalizeServerUrl(String raw) {
  var s = raw.trim();
  if (s.isEmpty) return s;
  if (!s.contains('://')) s = 'http://$s';
  while (s.endsWith('/')) {
    s = s.substring(0, s.length - 1);
  }
  return s;
}

class ApiClient {
  ApiClient(this.baseUrl, {http.Client? httpClient}) : _http = httpClient ?? http.Client();

  final String baseUrl;
  final http.Client _http;
  String? accessToken;
  String? refreshToken;
  /// 续签成功后写回安全存储 / Store。
  Future<void> Function(AuthTokens tokens)? onTokensUpdated;

  Future<void>? _refreshInFlight;

  Uri _u(String path, [Map<String, String>? query]) {
    final base = Uri.parse(baseUrl);
    return base.replace(path: '${base.path}$path'.replaceAll('//', '/'), queryParameters: query);
  }

  bool _isRefreshPath(String path) => path.contains('/auth/refresh');

  /// 无感续签；并发请求共用一次 refresh。
  Future<void> silentRefresh() async {
    final inflight = _refreshInFlight;
    if (inflight != null) {
      await inflight;
      return;
    }
    final rt = refreshToken;
    if (rt == null || rt.isEmpty) {
      throw ApiException('登录已失效', statusCode: 401, authNoise: true);
    }
    final done = Completer<void>();
    _refreshInFlight = done.future;
    try {
      final j = await _json(
        'POST',
        '/api/v1/auth/refresh',
        body: {'refresh_token': rt},
        auth: false,
        allowAuthRetry: false,
      );
      final tokens = AuthTokens.fromJson(j);
      accessToken = tokens.accessToken;
      refreshToken = tokens.refreshToken;
      final cb = onTokensUpdated;
      if (cb != null) await cb(tokens);
      done.complete();
    } catch (e) {
      done.completeError(e);
      if (e is ApiException && (e.statusCode == 401 || e.message.contains('令牌'))) {
        throw ApiException('登录已失效', statusCode: 401, authNoise: true);
      }
      rethrow;
    } finally {
      _refreshInFlight = null;
    }
  }

  Future<Map<String, dynamic>> _json(
    String method,
    String path, {
    Map<String, dynamic>? body,
    Map<String, String>? query,
    bool auth = false,
    Duration timeout = const Duration(seconds: 12),
    bool allowAuthRetry = true,
  }) async {
    final headers = <String, String>{'Content-Type': 'application/json', 'Accept': 'application/json'};
    if (auth) {
      if (accessToken == null || accessToken!.isEmpty) {
        throw ApiException('未登录', statusCode: 401, authNoise: true);
      }
      headers['Authorization'] = 'Bearer $accessToken';
    }
    final uri = _u(path, query);
    late http.Response res;
    try {
      switch (method) {
        case 'GET':
          res = await _http.get(uri, headers: headers).timeout(timeout);
        case 'POST':
          res = await _http
              .post(uri, headers: headers, body: body == null ? null : jsonEncode(body))
              .timeout(timeout);
        case 'PATCH':
          res = await _http
              .patch(uri, headers: headers, body: body == null ? null : jsonEncode(body))
              .timeout(timeout);
        case 'DELETE':
          res = await _http.delete(uri, headers: headers).timeout(timeout);
        default:
          throw ApiException('不支持的方法 $method');
      }
    } on ApiException {
      rethrow;
    } catch (e) {
      throw ApiException(_netFailMessage(e));
    }
    Map<String, dynamic>? data;
    if (res.body.isNotEmpty) {
      try {
        final decoded = jsonDecode(res.body);
        if (decoded is Map<String, dynamic>) {
          data = decoded;
        } else if (decoded is List) {
          data = {'items': decoded};
        }
      } catch (_) {}
    }
    if (res.statusCode >= 400) {
      // access 过期：静默 refresh 后重试一次
      if (auth &&
          allowAuthRetry &&
          res.statusCode == 401 &&
          !_isRefreshPath(path) &&
          (refreshToken ?? '').isNotEmpty) {
        var refreshed = false;
        try {
          await silentRefresh();
          refreshed = true;
        } catch (_) {}
        if (refreshed) {
          return _json(
            method,
            path,
            body: body,
            query: query,
            auth: auth,
            timeout: timeout,
            allowAuthRetry: false,
          );
        }
      }
      final detail = data?['detail'];
      final msg = _friendlyDetail(detail, res.statusCode);
      final authNoise = res.statusCode == 401 ||
          res.statusCode == 403 ||
          msg.contains('令牌') ||
          msg.contains('未登录');
      throw ApiException(msg, statusCode: res.statusCode, authNoise: authNoise);
    }
    return data ?? {};
  }

  Future<ServerInfo> fetchInfo() async {
    final j = await _json('GET', '/api/info');
    return ServerInfo.fromJson(j);
  }

  Future<AuthTokens> register({
    required String username,
    required String password,
    String inviteCode = '',
    String displayName = '',
    String deviceName = 'iOS',
  }) async {
    final j = await _json('POST', '/api/v1/auth/register', body: {
      'username': username,
      'password': password,
      'invite_code': inviteCode,
      'display_name': displayName,
      'device_name': deviceName,
    });
    return AuthTokens.fromJson(j);
  }

  Future<AuthTokens> login({
    required String username,
    required String password,
    String deviceName = 'iOS',
  }) async {
    final j = await _json('POST', '/api/v1/auth/login', body: {
      'username': username,
      'password': password,
      'device_name': deviceName,
    });
    return AuthTokens.fromJson(j);
  }

  Future<AuthTokens> refresh(String token) async {
    final j = await _json(
      'POST',
      '/api/v1/auth/refresh',
      body: {'refresh_token': token},
      auth: false,
      allowAuthRetry: false,
    );
    final tokens = AuthTokens.fromJson(j);
    accessToken = tokens.accessToken;
    refreshToken = tokens.refreshToken;
    return tokens;
  }

  /// 供图片等非 _json 路径在 401 时调用。
  Future<bool> trySilentRefresh() async {
    try {
      await silentRefresh();
      return true;
    } catch (_) {
      return false;
    }
  }

  Future<Map<String, dynamic>> sync() => _json('GET', '/api/v1/sync', auth: true);

  Future<Map<String, dynamic>> syncDelta({required String after}) {
    return _json('GET', '/api/v1/sync/delta', auth: true, query: {'after': after});
  }

  Future<Map<String, dynamic>> patchConversationDraft(String convId, String draft) {
    return _json('PATCH', '/api/v1/conversations/$convId', auth: true, body: {'draft': draft});
  }

  Future<Map<String, dynamic>> patchConversation(
    String convId, {
    String? draft,
    String? bgName,
    String? bgFileId,
    bool? pinned,
    bool? muted,
    bool? hidden,
  }) {
    return _json('PATCH', '/api/v1/conversations/$convId', auth: true, body: {
      if (draft != null) 'draft': draft,
      if (bgName != null) 'bg_name': bgName,
      if (bgFileId != null) 'bg_file_id': bgFileId,
      if (pinned != null) 'pinned': pinned,
      if (muted != null) 'muted': muted,
      if (hidden != null) 'hidden': hidden,
    });
  }

  /// 微信式删除聊天：云端清消息并去掉会话。
  Future<void> deleteConversation(String convId) async {
    await _json('DELETE', '/api/v1/conversations/$convId', auth: true);
  }

  Future<void> deletePersona(String personaId) async {
    await _json('DELETE', '/api/v1/personas/$personaId', auth: true);
  }

  /// 从通讯录打开/恢复会话（含已删聊天的软删恢复）。
  Future<Map<String, dynamic>> openPersonaChat(String personaId) {
    return _json('POST', '/api/v1/personas/$personaId/open-chat', auth: true);
  }

  Future<Map<String, dynamic>> cleanResidue(String convId) {
    return _json('POST', '/api/v1/conversations/$convId/clean-residue', auth: true);
  }

  Future<Map<String, dynamic>> getPrefs() => _json('GET', '/api/v1/prefs', auth: true);

  Future<Map<String, dynamic>> putPrefs(Map<String, dynamic> body) {
    return _json('PUT', '/api/v1/prefs', auth: true, body: body);
  }

  Future<Map<String, dynamic>> sendMessage({
    required String convId,
    required String clientId,
    required String text,
    String kind = 'text',
    String? quoteId,
    List<String>? mediaIds,
    int seconds = 0,
    int bytes = 0,
  }) {
    return _json(
      'POST',
      '/api/v1/conversations/$convId/messages',
      auth: true,
      timeout: const Duration(seconds: 60),
      body: {
        'client_id': clientId,
        'text': text,
        'kind': kind,
        'quote_id': ?quoteId,
        'media_ids': mediaIds ?? const <String>[],
        'seconds': seconds,
        'bytes': bytes,
      },
    );
  }

  /// 聊天媒体上传，返回 file_id / auth_url。
  ///
  /// 走公网反向隧道时偶发卡顿：独立 HttpClient、更长 idle、超时强制掐线，
  /// 401 先续签再重试，避免「看起来一直转圈最后超时」。
  Future<Map<String, dynamic>> uploadChatMedia({
    required Uint8List bytes,
    required String filename,
    String kind = 'image',
    String contentType = 'application/octet-stream',
    bool allowAuthRetry = true,
    Duration timeout = const Duration(seconds: 120),
  }) async {
    if (accessToken == null || accessToken!.isEmpty) {
      throw ApiException('未登录', statusCode: 401, authNoise: true);
    }
    final uri = _u('/api/v1/media/chat-upload');
    final req = MultipartRequest('POST', uri);
    req.headers['Authorization'] = 'Bearer $accessToken';
    req.headers['Accept'] = 'application/json';
    req.fields['kind'] = kind;
    MediaType? mt;
    try {
      mt = MediaType.parse(contentType);
    } catch (_) {
      mt = null;
    }
    req.files.add(MultipartFile.fromBytes('file', bytes, filename: filename, contentType: mt));

    final io = HttpClient()
      ..connectionTimeout = const Duration(seconds: 30)
      // 隧道卡顿时不能 30s 就当空闲掐线
      ..idleTimeout = const Duration(minutes: 3)
      // 与 main.dart HttpOverrides 一致，独立 client 也放行跳板自签
      ..badCertificateCallback = (cert, host, port) => host == '119.45.30.73';
    final client = IOClient(io);
    try {
      final streamed = await client.send(req).timeout(timeout);
      final res = await http.Response.fromStream(streamed).timeout(
        const Duration(seconds: 30),
      );
      Map<String, dynamic>? data;
      if (res.body.isNotEmpty) {
        try {
          final decoded = jsonDecode(res.body);
          if (decoded is Map<String, dynamic>) data = decoded;
        } catch (_) {}
      }
      if (res.statusCode == 401 && allowAuthRetry && (refreshToken ?? '').isNotEmpty) {
        if (await trySilentRefresh()) {
          return await uploadChatMedia(
            bytes: bytes,
            filename: filename,
            kind: kind,
            contentType: contentType,
            allowAuthRetry: false,
            timeout: timeout,
          );
        }
        throw ApiException('登录已失效，请重新登录', statusCode: 401, authNoise: true);
      }
      if (res.statusCode >= 400) {
        final detail = data?['detail'];
        String msg;
        if (detail is String && detail.trim().isNotEmpty) {
          msg = detail.trim();
        } else if (detail is Map && detail['error'] != null) {
          msg = '${detail['error']}';
        } else {
          msg = '上传失败（${res.statusCode}）';
        }
        throw ApiException(
          msg,
          statusCode: res.statusCode,
          authNoise: res.statusCode == 401 || msg.contains('令牌'),
        );
      }
      return data ?? {};
    } on ApiException {
      rethrow;
    } on TimeoutException {
      try {
        io.close(force: true);
      } catch (_) {}
      throw ApiException('上传超时（公网链路拥堵），请再试一次', statusCode: 408);
    } catch (e) {
      throw ApiException(_netFailMessage(e, action: '上传'));
    } finally {
      try {
        client.close();
      } catch (_) {}
    }
  }

  String mediaAuthUrl(String fileId) {
    final u = _u('/api/v1/media/files/$fileId');
    return u.toString();
  }

  Future<void> markRead(String convId) async {
    await _json('POST', '/api/v1/conversations/$convId/read', auth: true);
  }

  Future<List<dynamic>> getJsonList(String path, {bool allowAuthRetry = true}) async {
    final headers = <String, String>{'Accept': 'application/json'};
    if (accessToken == null || accessToken!.isEmpty) {
      throw ApiException('未登录', statusCode: 401, authNoise: true);
    }
    headers['Authorization'] = 'Bearer $accessToken';
    final uri = _u(path);
    late http.Response res;
    try {
      res = await _http.get(uri, headers: headers).timeout(const Duration(seconds: 12));
    } catch (e) {
      throw ApiException(_netFailMessage(e));
    }
    if (res.statusCode == 401 && allowAuthRetry && (refreshToken ?? '').isNotEmpty) {
      if (await trySilentRefresh()) {
        return getJsonList(path, allowAuthRetry: false);
      }
    }
    if (res.statusCode >= 400) {
      throw ApiException(
        '请求失败（${res.statusCode}）',
        statusCode: res.statusCode,
        authNoise: res.statusCode == 401,
      );
    }
    final decoded = jsonDecode(res.body);
    if (decoded is List) return decoded;
    return [];
  }

  Future<Map<String, dynamic>> getChannel(String id) {
    return _json('GET', '/api/v1/channels/$id', auth: true);
  }

  Future<Map<String, dynamic>> createChannel({
    required String name,
    required String model,
    required String baseUrl,
    String? apiKey,
    bool shared = false,
  }) {
    return _json('POST', '/api/v1/channels', auth: true, body: {
      'name': name,
      'model': model,
      'base_url': baseUrl,
      'api_key': ?apiKey,
      'shared': shared,
    });
  }

  Future<Map<String, dynamic>> updateChannel({
    required String id,
    required String name,
    required String model,
    required String baseUrl,
    String? apiKey,
  }) {
    return _json('PATCH', '/api/v1/channels/$id', auth: true, body: {
      'name': name,
      'model': model,
      'base_url': baseUrl,
      'api_key': ?apiKey,
    });
  }

  Future<void> deleteChannel(String id) async {
    await _json('DELETE', '/api/v1/channels/$id', auth: true);
  }

  Future<Map<String, dynamic>> createPersona({
    required String name,
    String bio = '',
    int color = 0xFF2F6BFF,
    String channelId = '',
    bool greet = true,
    bool momentsOn = true,
    String avatarFileId = '',
  }) {
    return _json('POST', '/api/v1/personas', auth: true, body: {
      'name': name,
      'bio': bio,
      'color': color,
      'channel_id': channelId,
      'is_bot': true,
      'greet': greet,
      'moments_on': momentsOn,
      'avatar_file_id': avatarFileId,
    });
  }

  Future<Map<String, dynamic>> probeChannel(String id) {
    return _json('POST', '/api/v1/channels/$id/probe', auth: true, timeout: const Duration(seconds: 30));
  }

  Future<Map<String, dynamic>> patchPersona(String id, Map<String, dynamic> body) {
    return _json('PATCH', '/api/v1/personas/$id', auth: true, body: body);
  }

  Future<Map<String, dynamic>> patchMe({
    String? displayName,
    String? avatarFileId,
    String? signature,
    String? momentsCoverFileId,
  }) {
    final body = <String, dynamic>{};
    if (displayName != null) body['display_name'] = displayName;
    if (avatarFileId != null) body['avatar_file_id'] = avatarFileId;
    if (signature != null) body['signature'] = signature;
    if (momentsCoverFileId != null) body['moments_cover_file_id'] = momentsCoverFileId;
    return _json('PATCH', '/api/v1/auth/me', auth: true, body: body);
  }

  Future<Map<String, dynamic>> usageSummary({int days = 30}) {
    return _json('GET', '/api/v1/usage/summary', auth: true, query: {'days': '$days'});
  }

  Future<List<dynamic>> usageLogs({int limit = 50, bool allowAuthRetry = true}) async {
    final headers = <String, String>{'Accept': 'application/json'};
    if (accessToken == null || accessToken!.isEmpty) {
      throw ApiException('未登录', statusCode: 401, authNoise: true);
    }
    headers['Authorization'] = 'Bearer $accessToken';
    final uri = _u('/api/v1/usage/logs', {'limit': '$limit'});
    try {
      final res = await _http.get(uri, headers: headers).timeout(const Duration(seconds: 12));
      if (res.statusCode == 401 && allowAuthRetry && (refreshToken ?? '').isNotEmpty) {
        if (await trySilentRefresh()) {
          return await usageLogs(limit: limit, allowAuthRetry: false);
        }
      }
      if (res.statusCode >= 400) {
        throw ApiException(
          '请求失败（${res.statusCode}）',
          statusCode: res.statusCode,
          authNoise: res.statusCode == 401,
        );
      }
      final decoded = jsonDecode(res.body);
      return decoded is List ? decoded : [];
    } catch (e) {
      if (e is ApiException) rethrow;
      throw ApiException(_netFailMessage(e));
    }
  }

  Future<List<dynamic>> listMemories(String personaId) => getJsonList('/api/v1/personas/$personaId/memories');

  Future<List<dynamic>> listCapabilities() async {
    final j = await _json('GET', '/api/v1/capabilities', auth: true);
    final list = j['capabilities'];
    return list is List ? list : [];
  }

  Future<List<dynamic>> listJobs({int limit = 40}) async {
    final j = await _json('GET', '/api/v1/jobs', auth: true, query: {'limit': '$limit'});
    final list = j['jobs'];
    return list is List ? list : [];
  }

  Future<Map<String, dynamic>> createJob({
    required String capability,
    required String pluginId,
    Map<String, dynamic>? input,
    bool needUpload = false,
    int timeoutSeconds = 600,
    String conversationId = '',
    String personaId = '',
    String clientMsgId = '',
  }) {
    final inp = Map<String, dynamic>.from(input ?? {});
    if (clientMsgId.isNotEmpty) inp['_client_msg_id'] = clientMsgId;
    return _json(
      'POST',
      '/api/v1/jobs',
      auth: true,
      timeout: const Duration(seconds: 60),
      body: {
        'capability': capability,
        'plugin_id': pluginId,
        'input': inp,
        'need_upload': needUpload,
        'timeout_seconds': timeoutSeconds,
        if (conversationId.isNotEmpty) 'conversation_id': conversationId,
        if (personaId.isNotEmpty) 'persona_id': personaId,
      },
    );
  }

  Future<Map<String, dynamic>> getJob(String jobId) {
    return _json('GET', '/api/v1/jobs/$jobId', auth: true);
  }

  Future<Map<String, dynamic>> createShare({
    required String fileId,
    int expiresHours = 24,
    int maxDownloads = 20,
  }) {
    return _json('POST', '/api/v1/shares', auth: true, body: {
      'file_id': fileId,
      'expires_hours': expiresHours,
      'max_downloads': maxDownloads,
    });
  }

  Future<Map<String, dynamic>> addMemory(String personaId, String text) {
    return _json('POST', '/api/v1/personas/$personaId/memories', auth: true, body: {'text': text});
  }

  Future<Map<String, dynamic>> updateMemory(String personaId, String memoryId, String text) {
    return _json('PATCH', '/api/v1/personas/$personaId/memories/$memoryId', auth: true, body: {'text': text});
  }

  Future<void> deleteMemory(String personaId, String memoryId) async {
    await _json('DELETE', '/api/v1/personas/$personaId/memories/$memoryId', auth: true);
  }

  Future<Map<String, dynamic>> companionBoard(String personaId) {
    return _json('GET', '/api/v1/companion/personas/$personaId/board', auth: true);
  }

  Future<Map<String, dynamic>> companionSnapshot(String personaId, {String query = ''}) {
    return _json(
      'GET',
      '/api/v1/companion/personas/$personaId/snapshot',
      auth: true,
      query: {if (query.isNotEmpty) 'query': query},
    );
  }

  Future<Map<String, dynamic>> companionPolicy(String personaId) {
    return _json('GET', '/api/v1/companion/personas/$personaId/policy', auth: true);
  }

  Future<Map<String, dynamic>> putCompanionPolicy(
    String personaId, {
    bool? pauseWrite,
    bool? pauseRead,
  }) {
    return _json(
      'PUT',
      '/api/v1/companion/personas/$personaId/policy',
      auth: true,
      body: {
        if (pauseWrite != null) 'pause_memory_write': pauseWrite,
        if (pauseRead != null) 'pause_memory_read': pauseRead,
      },
    );
  }

  Future<Map<String, dynamic>> listMoments({int limit = 30, String before = ''}) {
    return _json(
      'GET',
      '/api/v1/moments',
      auth: true,
      query: {
        'limit': '$limit',
        if (before.isNotEmpty) 'before': before,
      },
    );
  }

  Future<Map<String, dynamic>> createMoment({required String text, List<String> mediaIds = const []}) {
    return _json('POST', '/api/v1/moments', auth: true, body: {
      'text': text,
      'media_ids': mediaIds,
    });
  }

  Future<Map<String, dynamic>> toggleMomentLike(String momentId) {
    return _json('POST', '/api/v1/moments/$momentId/like', auth: true);
  }

  Future<Map<String, dynamic>> addMomentComment(String momentId, {required String text, String? replyTo}) {
    return _json('POST', '/api/v1/moments/$momentId/comments', auth: true, body: {
      'text': text,
      if (replyTo != null) 'reply_to': replyTo,
    });
  }

  Future<void> deleteMoment(String momentId) async {
    await _json('DELETE', '/api/v1/moments/$momentId', auth: true);
  }

  Future<List<dynamic>> listVirtualDevices() async {
    final j = await _json('GET', '/api/v1/virtual-devices', auth: true);
    return (j['devices'] as List?) ?? (j['items'] as List?) ?? const [];
  }

  Future<Map<String, dynamic>> createVirtualDevice({
    String label = '桌面',
    String kind = 'desktop',
    String personaId = '',
  }) {
    return _json('POST', '/api/v1/virtual-devices', auth: true, body: {
      'label': label,
      'kind': kind,
      if (personaId.isNotEmpty) 'persona_id': personaId,
    });
  }

  Future<Map<String, dynamic>> approveDeviceCreateRequest(String requestId) {
    return _json('POST', '/api/v1/virtual-devices/requests/$requestId/approve', auth: true);
  }

  Future<Map<String, dynamic>> denyDeviceCreateRequest(String requestId) {
    return _json('POST', '/api/v1/virtual-devices/requests/$requestId/deny', auth: true);
  }

  Future<Map<String, dynamic>> startVirtualDevice(String deviceId) {
    return _json('POST', '/api/v1/virtual-devices/$deviceId/start', auth: true);
  }

  Future<Map<String, dynamic>> stopVirtualDevice(String deviceId, {bool force = true}) {
    return _json('POST', '/api/v1/virtual-devices/$deviceId/stop', auth: true, query: {'force': force ? '1' : '0'});
  }

  Future<Map<String, dynamic>> acquireDeviceLease(String deviceId, {String role = 'view', int ttlSec = 120}) {
    return _json('POST', '/api/v1/virtual-devices/$deviceId/leases', auth: true, body: {
      'role': role,
      'ttl_sec': ttlSec,
    });
  }

  Future<Map<String, dynamic>> heartbeatDeviceLease(String deviceId, String leaseId, String token) {
    return _json(
      'POST',
      '/api/v1/virtual-devices/$deviceId/leases/$leaseId/heartbeat',
      auth: true,
      body: {'token': token},
    );
  }

  Future<void> releaseDeviceLease(String deviceId, String leaseId, String token) async {
    await _json(
      'POST',
      '/api/v1/virtual-devices/$deviceId/leases/$leaseId/release',
      auth: true,
      body: {'token': token},
    );
  }

  /// 设备画面截图（应用内预览轮询）。返回 PNG bytes。
  Future<Uint8List> virtualDeviceScreenshot(String deviceId) async {
    final j = await _json('POST', '/api/v1/virtual-devices/$deviceId/actions/screenshot', auth: true);
    final b64 = j['png_base64'] as String? ?? '';
    if (b64.isEmpty) {
      throw ApiException('截图为空');
    }
    return base64Decode(b64);
  }

  Future<List<dynamic>> listStickers({String packId = '', String emotion = ''}) async {
    final j = await _json('GET', '/api/v1/stickers', auth: true, query: {
      if (packId.isNotEmpty) 'pack_id': packId,
      if (emotion.isNotEmpty) 'emotion': emotion,
    });
    return (j['stickers'] as List?) ?? (j['items'] as List?) ?? const [];
  }

  Future<List<dynamic>> listStickerPacks({String scope = ''}) async {
    final j = await _json('GET', '/api/v1/stickers/packs', auth: true, query: {
      if (scope.isNotEmpty) 'scope': scope,
    });
    return (j['packs'] as List?) ?? const [];
  }

  Future<Map<String, dynamic>> getStickerPack(String packId) {
    return _json('GET', '/api/v1/stickers/packs/$packId', auth: true);
  }

  Future<Map<String, dynamic>> createStickerPack({
    required String title,
    String tags = '',
  }) {
    return _json('POST', '/api/v1/stickers/packs', auth: true, body: {
      'title': title,
      'tags': tags,
      'scope': 'account',
    });
  }

  Future<Map<String, dynamic>> appendPackItem({
    required String packId,
    required String fileId,
    String label = '',
    String emotion = '',
  }) {
    return _json('POST', '/api/v1/stickers/packs/$packId/items', auth: true, body: {
      'items': [
        {'file_id': fileId, 'label': label, 'emotion': emotion},
      ],
      'notify': false,
    });
  }

  Future<Map<String, dynamic>> subscribeStickerPack(String packId, {bool pinned = false}) {
    return _json('POST', '/api/v1/stickers/packs/$packId/subscribe', auth: true, body: {
      'pinned': pinned,
    });
  }

  Future<List<dynamic>> suggestStickers({required String q, int limit = 12}) async {
    final j = await _json('GET', '/api/v1/stickers/suggest', auth: true, query: {
      'q': q,
      'limit': '$limit',
    });
    return (j['items'] as List?) ?? const [];
  }

  Future<Map<String, dynamic>> collectSticker({
    required String fileId,
    String label = '',
    String emotion = '',
    String ownerKind = 'user',
    String ownerId = '',
    String packId = '',
  }) {
    return _json('POST', '/api/v1/stickers/collect', auth: true, body: {
      'file_id': fileId,
      'label': label,
      'emotion': emotion,
      'owner_kind': ownerKind,
      'owner_id': ownerId,
      if (packId.isNotEmpty) 'pack_id': packId,
    });
  }

  Future<List<dynamic>> listPersonaAssets(String personaId, {String kind = ''}) async {
    final j = await _json(
      'GET',
      '/api/v1/personas/$personaId/assets',
      auth: true,
      query: {if (kind.isNotEmpty) 'kind': kind},
    );
    return j['items'] as List? ?? const [];
  }

  Future<List<dynamic>> drawPersonaVoice(
    String personaId, {
    required String prompt,
    int count = 3,
    String sampleText = '',
    String batchId = '',
    int slotIndex = 0,
    bool refPersona = true,
  }) async {
    final j = await _json(
      'POST',
      '/api/v1/personas/$personaId/assets/draw/voice',
      auth: true,
      timeout: const Duration(minutes: 8),
      body: {
        'prompt': prompt,
        'count': count,
        'sample_text': sampleText,
        if (batchId.isNotEmpty) 'batch_id': batchId,
        'slot_index': slotIndex,
        'ref_persona': refPersona,
      },
    );
    return j['items'] as List? ?? const [];
  }

  Future<List<dynamic>> drawPersonaImage(
    String personaId, {
    required String prompt,
    int count = 3,
    String batchId = '',
    int slotIndex = 0,
    bool refPersona = true,
  }) async {
    final j = await _json(
      'POST',
      '/api/v1/personas/$personaId/assets/draw/image',
      auth: true,
      timeout: const Duration(minutes: 8),
      body: {
        'prompt': prompt,
        'count': count,
        if (batchId.isNotEmpty) 'batch_id': batchId,
        'slot_index': slotIndex,
        'ref_persona': refPersona,
      },
    );
    return j['items'] as List? ?? const [];
  }

  Future<Map<String, dynamic>> fixPersonaAsset(String personaId, String assetId) {
    return _json('POST', '/api/v1/personas/$personaId/assets/$assetId/fix', auth: true);
  }

  Future<void> deletePersonaAsset(String personaId, String assetId) async {
    await _json('DELETE', '/api/v1/personas/$personaId/assets/$assetId', auth: true);
  }

  String wsUrl(String token) {
    final u = Uri.parse(baseUrl);
    final scheme = u.scheme == 'https' ? 'wss' : 'ws';
    return Uri(
      scheme: scheme,
      host: u.host,
      port: u.hasPort ? u.port : null,
      path: '${u.path}/api/v1/ws'.replaceAll('//', '/'),
      queryParameters: {'token': token},
    ).toString();
  }

  /// 曲目鉴权流地址。iOS 播放器对自定义 Header 不稳，附带 access_token 查询参数。
  String musicTrackAudioUrl(String trackId) {
    final t = accessToken;
    final q = (t == null || t.isEmpty) ? null : {'access_token': t};
    return _u('/api/v1/music/tracks/$trackId/audio', q).toString();
  }

  Map<String, String> authHeaders() {
    final t = accessToken;
    if (t == null || t.isEmpty) return const {};
    return {'Authorization': 'Bearer $t'};
  }

  Future<Map<String, dynamic>> listMusicTracks({
    String q = '',
    int limit = 200,
    int offset = 0,
  }) {
    return _json('GET', '/api/v1/music/tracks', auth: true, query: {
      'q': q,
      'limit': '$limit',
      'offset': '$offset',
    });
  }

  Future<Map<String, dynamic>> placeReverse({required double lat, required double lng}) {
    return _json('GET', '/api/v1/place/reverse', auth: true, query: {
      'lat': '$lat',
      'lng': '$lng',
    });
  }

  Future<Map<String, dynamic>> placeNearby({
    required double lat,
    required double lng,
    String keyword = '',
    int radius = 2000,
    int pageSize = 15,
  }) {
    return _json('GET', '/api/v1/place/nearby', auth: true, query: {
      'lat': '$lat',
      'lng': '$lng',
      if (keyword.isNotEmpty) 'keyword': keyword,
      'radius': '$radius',
      'page_size': '$pageSize',
    });
  }

  Future<Map<String, dynamic>> inviteListen({
    required String convId,
    String mood = '',
    String reason = '',
    String context = '',
    int limit = 12,
    List<String>? trackIds,
  }) {
    return _json('POST', '/api/v1/listen/invite', auth: true, body: {
      'conv_id': convId,
      'mood': mood,
      'reason': reason,
      'context': context,
      'limit': limit,
      if (trackIds != null && trackIds.isNotEmpty) 'track_ids': trackIds,
    });
  }

  Future<List<dynamic>> listListenSessions() => getJsonList('/api/v1/listen/sessions');

  Future<Map<String, dynamic>> getListenSession(String sessionId) {
    return _json('GET', '/api/v1/listen/sessions/$sessionId', auth: true);
  }

  Future<Map<String, dynamic>> acceptListenSession(String sessionId) {
    return _json('POST', '/api/v1/listen/sessions/$sessionId/accept', auth: true);
  }

  Future<Map<String, dynamic>> patchListenSession(
    String sessionId, {
    int? trackIndex,
    int? positionMs,
    String? status,
  }) {
    return _json('PATCH', '/api/v1/listen/sessions/$sessionId', auth: true, body: {
      if (trackIndex != null) 'track_index': trackIndex,
      if (positionMs != null) 'position_ms': positionMs,
      if (status != null) 'status': status,
    });
  }

  Future<Map<String, dynamic>> createCall({
    required String convId,
    String media = 'audio',
  }) {
    return _json('POST', '/api/v1/calls', auth: true, body: {
      'conv_id': convId,
      'media': media,
    });
  }

  Future<Map<String, dynamic>> signalCall(
    String callId, {
    required String type,
    Map<String, dynamic> payload = const {},
  }) {
    return _json('POST', '/api/v1/calls/$callId/signal', auth: true, body: {
      'type': type,
      'payload': payload,
    });
  }

  Future<Map<String, dynamic>> voiceRealtimeReady({String convId = ''}) {
    return _json(
      'GET',
      '/api/v1/voice/realtime/ready',
      auth: true,
      query: {if (convId.isNotEmpty) 'conv_id': convId},
    );
  }

  Future<Map<String, dynamic>> voiceRealtimeWarm({String convId = ''}) {
    return _json(
      'POST',
      '/api/v1/voice/realtime/warm',
      auth: true,
      query: {if (convId.isNotEmpty) 'conv_id': convId},
    );
  }

  /// 点击语音通话：服务端插入微信式通话气泡（AI 默认已接）。
  Future<Map<String, dynamic>> voiceRealtimeStart({required String convId}) {
    return _json(
      'POST',
      '/api/v1/voice/realtime/start',
      auth: true,
      body: {'conv_id': convId},
    );
  }

  /// AI 接通短问候（固定音色），播完后再开麦。
  Future<Map<String, dynamic>> voiceRealtimeGreeting({required String convId}) {
    return _json(
      'POST',
      '/api/v1/voice/realtime/greeting',
      auth: true,
      body: {'conv_id': convId},
    );
  }

  /// 插话打断：停服务端本轮 LLM/TTS。
  Future<Map<String, dynamic>> voiceRealtimeCancel({
    required String convId,
    String turnId = '',
  }) {
    return _json(
      'POST',
      '/api/v1/voice/realtime/cancel',
      auth: true,
      body: {
        'conv_id': convId,
        if (turnId.isNotEmpty) 'turn_id': turnId,
      },
    );
  }

  Future<Map<String, dynamic>> voiceRealtimeHangup({
    required String convId,
    int? durationSec,
    String callId = '',
  }) {
    return _json(
      'POST',
      '/api/v1/voice/realtime/hangup',
      auth: true,
      body: {
        'conv_id': convId,
        if (durationSec != null) 'duration_sec': durationSec,
        if (callId.isNotEmpty) 'call_id': callId,
      },
    );
  }

  Future<Map<String, dynamic>> voiceRealtimeTurn({
    required String convId,
    required Uint8List bytes,
    String filename = 'voice.wav',
    String contentType = 'audio/wav',
    String prefixText = '',
    bool allowAuthRetry = true,
  }) async {
    if (accessToken == null || accessToken!.isEmpty) {
      throw ApiException('未登录', statusCode: 401, authNoise: true);
    }
    final uri = _u('/api/v1/voice/realtime/turn');
    final req = MultipartRequest('POST', uri);
    req.headers['Authorization'] = 'Bearer $accessToken';
    req.headers['Accept'] = 'application/json';
    req.fields['conv_id'] = convId;
    if (prefixText.trim().isNotEmpty) {
      req.fields['prefix_text'] = prefixText.trim();
    }
    MediaType? mt;
    try {
      mt = MediaType.parse(contentType);
    } catch (_) {
      mt = null;
    }
    req.files.add(MultipartFile.fromBytes('file', bytes, filename: filename, contentType: mt));
    try {
      final streamed = await req.send().timeout(const Duration(seconds: 60));
      final res = await http.Response.fromStream(streamed);
      Map<String, dynamic>? data;
      if (res.body.isNotEmpty) {
        try {
          final decoded = jsonDecode(res.body);
          if (decoded is Map<String, dynamic>) data = decoded;
        } catch (_) {}
      }
      if (res.statusCode == 401 && allowAuthRetry && (refreshToken ?? '').isNotEmpty) {
        if (await trySilentRefresh()) {
          return await voiceRealtimeTurn(
            convId: convId,
            bytes: bytes,
            filename: filename,
            contentType: contentType,
            prefixText: prefixText,
            allowAuthRetry: false,
          );
        }
      }
      if (res.statusCode >= 400) {
        final detail = data?['detail'];
        throw ApiException(
          detail is String ? detail : '语音回合失败（${res.statusCode}）',
          statusCode: res.statusCode,
        );
      }
      return data ?? {};
    } on ApiException {
      rethrow;
    } catch (e) {
      throw ApiException(_netFailMessage(e, action: '语音对话'));
    }
  }

  Future<Uint8List> downloadMediaBytes(String fileId, {bool allowAuthRetry = true}) async {
    if (accessToken == null || accessToken!.isEmpty) {
      throw ApiException('未登录', statusCode: 401, authNoise: true);
    }
    final uri = _u('/api/v1/media/files/$fileId');
    final res = await _http.get(uri, headers: {
      'Authorization': 'Bearer $accessToken',
      'Accept': '*/*',
    }).timeout(const Duration(seconds: 60));
    if (res.statusCode == 401 && allowAuthRetry && (refreshToken ?? '').isNotEmpty) {
      if (await trySilentRefresh()) {
        return downloadMediaBytes(fileId, allowAuthRetry: false);
      }
    }
    if (res.statusCode >= 400 || res.bodyBytes.isEmpty) {
      throw ApiException('音频下载失败（${res.statusCode}）', statusCode: res.statusCode);
    }
    return res.bodyBytes;
  }

  void close() => _http.close();
}
